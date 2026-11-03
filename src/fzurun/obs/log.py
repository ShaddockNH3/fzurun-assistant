"""试运行期的观测。

只做一件事：每次问答压成一行 JSONL，事后能回答三个问题——

  1. 哪些问题一直没人答上来（gate=irrelevant，或者命中被人说不对）
  2. 同义问法缺哪些（真实 query 和知识库问法的差集，就是 alt.json 的待补清单）
  3. 门控阈值是不是标错了（strong 里被判 bad 的比例）

不引数据库。试运行一天几百行，grep 就够了。真到几十万行再换 SQLite，
那时候 schema 也稳定了，迁移是一次性的事。
"""

from __future__ import annotations

import json
import re
import secrets
from collections import deque
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

# query 要留原文，否则看不出用户到底怎么问的。但手机号和身份证不该落盘。
# QQ 号只在带上下文时脱敏，不然「5:1」「2026」这种也会被误伤。
RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
RE_ID = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
RE_QQ = re.compile(r"(?i)(qq|扣扣|企鹅)\s*[:：号]?\s*\d{5,11}")

# 反馈原因，前端和 bot 共用这几个枚举，别自由发挥
REASONS = ("wrong", "missing", "outdated", "unclear", "other")


def scrub(text: str) -> str:
    t = RE_PHONE.sub("<phone>", text)
    t = RE_ID.sub("<id>", t)
    t = RE_QQ.sub(lambda m: f"{m.group(1)}:<qq>", t)
    return t


def new_request_id() -> str:
    """6 位十六进制。够短，用户能从屏幕上念出来。"""
    return secrets.token_hex(3)


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Observation:
    def __init__(self, log_dir: Path, enabled: bool = True, keep_query: bool = True):
        self.dir = Path(log_dir)
        self.enabled = enabled
        self.keep_query = keep_query
        # request_id → query。反馈可能晚几分钟甚至第二天才来，
        # 所以只缓存最近 500 条；查不到就让调用方自己带上。
        self._recent: dict[str, str] = {}
        self._order: deque[str] = deque(maxlen=500)

    # ---- 写 ----

    def ask(self, record: dict[str, Any]) -> None:
        rid = record.get("request_id")
        if rid and self.keep_query:
            self._remember(rid, record.get("query", ""))
        if not self.enabled:
            return
        self._append("ask", {**record, "ts": _stamp()})

    def feedback(self, record: dict[str, Any]) -> str:
        """写一条反馈。返回服务端补上的 query（可能为空）。"""
        rid = record.get("request_id", "")
        query = record.get("query") or self._recent.get(rid, "")
        record = {**record, "query": query, "ts": _stamp()}
        if self.enabled:
            self._append("feedback", record)
        return query

    def recall(self, request_id: str) -> str:
        return self._recent.get(request_id, "")

    def _remember(self, rid: str, query: str) -> None:
        if rid in self._recent:
            return
        self._recent[rid] = query
        self._order.append(rid)
        while len(self._recent) > 500:
            self._recent.pop(self._order[0], None)

    def _append(self, kind: str, record: dict[str, Any]) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self.dir / f"{kind}-{datetime.now():%Y-%m-%d}.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # 日志写不进去不该让用户看不到答案
            pass


def read_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in paths:
        try:
            with Path(p).open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            continue
    return out
