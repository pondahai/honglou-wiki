# -*- coding: utf-8 -*-
"""Reduce 階段:彙整 data/facts/ 的逐回事實,為每個人物撰寫有出處的生平。
覆寫 vault/人物/*.md 的「生平」節;可重跑,已含 facts 標記的頁自動跳過。
用法: python compose_bios.py [人數上限,預設全部]
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from characters import CHARACTERS
from config import BOOK_TITLE, VAULT, FACTS
from extract_facts import call_llm

MARKER = "<!-- source: map-reduce facts -->"
SECTION = re.compile(r"(## 生平\n).*?(\n## 出場章回)", re.S)
NAME = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
PENALTY = {"repetition_penalty": 1.08, "presence_penalty": 0.5}


META_RE = re.compile(r"清單未|故不列入|故不寫|推測基於|修正：|受限於清單|若清單")
BROKEN_LINK = re.compile(r"\[\[([^\[\]|]{2,6})\](?!\])")


def has_meta(bio):
    """模型把『要不要寫』的掙扎寫進了正式輸出"""
    return bool(META_RE.search(bio))


def fix_links(bio):
    """修復少一個右括號的斷腳連結 [[名字]"""
    return BROKEN_LINK.sub(r"[[\1]]", bio)


ALIAS2CANON = {a: c for c, al in CHARACTERS.items() for a in al}


def remap_alias_links(bio):
    """模型偶爾用別名連結([[觀音菩薩]]),改寫成 [[正名|別名]] 以免斷連結"""
    def repl(m):
        target, _, label = m.group(1).partition("|")
        canon = ALIAS2CANON.get(target)
        if canon is None:
            return m.group(0)
        return f"[[{canon}|{label or target}]]"
    return re.sub(r"\[\[([^\]]+)\]\]", repl, bio)


def is_degenerate(bio):
    """人物關係節同一名字出現 >3 次即視為重複迴圈退化"""
    rel = bio.split("### 人物關係")[-1]
    names = NAME.findall(rel)
    if not names:
        return False
    from collections import Counter
    return Counter(names).most_common(1)[0][1] > 3


def dedupe_relations(bio):
    """保底清理:人物關係節逐行去除重複的 [[名字]]"""
    head, sep, rel = bio.partition("### 人物關係")
    if not sep:
        return bio
    fixed = []
    for line in rel.splitlines():
        seen = set()
        parts, out = re.split(r"(、)", line), []
        for p in parts:
            key = "".join(NAME.findall(p)) or p
            if p != "、" and key in seen:
                continue
            if p != "、":
                seen.add(key)
            out.append(p)
        clean = "".join(out)
        clean = re.sub(r"(、)+$", "", re.sub(r"、{2,}", "、", clean))
        fixed.append(clean)
    return head + sep + "\n".join(fixed)


def load_facts():
    """人物 -> [(回數, 事實), ...]"""
    per_char = defaultdict(list)
    for f in sorted(FACTS.glob("ch_*.json")):
        num = int(re.match(r"ch_(\d+)", f.stem).group(1))
        for canon, facts in json.loads(f.read_text(encoding="utf-8")).items():
            for fact in facts:
                per_char[canon].append((num, fact))
    return per_char


def make_prompt(canon, aliases, facts):
    fact_lines = "\n".join(f"第{n}回:{fa}" for n, fa in facts)
    names = "、".join(CHARACTERS.keys())
    alias_str = f"(別名:{'、'.join(aliases)})" if aliases else ""
    return (
        f"以下是從《{BOOK_TITLE}》原文逐回抽取的、關於「{canon}」{alias_str}的全部事實清單,"
        "每條都附回數出處。請據此撰寫 wiki 條目,繁體中文、Markdown,只輸出三節:\n\n"
        "### 生平概述\n(2-4 段,按時間順序綜述)\n\n"
        "### 重要事蹟\n(條列,每項務必註明回數,直接取自清單)\n\n"
        "### 人物關係\n(條列:師徒、主僕、親屬、盟友、對手,只列清單中有依據的)\n\n"
        "嚴格規則:\n"
        "1. 只能根據下方清單撰寫,清單中沒有的情節、關係、稱號一律不寫,即使你認為是常識\n"
        "2. 清單資訊不足的節就寫短一點,不要填補\n"
        f"3. 提及他人時用 [[人物名]] 格式,僅限這些正名:{names}\n"
        "4. 不要輸出任何前言、結語、註記或自我修正;拿不準的內容直接省略,不要解釋為什麼省略\n\n"
        f"=== 事實清單 ===\n{fact_lines}"
    )


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10**9
    per_char = load_facts()
    ranked = sorted(per_char, key=lambda c: -len(per_char[c]))[:limit]
    done = skipped = 0
    for canon in ranked:
        page = VAULT / "人物" / f"{canon}.md"
        if not page.exists():
            continue
        text = page.read_text(encoding="utf-8")
        if MARKER in text:
            skipped += 1
            continue
        facts = per_char[canon]
        print(f"composing {canon} ({len(facts)} facts) ...", flush=True)
        try:
            prompt = make_prompt(canon, CHARACTERS[canon], facts)
            bio = call_llm(prompt, max_tokens=4000, extra=PENALTY)
            if is_degenerate(bio) or has_meta(bio):
                print("  degenerate/meta, retrying ...", flush=True)
                bio = call_llm(prompt, max_tokens=4000, temperature=0.7, extra=PENALTY)
            if is_degenerate(bio):
                bio = dedupe_relations(bio)
                print("  still degenerate, deduped", flush=True)
            if has_meta(bio):
                # 保底:整行剔除仍含碎念的條目
                bio = "\n".join(ln for ln in bio.splitlines() if not META_RE.search(ln))
                print("  still meta, lines dropped", flush=True)
            bio = remap_alias_links(fix_links(bio))
        except Exception as e:
            print(f"  FAILED {canon}: {e}", flush=True)
            continue
        new = SECTION.sub(lambda m: m.group(1) + "\n" + MARKER + "\n\n" + bio + "\n" + m.group(2), text, count=1)
        if new == text:
            print(f"  SKIP {canon}: 生平 section not found", flush=True)
            continue
        page.write_text(new, encoding="utf-8")
        done += 1
        print(f"  ok ({len(bio)} chars)", flush=True)
    print(f"done={done} skipped={skipped}")


if __name__ == "__main__":
    main()
