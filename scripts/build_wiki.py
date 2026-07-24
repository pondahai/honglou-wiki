# -*- coding: utf-8 -*-
"""原文 → Obsidian wiki vault
讀 config.RAW,切章回、自動加人名 [[連結]]、生成人物頁與索引。
書名/回數/路徑等設定見 config.py。
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from characters import CHARACTERS, EXCLUDE_PHRASES
from config import BOOK_TITLE, N_CHAPTERS, RAW, VAULT, EDITION_NOTE, HEADING, PREFACE_TITLE

EXCLUDE_RE = re.compile("|".join(re.escape(p) for p in EXCLUDE_PHRASES)) if EXCLUDE_PHRASES else None

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn2int(s: str) -> int:
    """中文數字(一~一百二十)轉整數;支援位值寫法(一零零、一一零、一二零)"""
    if "百" not in s and "十" not in s and len(s) > 1:
        return int("".join(str(CN_NUM.get(c, 0)) for c in s))
    n = 0
    if "百" in s:
        head, s = s.split("百")
        n += (CN_NUM.get(head, 1)) * 100
    if "十" in s:
        head, tail = s.split("十")
        n += (CN_NUM[head] if head else 1) * 10
        n += CN_NUM.get(tail, 0)
    elif s:
        n += CN_NUM[s]
    return n


def parse_chapters(text: str):
    """回傳 [(num, title, body_lines)];目錄區的重複標題以「最後一次出現」為準"""
    lines = text.splitlines()
    marks = []  # (line_idx, num, title)
    for i, ln in enumerate(lines):
        m = HEADING.match(ln.strip())
        if m:
            # 標題 = 第 1 群組之後所有非空群組(上下聯兩組或單句一組皆可)
            title = "  ".join(g for g in m.groups()[1:] if g)
            marks.append((i, cn2int(m.group(1)), title))
    # 每個回數取最後一次出現(前面是目錄)
    last = {}
    for i, num, title in marks:
        last[num] = (i, title)
    order = sorted(last.items())  # by num
    chapters = []
    for k, (start, title) in order:
        # 正文結束於下一回正文開頭
        nexts = [s for n, (s, _) in last.items() if s > start]
        end = min(nexts) if nexts else len(lines)
        body = lines[start + 1 : end]
        chapters.append((k, title, body))
    return chapters


def build_alias_map():
    """別名 -> 正名,含正名本身;回傳 (map, 由長到短排序的 regex)"""
    amap = {}
    for canon, aliases in CHARACTERS.items():
        amap[canon] = canon
        for a in aliases:
            amap[a] = canon
    keys = sorted(amap.keys(), key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(k) for k in keys))
    return amap, pattern


def link_paragraph(par: str, amap, pattern, seen_counter):
    """段落內每個人物只連結第一次出現;同時統計出現次數"""
    linked_in_par = set()
    # 名字與景物/官職同形處(見 EXCLUDE_PHRASES)的字元範圍,落在其中的比對一律略過
    blocked = [m.span() for m in EXCLUDE_RE.finditer(par)] if EXCLUDE_RE else []

    def repl(m):
        word = m.group(0)
        if any(s <= m.start() < e for s, e in blocked):
            return word
        canon = amap[word]
        seen_counter[canon] += 1
        if canon in linked_in_par:
            return word
        linked_in_par.add(canon)
        return f"[[{canon}]]" if word == canon else f"[[{canon}|{word}]]"

    return pattern.sub(repl, par)


def reflow(body_lines):
    """逐行文字重排成段落:以全形空格開頭視為新段"""
    paras = []
    cur = ""
    for ln in body_lines:
        ln = ln.rstrip()
        if not ln.strip():
            continue
        if ln.startswith("  ") or ln.startswith("　"):  # 新段落
            if cur:
                paras.append(cur)
            cur = ln.strip()
        else:
            cur += ln.strip()
    if cur:
        paras.append(cur)
    return paras


def main():
    text = RAW.read_text(encoding="utf-8")
    chapters = parse_chapters(text)
    print(f"parsed {len(chapters)} chapters")
    assert len(chapters) == N_CHAPTERS, f"應為 {N_CHAPTERS} 回"

    amap, pattern = build_alias_map()

    (VAULT / "回目").mkdir(parents=True, exist_ok=True)
    (VAULT / "人物").mkdir(exist_ok=True)

    # 人物 -> {回數: 次數}
    appearances = defaultdict(dict)
    chapter_files = {}

    for num, title, body in chapters:
        fname = f"第{num:03d}回 {title.replace('  ', ' ')}"
        chapter_files[num] = fname
        paras = reflow(body)
        counter = defaultdict(int)
        linked = [link_paragraph(p, amap, pattern, counter) for p in paras]
        for canon, c in counter.items():
            appearances[canon][num] = c

        prev_link = f"[[第{num-1:03d}回 {chapters[num-2][1].replace('  ', ' ')}|← 上一回]]" if num > 1 else ""
        next_link = f"[[第{num+1:03d}回 {chapters[num][1].replace('  ', ' ')}|下一回 →]]" if num < N_CHAPTERS else ""
        nav = " | ".join(x for x in (prev_link, "[[回目索引]]", next_link) if x)

        content = f"# 第{num}回 {title}\n\n{nav}\n\n" + "\n\n".join(linked) + f"\n\n---\n{nav}\n"
        (VAULT / "回目" / f"{fname}.md").write_text(content, encoding="utf-8")

    # 人物頁(已有 LLM 生成生平的保留,不重置)
    section_re = re.compile(r"## 生平\n(.*?)\n## 出場章回", re.S)
    for canon, aliases in CHARACTERS.items():
        old_bio = None
        old_page = VAULT / "人物" / f"{canon}.md"
        if old_page.exists():
            m = section_re.search(old_page.read_text(encoding="utf-8"))
            if m and "待 LLM 生成" not in m.group(1):
                old_bio = m.group(1).strip()
        chaps = appearances.get(canon, {})
        total = sum(chaps.values())
        lines = [f"# {canon}\n"]
        if aliases:
            lines.append(f"**別名**:{'、'.join(aliases)}\n")
        lines.append(f"**總出現次數**:{total},**出場回數**:{len(chaps)} 回\n")
        lines.append(f"## 生平\n\n{old_bio or '_(待 LLM 生成)_'}\n")
        lines.append("## 出場章回\n")
        for n in sorted(chaps):
            lines.append(f"- [[{chapter_files[n]}|第{n}回]] ({chaps[n]} 次)")
        (VAULT / "人物" / f"{canon}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 卷首文字(第一回之前),獨立成頁;人名照樣連結,但不計入出場統計
    has_preface = False
    if PREFACE_TITLE:
        lines_all = text.splitlines()
        first = next((i for i, ln in enumerate(lines_all) if HEADING.match(ln.strip())), 0)
        pre = [ln for ln in lines_all[:first] if ln.strip() and ln.strip() != PREFACE_TITLE]
        if pre:
            paras = reflow(pre)
            dummy = defaultdict(int)
            linked = [link_paragraph(p, amap, pattern, dummy) for p in paras]
            (VAULT / f"{PREFACE_TITLE}.md").write_text(
                f"# {PREFACE_TITLE}\n\n[[首頁]] | [[{chapter_files[1]}|第1回 →]]\n\n"
                + "\n\n".join(linked) + "\n",
                encoding="utf-8",
            )
            has_preface = True

    # 索引頁
    idx = ["# 回目索引\n"]
    for num, title, _ in chapters:
        idx.append(f"- [[{chapter_files[num]}|第{num}回 {title}]]")
    (VAULT / "回目索引.md").write_text("\n".join(idx) + "\n", encoding="utf-8")

    ranked = sorted(CHARACTERS, key=lambda c: -sum(appearances.get(c, {}).values()))
    pidx = ["# 人物索引\n", "依全書出現次數排序:\n"]
    for c in ranked:
        t = sum(appearances.get(c, {}).values())
        pidx.append(f"- [[{c}]] — {t} 次,{len(appearances.get(c, {}))} 回")
    (VAULT / "人物索引.md").write_text("\n".join(pidx) + "\n", encoding="utf-8")

    (VAULT / "首頁.md").write_text(
        f"# {BOOK_TITLE} Wiki\n\n{EDITION_NOTE}\n\n"
        + (f"- [[{PREFACE_TITLE}]] — 卷首文字\n" if has_preface else "")
        + f"- [[回目索引]] — 全部 {N_CHAPTERS} 回正文\n"
        "- [[人物索引]] — 主要人物(依出現次數)\n\n"
        "人名在正文中已自動連結,點擊即可跳到人物頁;"
        "在 Obsidian 開啟本資料夾即可使用 graph view 檢視人物關係網。\n",
        encoding="utf-8",
    )
    print("top 10:", [(c, sum(appearances.get(c, {}).values())) for c in ranked[:10]])
    print("vault written to", VAULT)


if __name__ == "__main__":
    main()
