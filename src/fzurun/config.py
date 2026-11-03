from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全部配置走环境变量，前缀 FZURUN_，也可写 .env。"""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="FZURUN_", extra="ignore")

    kb_path: Path = Path("data/kb/qa.json")
    web_dir: Path = Path("web")

    # 检索
    top_k: int = 5
    # 门控阈值，按 99 条语料标定，语料大改后要重标
    gate_strong_coverage: float = 0.5
    gate_weak_coverage: float = 0.3
    gate_strong_margin: float = 0.55
    gate_min_vocab_ratio: float = 0.55

    # RRF 融合参数（沿用老项目的 60，业界默认值）
    rrf_k: int = 60

    # LLM：留空则走「只检索不生成」模式
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout: float = 60.0
    llm_max_tokens: int = 0  # 0 表示不传，用服务端默认
    # 各家有私有参数。智谱的 thinking 开关就走这里：
    #   {"thinking": {"type": "disabled"}}
    # 原样合并进请求体，所以在别的服务上留空就行。
    llm_extra_body: str = ""

    # Embedding：留空则只有 BM25 一路
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = ""
    embed_cache: Path = Path("data/kb/embeddings.npy")

    # 试运行观测：每次问答写一行 JSONL
    log_enabled: bool = True
    log_query: bool = True  # 关掉就只存门控和命中，不存问题原文
    log_dir: Path = Path("data/logs")

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    @property
    def llm_extra(self) -> dict:
        """把 llm_extra_body 解析成 dict。写错了就当没配，不要让服务起不来。"""
        if not self.llm_extra_body.strip():
            return {}
        try:
            value = json.loads(self.llm_extra_body)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @property
    def embed_enabled(self) -> bool:
        return bool(self.embed_base_url and self.embed_model)


settings = Settings()
