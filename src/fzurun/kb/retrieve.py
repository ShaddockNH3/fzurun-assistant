"""检索：双通道 + RRF 融合 + 门控。

通道一 BM25（稀疏，中文二元组）
通道二 向量（稠密，需要配 embedding 服务；不配就只有通道一）

融合用 RRF，只看名次不看分数，省得处理两路量纲不一致——这是从老项目
utils/rrf.py 那套多路融合的思路搬过来的，只是把参数化和去重修好了。

门控在融合之后做，用词表覆盖率和 idf 加权覆盖率两个可解释的量。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from .fuse import rrf
from .index import BM25
from .loader import Entry
from .vector import EmbeddingClient, VectorIndex


@dataclass
class Hit:
    entry: Entry
    score: float        # RRF 融合分，只用来排序
    coverage: float
    bm25: float = 0.0   # 融合前的原始分。RRF 会把相邻名次压到 0.016/0.016，
                        # 所以分析「为什么这条赢了」只能看这个
    rank: int = 0


@dataclass
class Retrieval:
    query: str
    hits: list[Hit]
    gate: str  # strong / weak / irrelevant
    channels: list[str]
    vocab: float = 0.0  # 问题里的词有多大比例真的在语料里，判域外用

    @property
    def top(self) -> Hit | None:
        return self.hits[0] if self.hits else None


class Retriever:
    def __init__(self, entries: list[Entry], settings: Settings,
                 embedder: EmbeddingClient | None = None,
                 vector: VectorIndex | None = None) -> None:
        self.entries = entries
        self.settings = settings
        self.embedder = embedder
        self.vector = vector
        self.bm25 = BM25()
        self.bm25.fit([(e.id, e.index_text) for e in entries])
        self._by_id = {e.id: e for e in entries}
        self._pos = {e.id: i for i, e in enumerate(entries)}

    @property
    def channels(self) -> list[str]:
        ok = self.vector is not None and self.embedder is not None and self.embedder.enabled
        return ["bm25", "vector"] if ok else ["bm25"]

    def get(self, entry_id: str) -> Entry | None:
        return self._by_id.get(entry_id)

    def search(self, query: str, top_k: int | None = None) -> Retrieval:
        k = top_k or self.settings.top_k
        q = (query or "").strip()
        if not q or not self.entries:
            return Retrieval(query=q, hits=[], gate="irrelevant", channels=self.channels)

        scores = self.bm25.scores(q)
        bm25_ids = [
            self.entries[i].id
            for i in sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            if scores[i] > 0
        ]

        rankings = [bm25_ids]
        weights = [1.0]
        if "vector" in self.channels:
            assert self.vector is not None and self.embedder is not None
            qv = self.embedder.embed([q])[0]
            rankings.append([doc_id for doc_id, _ in self.vector.search(qv, k=20)])
            weights.append(1.0)

        raw = {self.entries[i].id: scores[i] for i in range(len(scores))}
        fused = rrf(rankings, k=self.settings.rrf_k, weights=weights, top_n=k)
        hits: list[Hit] = []
        for doc_id, score in fused:
            if doc_id not in self._by_id:
                continue
            hits.append(
                Hit(
                    entry=self._by_id[doc_id],
                    score=round(score, 6),
                    coverage=round(self.bm25.coverage(q, self._pos[doc_id]), 3),
                    bm25=round(raw.get(doc_id, 0.0), 3),
                    rank=len(hits) + 1,
                )
            )

        vocab = self.bm25.vocab_ratio(q)
        return Retrieval(query=q, hits=hits,
                         gate=self._gate(hits, vocab, raw),
                         channels=self.channels, vocab=round(vocab, 3))

    def _gate(self, hits: list[Hit], vocab: float, raw: dict[str, float]) -> str:
        # 一半以上的词语料里都没有，就是这个领域之外的问题
        if vocab < self.settings.gate_min_vocab_ratio:
            return "irrelevant"
        if not hits:
            return "irrelevant"
        top = hits[0]
        s = self.settings
        if top.coverage >= s.gate_strong_coverage:
            # 用融合前的 BM25 原始分算差距：RRF 会把相邻名次压到几乎一样，没法判
            first_raw = raw.get(top.entry.id, 0.0)
            second_raw = raw.get(hits[1].entry.id, 0.0) if len(hits) > 1 else 0.0
            if first_raw <= 0:  # 纯向量命中，没有词面证据
                return "weak"
            margin = first_raw / (first_raw + second_raw) if first_raw + second_raw > 0 else 0.0
            return "strong" if margin >= s.gate_strong_margin else "weak"
        if top.coverage >= s.gate_weak_coverage:
            return "weak"
        # 向量通道命中但词面完全对不上：语义可能对、词面陌生，降级成 weak 交给人看
        return "weak" if "vector" in self.channels else "irrelevant"
