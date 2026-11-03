from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Entry:
    id: str
    kind: str
    part: str
    section: str
    question: str = ""
    title: str = ""
    content: str = ""
    tier: str = "policy"
    status: str = "active"
    caveats: list[str] = field(default_factory=list)
    alt_questions: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    grade_scope: list[str] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    anchor: str = ""
    source_quote: str | None = None

    @property
    def label(self) -> str:
        return self.question or self.title or self.id

    @property
    def index_text(self) -> str:
        """进倒排的文本。

        问题重复 5 次、同义问法重复 3 次，正文 1 次——这是最简单的字段加权：
        tf 被抬高的词更容易赢。不这么做「面试要注意什么」会被「挂科」那条抢走，
        因为「注意」在整个语料里比「面试」稀有，idf 更高。
        """
        parts = [" ".join([self.question] * 5),
                 " ".join(self.alt_questions * 3),
                 self.title,
                 self.content]
        return "\n".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "part": self.part,
            "section": self.section,
            "question": self.question,
            "title": self.title,
            "label": self.label,
            "tier": self.tier,
            "status": self.status,
            "caveats": self.caveats,
            "related": self.related,
            "tags": self.tags,
            "grade_scope": self.grade_scope,
            "links": self.links,
            "assets": self.assets,
            "anchor": self.anchor,
            "content": self.content,
        }


def load_entries(path: Path) -> list[Entry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[Entry] = []
    for item in raw:
        if item.get("status") == "removed":
            continue
        out.append(
            Entry(
                id=item["id"],
                kind=item.get("kind", "qa"),
                part=item.get("part", ""),
                section=item.get("section", ""),
                question=item.get("question", "") or "",
                title=item.get("title", "") or "",
                content=item.get("content", "") or "",
                tier=item.get("tier", "policy"),
                status=item.get("status", "active"),
                caveats=item.get("caveats") or [],
                alt_questions=item.get("alt_questions") or [],
                related=item.get("related") or [],
                tags=item.get("tags") or [],
                grade_scope=item.get("grade_scope") or [],
                links=item.get("links") or [],
                assets=item.get("assets") or [],
                anchor=item.get("anchor", "") or "",
                source_quote=item.get("source_quote"),
            )
        )
    return out
