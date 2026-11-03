"""倒数排名融合（RRF）。

从老项目的 utils/rrf.py 搬过来，修了三处：
  1. 参数化 k 和 top_n（原来 k=60 写死，末尾还硬编码 [:20]）
  2. 按 id 融合，不再序列化整个 Document 去重（原来那么做，同内容不同 id 的重复项去不掉）
  3. 支持多路不同权重，方便以后给向量路加权

RRF 的好处是不用管两路分数量纲不一致——只看名次。
"""

from __future__ import annotations


def rrf(rankings: list[list[str]], k: int = 60, weights: list[float] | None = None,
        top_n: int | None = None) -> list[tuple[str, float]]:
    """rankings: 每路一个按相关度降序排好的 id 列表。返回 [(id, 融合分)]。"""
    if weights is None:
        weights = [1.0] * len(rankings)

    fused: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights):
        for rank, doc_id in enumerate(ranking):
            fused[doc_id] = fused.get(doc_id, 0.0) + weight / (k + rank + 1)

    ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
    return ordered[:top_n] if top_n else ordered
