"""Nemotron query embeddings, the prebuilt cuVS document index, and stored vectors."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import EMBED_ID, EMBED_REVISION
from .scenario import Scenario, ScenarioError

DIMENSION = 2048


class SemanticIndex:
    def __init__(self, scenario: Scenario, embed_path: Path, cp: Any):
        import torch
        from cuvs.neighbors import brute_force
        from sentence_transformers import SentenceTransformer

        metadata = scenario.semantic_index
        identity = (metadata["model_id"], metadata["revision"], metadata["dimension"])
        if identity != (EMBED_ID, EMBED_REVISION, DIMENSION) or metadata.get("fixture_only"):
            raise ScenarioError("semantic_index_model_mismatch")
        if not torch.cuda.is_available():
            raise ScenarioError("embedding_cuda_unavailable")
        self.cp, self.brute_force = cp, brute_force
        self.model = SentenceTransformer(
            str(embed_path),
            device="cuda",
            local_files_only=True,
            model_kwargs={"dtype": torch.bfloat16, "attn_implementation": metadata["attention"]},
        )
        self.model.max_seq_length = 4096
        self.index = brute_force.load(str(scenario.root / metadata["index_path"]))
        self.ids: list[str] = scenario.embedding_ids
        self.row = {chunk_id: index for index, chunk_id in enumerate(self.ids)}
        vectors = cp.fromfile(str(scenario.root / "indexes/embeddings.f32"), dtype=cp.float32)
        self.vectors = vectors.reshape(len(self.ids), DIMENSION)

    def search(self, query: str, allowed: set[str], top_k: int) -> list[tuple[str, float]]:
        """Inner-product nearest documents among ``allowed`` chunk IDs, best first."""
        embedded = self.model.encode_query([query], convert_to_tensor=True).float()
        distances, neighbors = self.brute_force.search(
            self.index, self.cp.from_dlpack(embedded), k=len(self.ids)
        )
        indexes = self.cp.asarray(neighbors)[0].tolist()
        ranked = zip(indexes, self.cp.asarray(distances)[0].tolist(), strict=True)
        hits = [(self.ids[index], float(score)) for index, score in ranked if self.ids[index] in allowed]
        return hits[:top_k]

    def document_vectors(self, chunk_ids: list[str]) -> Any:
        """Stored 2048-d document embeddings, in the given order, on the GPU."""
        return self.vectors[self.cp.asarray([self.row[chunk_id] for chunk_id in chunk_ids])]
