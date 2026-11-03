"""OpenAI 兼容的 chat 客户端。

只发一个 POST /chat/completions，所以直接用 httpx，不引 openai SDK。
任何 OpenAI 风格的服务都能用：智谱、DeepSeek、vllm serve、Ollama、LM Studio。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import Settings


class ChatClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.llm_enabled

    def complete(self, messages: list[dict[str, str]], temperature: float = 0.0) -> str:
        if not self.enabled:
            raise RuntimeError("LLM 未配置：FZURUN_LLM_BASE_URL / FZURUN_LLM_MODEL 为空")

        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        # 私有参数放最后，允许覆盖上面的默认值
        payload.update(self.settings.llm_extra)
        if self.settings.llm_max_tokens > 0:
            payload["max_tokens"] = self.settings.llm_max_tokens
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = "Bearer " + self.settings.llm_api_key

        with httpx.Client(timeout=self.settings.llm_timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"] or ""

    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any] | None:
        """宽松解析：模型常把 JSON 包在 ``` 里，前后还带解释。"""
        text = self.complete(messages)
        if not text:
            return None
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
