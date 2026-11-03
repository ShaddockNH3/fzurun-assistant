#!/usr/bin/env python3
"""把源文档解析成 data/kb/qa.json。

设计要点：
  1. 文档是唯一源，本脚本只做派生，不改文档、不改内容措辞。
  2. id 是出生编号，一旦分配就冻结：重编号、挪小节都不改，删了留 removed 墓碑。
  3. 重新生成时人工字段（tier 微调 / caveats / alt_questions / related / tags /
     status / superseded_by / grade_scope / notes）一律保留，只刷新正文类字段。

用法：
  uv run python scripts/build_kb.py                  # 生成并覆盖 data/kb/qa.json
  uv run python scripts/build_kb.py --dry-run        # 只打印报告，不写文件
  uv run python scripts/build_kb.py --doc 别的文档.md # 换一份源

源文档默认是同级目录下的 Firefly 站点：
  ../Firefly/src/content/posts/FZU/overview/index.md
也可以用环境变量 KB_DOC 覆盖。
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # 项目根
DOC = Path(os.environ.get("KB_DOC")
           or ROOT.parent / "Firefly/src/content/posts/FZU/overview/index.md")
OUT = ROOT / "data/kb/qa.json"
ALT = ROOT / "data/kb/alt.json"
SCHEMA_VERSION = 1

# 不进知识库的小节（群运营内容，bot 不该答）
KB_FALSE = {"0.1", "0.2", "0.3"}

# 子块（#### 非问答）的 ascii 短名，新增子块要在这里登记，否则退化成 r1/r2
REF_SLUG = {
    "25 年大一转专业报录比（经验值）": "ratio",
    "前辈踩过的坑": "pitfalls",
    "2026 级报名门槛": "gate",
    "通用考核用书": "books",
    "备忘清单": "checklist",
    "前往计算机院楼的路线": "route",
    "教务处最新通知": "jwc-notice",
    "2026 级校级实验班招收通知": "exp-class",
}

# 子块的默认 tier
REF_TIER = {
    "ratio": "experience",
    "pitfalls": "experience",
    "route": "tool",
    "checklist": "tool",
    "books": "tool",
    "gate": "policy",
    "jwc-notice": "policy",
    "exp-class": "policy",
}

# 单条问答的默认 tier（按小节）
SEC_TIER = {
    "1.4": "experience",
    "4.1": "tool",
    "4.2": "tool",
}
SEC_TIER_DEFAULT = "policy"

KEEP_ON_REBUILD = ["tier", "caveats", "alt_questions", "related", "tags",
                   "status", "superseded_by", "grade_scope", "notes", "source_quote"]


def slugify(text, counter):
    """复刻 github-slugger：小写、去标点、空白转连字符、重名加 -1/-2"""
    s = unicodedata.normalize("NFKD", text).lower()
    out = []
    for ch in s:
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        elif ch.isspace():
            out.append("-")
    base = "".join(out)
    n = counter.get(base, 0)
    counter[base] = n + 1
    return base if n == 0 else f"{base}-{n}"


def parse(doc_text):
    lines = doc_text.split("\n")
    # 跳过 frontmatter
    i = 0
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            i += 1
        i += 1

    heads = []  # (level, text, line_no)
    for idx in range(i, len(lines)):
        m = re.match(r"^(#{2,4})\s+(.*)$", lines[idx])
        if m:
            heads.append((len(m.group(1)), m.group(2).strip(), idx))

    counter = {}
    entries = []
    part = ""
    section = ""
    secnum = ""

    for k, (level, text, ln) in enumerate(heads):
        anchor = slugify(text, counter)
        end = len(lines)
        for j in range(k + 1, len(heads)):
            if heads[j][0] <= level:
                end = heads[j][2]
                break
        body = "\n".join(lines[ln + 1:end]).strip()

        if level == 2:
            part = text
            continue
        if level == 3:
            section = text
            m = re.match(r"^([0-9]+(?:\.[0-9]+)?|[A-Z])(?:\s|$)", text)
            secnum = m.group(1) if m else re.sub(r"\s.*", "", text)[:6]
            if secnum in KB_FALSE:
                continue
            # 整节一个 #### 都没有，才单独成条；否则由下面的子块覆盖
            if body and not re.search(r"^#### ", body, re.MULTILINE):
                entries.append({
                    "schema_version": SCHEMA_VERSION,
                    "id": f"sec.{secnum}",
                    "kind": "section",
                    "part": part,
                    "section": section,
                    "title": re.sub(r"^([0-9]+(?:\.[0-9]+)?|[A-Z])\s+", "", text),
                    "content": body,
                    "tier": SEC_TIER.get(secnum, SEC_TIER_DEFAULT),
                    "status": "active",
                    "superseded_by": None,
                    "caveats": [],
                    "related": [],
                    "tags": [],
                    "grade_scope": [],
                    "links": [],
                    "assets": [],
                    "anchor": anchor,
                    "source_quote": None,
                    "notes": None,
                })
            continue

        # level == 4
        m = re.match(r"^Q([0-9]+)[：:]\s*(.*)$", text)
        if m:
            entries.append({
                "schema_version": SCHEMA_VERSION,
                "id": f"{secnum}.q{m.group(1)}",
                "kind": "qa",
                "part": part,
                "section": section,
                "question": m.group(2).strip(),
                "alt_questions": [],
                "content": body,
                "tier": SEC_TIER.get(secnum, SEC_TIER_DEFAULT),
                "status": "active",
                "superseded_by": None,
                "caveats": [],
                "related": [],
                "tags": [],
                "grade_scope": [],
                "links": [],
                "assets": [],
                "anchor": anchor,
                "source_quote": None,
                "notes": None,
            })
        else:
            slug = REF_SLUG.get(text)
            if not slug:
                slug = f"r{len([e for e in entries if e["id"].startswith(f"ref.{secnum}.")])}"
            entries.append({
                "schema_version": SCHEMA_VERSION,
                "id": f"ref.{secnum}.{slug}",
                "kind": "reference",
                "part": part,
                "section": section,
                "title": text,
                "content": body,
                "tier": REF_TIER.get(slug, SEC_TIER_DEFAULT),
                "status": "active",
                "superseded_by": None,
                "caveats": [],
                "related": [],
                "tags": [],
                "grade_scope": [],
                "links": [],
                "assets": [],
                "anchor": anchor,
                "source_quote": None,
                "notes": None,
            })
    return entries


def merge(old, new):
    """按 id 合并：老条目保留人工字段，刷新正文；消失的标 removed；新增的分配 id"""
    old_by_id = {e["id"]: e for e in old}
    old_by_q = {}
    for e in old:
        if e["kind"] == "qa":
            old_by_q[(e["section"], e["question"])] = e

    out, added, updated, moved = [], [], [], []
    for e in new:
        prev = old_by_id.get(e["id"])
        if prev is None and e["kind"] == "qa":
            prev = old_by_q.get((e["section"], e["question"]))
            if prev:
                e["id"] = prev["id"]
                moved.append((prev["id"], e["question"]))
            else:
                added.append(e["id"])
        elif prev is None:
            added.append(e["id"])

        if prev:
            for f in KEEP_ON_REBUILD:
                if f in prev:
                    e[f] = prev[f]
            if prev.get("content") != e.get("content") or prev.get("question") != e.get("question"):
                updated.append(e["id"])
        out.append(e)

    seen = {e["id"] for e in out}
    for e in old:
        if e["id"] not in seen and e["status"] != "removed":
            e = dict(e)
            e["status"] = "removed"
            out.append(e)

    return out, added, updated, moved


def validate(entries):
    errs = []
    ids = set()
    for e in entries:
        i = e["id"]
        if i in ids:
            errs.append(f"id 重复: {i}")
        ids.add(i)
        if not e.get("content"):
            errs.append(f"正文为空: {i}")
        if e["kind"] == "qa" and not e.get("question"):
            errs.append(f"缺 question: {i}")
        if e["kind"] != "qa" and not e.get("title"):
            errs.append(f"缺 title: {i}")
        if e["tier"] not in ("policy", "experience", "tool"):
            errs.append(f"tier 非法: {i}")
        if e["status"] not in ("active", "deprecated", "removed"):
            errs.append(f"status 非法: {i}")
        if e["status"] == "deprecated" and not e.get("superseded_by"):
            errs.append(f"deprecated 缺 superseded_by: {i}")
    for e in entries:
        for r in e.get("related") or []:
            if r not in ids:
                errs.append("related 指向不存在的 id: {} -> {}".format(e["id"], r))
        if e.get("superseded_by") and e["superseded_by"] not in ids:
            errs.append("superseded_by 指向不存在: {}".format(e["id"]))
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", default=DOC, help="源 markdown 路径")
    ap.add_argument("--dry-run", action="store_true", help="只打印报告，不写文件")
    args = ap.parse_args()

    doc = Path(args.doc).read_text(encoding="utf-8")
    new = parse(doc)

    old = []
    if OUT.exists():
        old = json.loads(OUT.read_text(encoding="utf-8"))

    entries, added, updated, moved = merge(old, new)

    if ALT.exists():
        alt = json.loads(ALT.read_text(encoding="utf-8"))
        for e in entries:
            if e["id"] in alt:
                e["alt_questions"] = sorted(set(alt[e["id"]]))
        ids_in = {e["id"] for e in entries}
        miss = [k for k in alt if k not in ids_in]
        if miss:
            print("alt.json 里有对不上的 id: {}".format(", ".join(miss[:6])))

    errs = validate(entries)

    def count(k):
        return len([e for e in entries if e["kind"] == k and e["status"] != "removed"])

    print(f"源: {os.path.relpath(args.doc, ROOT)}")
    print(f"解析: qa {count('qa')} / section {count('section')} "
          f"/ reference {count('reference')}，共 {len(entries)} 条")
    print(f"新增 {len(added)}，正文变更 {len(updated)}，因移动而沿用旧 id {len(moved)}")
    if added:
        print("  新增:", ", ".join(added[:12]) + (" …" if len(added) > 12 else ""))
    if updated:
        print("  变更:", ", ".join(updated[:12]) + (" …" if len(updated) > 12 else ""))
    removed = [e["id"] for e in entries if e["status"] == "removed"]
    if removed:
        print("  标为 removed:", ", ".join(removed[:12]))

    if errs:
        print(f"\n校验失败 {len(errs)} 条:")
        for x in errs[:20]:
            print("  ✗", x)
        return 1
    print("校验通过 ✓")

    if args.dry_run:
        print("\n--dry-run，未写文件")
        return 0

    entries.sort(key=lambda e: (e["part"], e["section"], e["id"]))
    OUT.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {os.path.relpath(OUT, ROOT)}（{len(entries)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
