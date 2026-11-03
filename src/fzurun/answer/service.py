"""作答。

两种模式：
  retrieval_only  没配 LLM 时，直接把命中的条目原文交出去，附引用。这不是降级，
                  政策类问题本来就该看原文。
  llm             配了 LLM 时，让它只做「挑段落 + 按原话摘句」，不写新事实。
                  生成后强制过一次数字校验。
"""

from __future__ import annotations

import re
from typing import Any

from ..config import Settings
from ..kb.loader import Entry
from ..kb.retrieve import Hit, Retrieval
from ..providers.openai_compat import ChatClient

NUM_RE = re.compile(r"\d+(?:\.\d+)?")

TIER_NOTE = {
    "experience": "以下为非官方经验值，仅供参照。",
    "tool": "",
    "policy": "",
}


def _clean(text: str) -> str:
    t = text.strip()
    t = t.removeprefix("A：")
    return t.strip()


def _cite(hit: Hit) -> dict[str, Any]:
    e: Entry = hit.entry
    return {
        "id": e.id,
        "label": e.label,
        "section": e.section,
        "part": e.part,
        "tier": e.tier,
        "anchor": e.anchor,
        "score": hit.score,
        "coverage": hit.coverage,
    }


def verify_numbers(answer: str, sources: list[str]) -> list[str]:
    """答案里出现的每个数字，必须能在来源原文里找到。返回找不到的那些。"""
    pool = "\n".join(sources)
    allowed = set(NUM_RE.findall(pool))
    return [n for n in NUM_RE.findall(answer) if n not in allowed]


def answer(query: str, retrieval: Retrieval, settings: Settings,
           client: ChatClient) -> dict[str, Any]:
    tier_note = ""
    if retrieval.top:
        tier_note = TIER_NOTE.get(retrieval.top.entry.tier, "")

    if retrieval.gate == "irrelevant" or not retrieval.hits:
        nav = [{"id": h.entry.id, "label": h.entry.label} for h in retrieval.hits[:3]]
        return {
            "answer": "文档里没有找到和这个问题相关的内容。",
            "mode": "retrieval_only",
            "gate": retrieval.gate,
            "citations": [],
            "navigation": nav,
            "caveats": [],
            "note": "",
        }

    top = retrieval.top
    assert top is not None
    sources = [_clean(h.entry.content) for h in retrieval.hits[:3]]

    if not client.enabled:
        return {
            "answer": _clean(top.entry.content),
            "mode": "retrieval_only",
            "gate": retrieval.gate,
            "citations": [_cite(h) for h in retrieval.hits[:3]],
            "navigation": [],
            "caveats": top.entry.caveats,
            "note": tier_note,
        }

    blocks = "\n\n".join(f"[{i + 1}] {s}" for i, s in enumerate(sources))
    messages = [
        {
            "role": "system",
            "content": (
                "你在帮福州大学的学生查转专业资料。只允许用下面的资料原话作答，"
                "不得补充资料之外的事实，不得改写任何数字、比例、日期。"
                "如果资料回答不了这个问题，cited 输出空数组。"
                '输出 JSON：{"cited": [序号], "answer": "回答"}'
            ),
        },
        {"role": "user", "content": f"问题：{query}\n\n资料：\n{blocks}"},
    ]
    parsed = client.complete_json(messages) or {}
    text = (parsed.get("answer") or "").strip()
    cited = [i for i in parsed.get("cited") or [] if isinstance(i, int) and 1 <= i <= len(sources)]

    if not text:
        return {
            "answer": "资料里没有能直接回答这个问题的内容。",
            "mode": "llm",
            "gate": retrieval.gate,
            "citations": [],
            "navigation": [{"id": h.entry.id, "label": h.entry.label} for h in retrieval.hits[:3]],
            "caveats": top.entry.caveats,
            "note": tier_note,
        }

    bad = verify_numbers(text, [sources[i - 1] for i in cited] or sources)
    if bad:
        text = "（模型输出里的数字与原文对不上，已退回原文）\n\n" + _clean(top.entry.content)
        cited = []

    used = [retrieval.hits[i - 1] for i in cited] or retrieval.hits[:1]
    return {
        "answer": text,
        "mode": "llm",
        "gate": retrieval.gate,
        "citations": [_cite(h) for h in used],
        "navigation": [],
        "caveats": top.entry.caveats,
        "note": tier_note,
    }
