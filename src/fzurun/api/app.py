from __future__ import annotations

import json
import time

from blacksheep import Application, Request, get, post
from blacksheep import json as json_response
from blacksheep.settings.json import json_settings

from ..answer.service import answer as build_answer
from ..config import settings
from ..kb.loader import load_entries
from ..kb.retrieve import Retriever
from ..kb.vector import EmbeddingClient, VectorIndex
from ..obs.log import REASONS, Observation, new_request_id, scrub
from ..providers.openai_compat import ChatClient

# BlackSheep 默认把非 ASCII 转成 \uXXXX。对中文接口来说既难读又让体积翻倍，
# 用官方提供的 json_settings 换掉序列化器（不是 monkey patch）。
json_settings.use(
    loads=json.loads,
    dumps=lambda obj: json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
    pretty_dumps=lambda obj: json.dumps(obj, ensure_ascii=False, indent=2),
)

app = Application()

_entries = load_entries(settings.kb_path)
_client = ChatClient(settings)
_embedder = EmbeddingClient(settings)

# 配了 embedding 就走双通道，没配就纯 BM25。建向量失败不阻断启动。
_vector: VectorIndex | None = None
if settings.embed_enabled:
    try:
        _vector = VectorIndex.load_or_build(
            settings.embed_cache,
            [e.id for e in _entries],
            [e.index_text for e in _entries],
            _embedder,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 向量索引构建失败，退化为纯 BM25：{exc}")

_retriever = Retriever(_entries, settings, embedder=_embedder, vector=_vector)
_obs = Observation(settings.log_dir, settings.log_enabled, settings.log_query)


@get("/api/health")
def health():
    return json_response(
        {
            "ok": True,
            "entries": len(_entries),
            "channels": _retriever.channels,
            "llm": settings.llm_enabled,
            "model": settings.llm_model or None,
            "logging": settings.log_enabled,
        }
    )


@get("/api/stats")
def stats():
    by_kind: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    for e in _entries:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        by_tier[e.tier] = by_tier.get(e.tier, 0) + 1
    return json_response({"total": len(_entries), "kind": by_kind, "tier": by_tier})


@get("/api/entries/{entry_id}")
def get_entry(entry_id: str):
    e = _retriever.get(entry_id)
    if e is None:
        return json_response({"error": "not found", "id": entry_id}, status=404)
    return json_response(e.to_dict())


@get("/api/search")
def search(q: str, k: int = 5):
    r = _retriever.search(q, top_k=k)
    return json_response(
        {
            "query": r.query,
            "gate": r.gate,
            "hits": [
                {"id": h.entry.id, "label": h.entry.label, "score": h.score,
                 "coverage": h.coverage, "tier": h.entry.tier}
                for h in r.hits
            ],
        }
    )


@post("/api/ask")
async def ask(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        return json_response({"error": "body 必须是 JSON 对象"}, status=400)

    query = (body.get("query") or "").strip()
    if not query:
        return json_response({"error": "query 不能为空"}, status=400)

    k = body.get("top_k")
    started = time.perf_counter()
    r = _retriever.search(query, top_k=k if isinstance(k, int) and k > 0 else None)
    payload = build_answer(query, r, settings, _client)
    latency = round((time.perf_counter() - started) * 1000, 1)

    rid = new_request_id()
    payload["query"] = query
    payload["request_id"] = rid
    payload["latency_ms"] = latency

    _obs.ask(
        {
            "request_id": rid,
            "query": scrub(query),
            "gate": r.gate,
            "mode": payload["mode"],
            "client": str(body.get("client") or "web")[:20],
            "vocab": r.vocab,
            "n_hits": len(r.hits),
            "top": [
                {"id": h.entry.id, "rank": h.rank, "bm25": h.bm25, "cov": h.coverage}
                for h in r.hits[:3]
            ],
            "answer_len": len(payload["answer"]),
            "citations": [c["id"] for c in payload["citations"]],
            "latency_ms": latency,
        }
    )
    return json_response(payload)


@post("/api/feedback")
async def feedback(request: Request):
    """用户说「这条不对」时回来的一枪。

    bot 那边就是把用户的「不对」「没答到」映射成一次 POST。
    不强制 request_id：拿不到也能记，只是归因弱一点。
    """
    body = await request.json()
    if not isinstance(body, dict):
        return json_response({"error": "body 必须是 JSON 对象"}, status=400)

    verdict = body.get("verdict")
    if verdict not in ("ok", "bad"):
        return json_response({"error": "verdict 只能是 ok 或 bad"}, status=400)

    reason = (body.get("reason") or "").strip()
    if reason and reason not in REASONS:
        return json_response({"error": f"reason 只能是 {list(REASONS)} 之一"}, status=400)

    rid = (body.get("request_id") or "").strip()[:16]
    query = _obs.feedback(
        {
            "request_id": rid,
            "verdict": verdict,
            "reason": reason,
            "note": (body.get("note") or "").strip()[:200],
            "query": scrub(str(body.get("query") or "")),
            "client": str(body.get("client") or "web")[:20],
        }
    )
    return json_response({"ok": True, "request_id": rid, "query": query})


app.serve_files(settings.web_dir, root_path="/", index_document="index.html")
