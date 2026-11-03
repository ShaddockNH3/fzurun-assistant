#!/usr/bin/env python3
"""把试运行的日志收成一份人看的东西。

    python3 scripts/review.py              # 全量
    python3 scripts/review.py --days 7     # 最近 7 天
    python3 scripts/review.py --days 7 --out report.md

回答三个问题：哪些问题没人答上来、哪些是命中了但答偏、同义问法还缺什么。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fzurun.config import settings
from fzurun.obs.log import read_jsonl

GATE_LABEL = {"strong": "强命中", "weak": "弱命中", "irrelevant": "没命中"}
REASON_LABEL = {
    "wrong": "答错了",
    "missing": "文档里其实有，没找到",
    "outdated": "政策过期了",
    "unclear": "答得太含糊",
    "other": "其他",
}


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round((len(s) - 1) * p)))]


def ms(v: float) -> str:
    return f"{v:.0f}" if v >= 10 else f"{v:.1f}"


def load(kind: str, since: str) -> list[dict]:
    recs = read_jsonl(sorted(Path(settings.log_dir).glob(f"{kind}-*.jsonl")))
    if since:
        recs = [r for r in recs if str(r.get("ts", ""))[:10] >= since]
    return recs


def bar(n: int, total: int, width: int = 28) -> str:
    if total <= 0:
        return ""
    filled = max(1, round(n / total * width)) if n else 0
    return "█" * filled


def top_queries(recs: list[dict], limit: int = 20) -> list[tuple[str, int]]:
    c = Counter((r.get("query") or "").strip() for r in recs)
    c.pop("", None)
    return c.most_common(limit)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="只看最近 N 天，默认全量")
    ap.add_argument("--out", default="", help="写进文件而不是打到终端")
    ap.add_argument("--limit", type=int, default=20, help="每个榜单列几条")
    args = ap.parse_args()

    since = (date.today() - timedelta(days=args.days)).isoformat() if args.days else ""
    asks = load("ask", since)
    fbs = load("feedback", since)

    lines: list[str] = []
    add = lines.append

    add("# 试运行报告")
    add("")
    span = f"最近 {args.days} 天" if args.days else "全部日志"
    if asks:
        span += f"，{str(asks[0].get('ts'))[:10]} 起"
    add(f"范围：{span}　问答 {len(asks)} 次　反馈 {len(fbs)} 条")
    add("")

    if not asks:
        add("还没有日志。跑几次 `/api/ask` 再来看，或者先确认 `FZURUN_LOG_ENABLED` 没被关掉。")
        text = "\n".join(lines)
        print(text)
        return 0

    # ---- 门控 ----
    gates = Counter(r.get("gate", "?") for r in asks)
    add("## 门控分布")
    add("")
    for g in ("strong", "weak", "irrelevant"):
        n = gates.get(g, 0)
        add(f"{GATE_LABEL[g]:<6}{n:>5}  {n / len(asks) * 100:>5.1f}%  {bar(n, len(asks))}")
    add("")

    lat = [float(r.get("latency_ms") or 0) for r in asks]
    modes = Counter(r.get("mode", "?") for r in asks)
    add(f"延迟 p50 {ms(pct(lat, 0.5))}ms · p95 {ms(pct(lat, 0.95))}ms（不含模型推理）")
    add("模式 " + " · ".join(f"{k} {v}" for k, v in modes.most_common()))
    add("")

    # ---- 没答上来 ----
    miss = [r for r in asks if r.get("gate") == "irrelevant"]
    ranked = top_queries(miss, args.limit)
    add(f"## 没答上来的　{len(miss)} 次 / {len({r.get('query') for r in miss})} 种问法")
    add("")
    if not ranked:
        add("（一条都没有）")
    for q, n in ranked:
        add(f"  {n:>3} ×  {q}")
    add("")
    add("> 要分两类看：文档真的没写（记进「答不了」清单），还是同义问法没覆盖（补 `alt.json`）。")
    add("> 反反复复出现的，基本都是第二类。")
    add("")

    # ---- 弱命中 ----
    weak = [r for r in asks if r.get("gate") == "weak"]
    add(f"## 弱命中　{len(weak)} 次 / {len({r.get('query') for r in weak})} 种问法")
    add("")
    add("这些是「勉强找到了，但不确定找对没有」，也是 `alt.json` 最该补的地方。")
    add("")
    for q, n in top_queries(weak, args.limit):
        add(f"  {n:>3} ×  {q}")
    if not weak:
        add("（一条都没有）")
    add("")

    # 直接给出 alt.json 的骨架：按「当时的第一名」把问法归堆。
    # 映射对不对还得人看，但省掉从零整理那一步。
    buckets: dict[str, Counter] = {}
    for r in weak:
        top = (r.get("top") or [{}])[0].get("id")
        q = (r.get("query") or "").strip()
        if not top or not q:
            continue
        buckets.setdefault(top, Counter())[q] += 1
    if buckets:
        add("### 可以粘进 `alt.json` 的骨架")
        add("")
        add("按「当时排第一的那条」把问法归了堆。映射对不对要你自己看一眼，别直接提交。")
        add("")
        add("```json")
        add(json.dumps({k: sorted(c) for k, c in sorted(buckets.items())},
                       ensure_ascii=False, indent=2))
        add("```")
        add("")

    # ---- 反馈 ----
    add(f"## 用户说不对　{sum(1 for f in fbs if f.get('verdict') == 'bad')} 条")
    add("")
    bad = [f for f in fbs if f.get("verdict") == "bad"]
    if bad:
        reasons = Counter(f.get("reason") or "other" for f in bad)
        add("原因分布：" + " · ".join(f"{REASON_LABEL.get(k, k)} {v}" for k, v in reasons.most_common()))
        add("")
        by_id = {r.get("request_id"): r for r in asks}
        for f in bad[: args.limit]:
            src = by_id.get(f.get("request_id"), {})
            gate = src.get("gate", "?")
            top = (src.get("top") or [{}])[0]
            hit = top.get("id", "-")
            bm25 = top.get("bm25", 0)
            add(
                f"  [{REASON_LABEL.get(f.get('reason') or 'other', '其他')}] {f.get('query') or '（无）'}"
                f"　→ 当时命中 {hit}（bm25 {bm25}），{GATE_LABEL.get(gate, gate)}"
            )
            note = (f.get("note") or "").strip()
            if note:
                add(f"         备注：{note}")
    else:
        add("（还没有，或者没人点）")
    add("")

    # ---- 建议 ----
    add("## 接下来做什么")
    add("")
    tips: list[str] = []
    repeat_miss = [(q, n) for q, n in ranked if n >= 2]
    if repeat_miss:
        tips.append(f"有 {len(repeat_miss)} 个问题被反复问到却没命中，优先给这些补同义问法或补条目。")

    strong_bad = 0
    by_id = {r.get("request_id"): r for r in asks}
    for f in bad:
        if by_id.get(f.get("request_id"), {}).get("gate") == "strong":
            strong_bad += 1
    if strong_bad:
        tips.append(
            f"{strong_bad} 条差评出现在强命中上——检索没找错，是答案组织或者条目内容的问题，别再去调阈值。"
        )

    miss_rate = gates.get("irrelevant", 0) / len(asks)
    if miss_rate > 0.3:
        tips.append(
            f"没命中率 {miss_rate * 100:.0f}%，偏高。要么知识库覆盖面不够，要么阈值定硬了，"
            "先看那一批 query 是「问歪了」还是「真没有」。"
        )
    if pct(lat, 0.95) > 3000:
        tips.append(f"p95 延迟 {ms(pct(lat, 0.95))}ms，该看模型那一侧了。")
    if not tips:
        tips.append("暂时看不出系统性问题，继续攒样本。")
    for i, t in enumerate(tips, 1):
        add(f"{i}. {t}")
    add("")

    text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"写到 {args.out}（{len(text)} 字）")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
