"""向量通道。

默认关闭：没配 embedding 服务时整条链路退化成纯 BM25，照样能跑。
配了就走 OpenAI 兼容的 /embeddings，向量缓存在 data/kb/embeddings.npy，
条目 id 集合变了才重算。

没有用 FAISS：99 条 × 1024 维，一次矩阵乘就是毫秒级，
FAISS 反而带来越野式的麻烦（索引文件反序列化、增量更新、维度锁死）。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import numpy as np

from ..config import Settings


class EmbeddingClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.embed_base_url and self.settings.embed_model)

    def embed(self, texts: list[str]) -> np.ndarray:
        if not self.enabled:
            raise RuntimeError("embedding 未配置：FZURUN_EMBED_BASE_URL / FZURUN_EMBED_MODEL 为空")
        url = self.settings.embed_base_url.rstrip("/") + "/embeddings"
        headers = {"Content-Type": "application/json"}
        if self.settings.embed_api_key:
            headers["Authorization"] = "Bearer " + self.settings.embed_api_key
        with httpx.Client(timeout=self.settings.llm_timeout) as client:
            resp = client.post(
                url, headers=headers,
                json={"model": self.settings.embed_model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
        vecs = np.array([d["embedding"] for d in data["data"]], dtype=np.float32)
        return _normalize(vecs)


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


class VectorIndex:
    def __init__(self, ids: list[str], matrix: np.ndarray) -> None:
        self.ids = ids
        self.matrix = _normalize(matrix)

    @classmethod
    def load_or_build(cls, cache: Path, ids: list[str], texts: list[str],
                      client: EmbeddingClient) -> VectorIndex | None:
        if not client.enabled:
            return None
        if cache.exists():
            meta = json.loads(cache.with_suffix(".json").read_text(encoding="utf-8"))
            if meta.get("ids") == ids:
                return cls(ids, np.load(cache))
        matrix = client.embed(texts)
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache, matrix)
        cache.with_suffix(".json").write_text(
            json.dumps({"ids": ids, "model": client.settings.embed_model}, ensure_ascii=False),
            encoding="utf-8",
        )
        return cls(ids, matrix)

    def search(self, query_vec: np.ndarray, k: int = 20) -> list[tuple[str, float]]:
        q = _normalize(query_vec.reshape(1, -1))[0]
        sims = self.matrix @ q
        order = np.argsort(-sims)[:k]
        return [(self.ids[i], float(sims[i])) for i in order]
