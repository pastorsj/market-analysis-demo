"""Pure, fact-preserving answer layout and Markdown compilation."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

PRESENTED_ANSWER_MAX = 3200
PRESENTATION_LONG_ANSWER_CHARS = 700
PRESENTATION_STRUCTURED_ANSWER_CHARS = 320
PRESENTATION_STRUCTURED_BLOCKS = 4
_LEADING_MARKER = re.compile(r"^(?:#{1,6}\s+|[-*+]\s+|\d{1,2}[.)]\s+)")
_RAW_CITATION = re.compile(r"\bcit-[a-f0-9]{12,64}\b", re.I)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s+|\d{1,2}[.)]\s+)", re.M)
_SECTION_CUE = re.compile(
    r"^\s*(?:bottom line|key findings?|measured comparison|similarity ranking|"
    r"ranking method|how similarity was measured|evidence|limits?|"
    r"where comparisons? break down|what remains uncertain|uncertainty)\s*:",
    re.I | re.M,
)

Heading = Literal[
    "Bottom line",
    "Key findings",
    "Measured comparison",
    "Similarity ranking",
    "How similarity was measured",
    "Evidence",
    "Limits of the comparison",
    "What remains uncertain",
]
Layout = Literal["paragraphs", "ordered_list", "unordered_list"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PresentationStyle(_StrictModel):
    heading: Heading | None
    layout: Layout


class PresentationPlan(_StrictModel):
    styles: Annotated[tuple[PresentationStyle, ...], Field(min_length=1, max_length=12)]


@lru_cache(maxsize=12)
def presentation_plan_schema(block_count: int) -> type[PresentationPlan]:
    """Return a strict provider schema with one cosmetic style per source position."""
    if not 1 <= block_count <= 12:
        raise ValueError("presentation block count is outside the schema contract")
    styles = Annotated[
        tuple[PresentationStyle, ...],
        Field(min_length=block_count, max_length=block_count),
    ]
    return create_model(
        f"PresentationPlan{block_count:02d}",
        __base__=PresentationPlan,
        styles=(styles, ...),
    )


@dataclass(frozen=True, slots=True)
class SourceBlock:
    block_id: str
    text: str


@dataclass(frozen=True, slots=True)
class PresentationEligibility:
    eligible: bool
    reason: Literal["long_answer", "structured_answer", "compact_answer"]
    character_count: int
    block_count: int
    list_item_count: int
    section_cue_count: int


def presentation_eligibility(answer: str) -> PresentationEligibility:
    """Explain whether answer structure warrants the additional formatting call."""
    normalized = answer.strip()
    blocks = tuple(item for item in re.split(r"\n\s*\n", normalized) if item.strip())
    character_count = len(normalized)
    list_item_count = len(_LIST_ITEM.findall(normalized))
    section_cue_count = len(_SECTION_CUE.findall(normalized))
    long_answer = character_count >= PRESENTATION_LONG_ANSWER_CHARS
    structured_answer = character_count >= PRESENTATION_STRUCTURED_ANSWER_CHARS and (
        len(blocks) >= PRESENTATION_STRUCTURED_BLOCKS or list_item_count >= 2 or section_cue_count >= 2
    )
    reason: Literal["long_answer", "structured_answer", "compact_answer"]
    if long_answer:
        reason = "long_answer"
    elif structured_answer:
        reason = "structured_answer"
    else:
        reason = "compact_answer"
    return PresentationEligibility(
        eligible=long_answer or structured_answer,
        reason=reason,
        character_count=character_count,
        block_count=len(blocks),
        list_item_count=list_item_count,
        section_cue_count=section_cue_count,
    )


def source_blocks(answer: str) -> tuple[SourceBlock, ...]:
    """Turn prose into immutable semantic blocks, removing presentation markers only."""
    paragraphs = re.split(r"\n\s*\n", answer.strip())
    cleaned: list[str] = []
    for paragraph in paragraphs:
        text = " ".join(line.strip() for line in paragraph.splitlines() if line.strip())
        text = _LEADING_MARKER.sub("", text, count=1).strip()
        text = text.replace("**", "")
        if text:
            cleaned.append(text)
    if not cleaned or len(cleaned) > 12 or any(_RAW_CITATION.search(item) for item in cleaned):
        raise ValueError("presentation source blocks are invalid")
    return tuple(SourceBlock(f"block-{index:02d}", text) for index, text in enumerate(cleaned, 1))


def compile_markdown(blocks: Sequence[SourceBlock], plan: PresentationPlan) -> str:
    """Attach cosmetic styles by position; the model never controls source membership."""
    if not blocks or len(blocks) != len(plan.styles):
        raise ValueError("presentation plan must have one style per source block")
    groups: list[tuple[PresentationStyle, list[SourceBlock]]] = []
    for block, style in zip(blocks, plan.styles, strict=True):
        if not groups or style.heading is not None or groups[-1][0].layout != style.layout:
            groups.append((style, [block]))
        else:
            groups[-1][1].append(block)

    rendered_sections: list[str] = []
    for style, members in groups:
        rows = [item.text for item in members]
        if style.layout == "paragraphs":
            body = "\n\n".join(rows)
        elif style.layout == "ordered_list":
            body = "\n".join(f"{index}. {row}" for index, row in enumerate(rows, 1))
        else:
            body = "\n".join(f"- {row}" for row in rows)
        if style.heading is None:
            rendered_sections.append(body)
        else:
            rendered_sections.append(f"## {style.heading}\n\n{body}")
    markdown = "\n\n".join(rendered_sections)
    if not markdown or len(markdown) > PRESENTED_ANSWER_MAX or _RAW_CITATION.search(markdown):
        raise ValueError("compiled presentation is outside the answer contract")
    return markdown


def presentation_packet(question: str, blocks: Sequence[SourceBlock]) -> str:
    return json.dumps(
        {
            "question": question,
            "immutable_blocks": [
                {"position": index, "text": item.text} for index, item in enumerate(blocks, 1)
            ],
            "contract": {
                "one_style_per_position": len(blocks),
                "compiler_owns_text_order_and_membership": True,
                "style_order_matches_block_positions": True,
                "heading_null_continues_compatible_layout": True,
                "allowed_markdown": ["##", "1. ", "- "],
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
