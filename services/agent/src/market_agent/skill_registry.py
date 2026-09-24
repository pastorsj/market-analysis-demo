from __future__ import annotations
import hashlib
import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from .config import TOOLS


SKILL_NAMES = (
    "market-dislocation",
    "peer-comparison",
    "historical-analogues",
    "shock-propagation",
    "volatility-risk",
    "narrative-map",
    "market-research-guide",
    "report-presentation",
)
REQUIRED_TOOLS = {
    "market-dislocation": ("get_price_context", "detect_market_shock"),
    "peer-comparison": ("get_price_context", "detect_market_shock"),
    "historical-analogues": ("get_price_context", "detect_market_shock", "find_historical_analogues"),
    "shock-propagation": (
        "get_price_context",
        "detect_market_shock",
        "search_news",
        "trace_shock_propagation",
    ),
    "volatility-risk": ("get_price_context", "detect_market_shock", "predict_volatility_risk"),
    "narrative-map": ("search_news", "project_news_topics"),
    "market-research-guide": (),
    "report-presentation": (),
}
_FRONTMATTER = re.compile(
    r'\A---\nname: (?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)\ndescription: (?P<description>[^\n]+)\nmetadata:\n  version: "(?P<version>\d+\.\d+\.\d+)"\nallowed-tools:(?P<tools> \[\]|(?:\n  - [a-z][a-z0-9_]+)+)\n---\n\n(?P<body>#[^\x00]+\n)\Z'
)


class SkillRegistryError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    return None if condition else (_ for _ in ()).throw(SkillRegistryError(message))


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    version: str
    description: str
    allowed_tools: tuple[str, ...]
    required_tools: tuple[str, ...]
    content_sha256: str
    prompt_text: str

    @property
    def skill_id(self) -> str:
        return f"{self.name}/{self.version}"

    def metadata(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "skill_id": self.skill_id,
                "name": self.name,
                "version": self.version,
                "description": self.description,
                "allowed_tools": self.allowed_tools,
                "required_tools": self.required_tools,
                "content_sha256": self.content_sha256,
            }
        )


@dataclass(frozen=True)
class SkillRegistry:
    skills: Mapping[str, SkillDefinition]
    content_sha256: str

    def __iter__(self) -> Iterator[str]:
        return iter(self.skills)

    def __len__(self) -> int:
        return len(self.skills)

    def __getitem__(self, name: str) -> SkillDefinition:
        return (
            self.skills[name]
            if name in self.skills
            else (_ for _ in ()).throw(SkillRegistryError(f"unknown skill: {name}"))
        )

    def render_prompt(self, names: Iterable[str]) -> str:
        selected = tuple(names)
        _require(
            bool(selected) and len(selected) == len(set(selected)),
            "skill selection must be nonempty and unique",
        )
        return "\n\n".join(
            f"[Skill {skill.skill_id}; sha256={skill.content_sha256}]\n{skill.prompt_text}"
            for skill in (self[name] for name in selected)
        )


def load_skill(path: Path, *, allowed_tools: Iterable[str] = TOOLS) -> SkillDefinition:
    path = Path(path)
    _require(
        path.name == "SKILL.md" and path.is_file() and not path.is_symlink(),
        "skill must be a regular SKILL.md file",
    )
    raw = path.read_bytes()
    _require(0 < len(raw) <= 16_384, "skill size is outside the bound")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillRegistryError("skill must be UTF-8") from exc
    match = _FRONTMATTER.fullmatch(text)
    _require(match is not None, "skill frontmatter is not canonical")
    values = match.groupdict()
    tools = tuple(re.findall(r"  - ([a-z][a-z0-9_]+)", values["tools"]))
    _require(path.parent.name == values["name"], "skill name and directory disagree")
    _require(
        20 <= len(values["description"]) <= 500 and not {"<", ">"} & set(values["description"]),
        "skill description is invalid",
    )
    required = REQUIRED_TOOLS[values["name"]]
    _require(
        len(tools) == len(set(tools)) and set(tools) <= frozenset(allowed_tools),
        "skill tools are duplicated or unapproved",
    )
    _require(set(required) <= set(tools), "required skill tools are not allowed")
    return SkillDefinition(
        values["name"],
        values["version"],
        values["description"],
        tools,
        required,
        hashlib.sha256(raw).hexdigest(),
        values["body"].strip(),
    )


def load_skill_registry(root: Path, *, allowed_tools: Iterable[str] = TOOLS) -> SkillRegistry:
    root = Path(root)
    _require(root.is_dir() and not root.is_symlink(), "skill root must be a regular directory")
    children = {item.name: item for item in root.iterdir() if not item.name.startswith(".")}
    expected, found = set(SKILL_NAMES), set(children)
    _require(found == expected, "skill catalog membership is invalid")
    _require(
        all(item.is_dir() and not item.is_symlink() for item in children.values()),
        "skill entries must be regular directories",
    )
    parsed = {
        name: load_skill(children[name] / "SKILL.md", allowed_tools=allowed_tools)
        for name in sorted(children)
    }
    selected = MappingProxyType({name: parsed[name] for name in SKILL_NAMES})
    material = [dict(selected[name].metadata()) for name in SKILL_NAMES]
    return SkillRegistry(
        selected,
        hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    )
