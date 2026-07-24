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
from config import BOOK_TITLE, VAULT, FACTS, RELATION_KINDS
from extract_facts import call_llm
from zh_fix import find_simplified, fix_simplified

MARKER = "<!-- source: map-reduce facts -->"
SECTION = re.compile(r"(## 生平\n).*?(\n## 出場章回)", re.S)
NAME = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
PENALTY = {"repetition_penalty": 1.08, "presence_penalty": 0.5}


META_RE = re.compile(
    r"清單|資料|故不列入|故不寫|推測基於|修正：|來源：|依據：|"
    r"未見記載|未明確|無從得知|本文所依|所提供"
)

# 抽取階段偶爾把「本回沒戲」也寫成一條事實(規則 2 明訂應輸出空陣列),不能餵給撰寫階段
NON_EVENT_RE = re.compile(r"僅被提及|僅提及|未登場|沒有登場|無相關情節|未實際登場|無具體情節|未參與")
BROKEN_LINK = re.compile(r"\[\[([^\[\]|]{2,6})\](?!\])")
# 模型偶爾把 [[名字]] 的右括號打成 }}/}]/] ,一律補正成 ]]
MALFORMED_LINK = re.compile(r"\[\[([^\[\]{}|\n]{1,8})(?:\}\}|\}\]|\]\}|\})")


def has_meta(bio):
    """模型把『要不要寫』的掙扎寫進了正式輸出"""
    return bool(META_RE.search(bio))


# 跳出書外的百科口吻(「X 為清代小說《紅樓夢》中的人物」);只用來觸發重試,
# 不做整行剔除——那句話通常和開頭段黏在一起,砍掉會連正常敘述一起沒了
OUTSIDE_RE = re.compile(r"清代|小說《|中的(?:虛構)?人物|作者|曹雪芹|章回小說")


def has_outside_view(bio):
    return bool(OUTSIDE_RE.search(bio))


REQUIRED_SECTIONS = ("### 生平概述", "### 重要事蹟", "### 人物關係")


def missing_sections(bio):
    """回傳缺少的必要小節(模型偶爾整節漏掉,如只寫概述與事蹟)"""
    return [s for s in REQUIRED_SECTIONS if s not in bio]


def fix_links(bio):
    """修復收尾括號打壞的連結:[[名字]（少一括號）、[[名字}}(打成大括號)"""
    bio = MALFORMED_LINK.sub(r"[[\1]]", bio)
    return BROKEN_LINK.sub(r"[[\1]]", bio)


# 回數出處被寫成 [[第26回]]、[[65回]] 等連結;vault 裡沒有這種頁面,一律是死連結
CHAP_REF = re.compile(r" ?(?:\[\[第?[0-9一二三四五六七八九十百零]+回\]\]、?)+")
CHAP_NUM = re.compile(r"\[\[第?([0-9一二三四五六七八九十百零]+)回\]\]")
# 上一版修補留下的畸形字串「第（第44回）回」
BOTCHED_REF = re.compile(r"第（(第[0-9一二三四五六七八九十百零]+回)）回")


def fix_chapter_refs(bio):
    """回數連結改回純文字:句中位置(在…中)去括號,句末出處位置加全形括號"""
    bio = BOTCHED_REF.sub(r"\1", bio)

    def repl(m):
        body = "、".join(f"第{n}回" for n in CHAP_NUM.findall(m.group(0)))
        prev = bio[m.start() - 1] if m.start() else ""
        nxt = bio[m.end()] if m.end() < len(bio) else ""
        if prev in "在於至自到從第（(" or nxt in "中時裡起":  # 已在括號內就不再加括號
            return body
        return f"（{body}）"

    return CHAP_REF.sub(repl, bio)


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
    """人物關係節退化:塞入過多不同名字(名冊傾倒),或某個名字重複 >3 次(迴圈)"""
    if "### 人物關係" not in bio:  # 缺節,無關係節可退化(缺節另由 has_all_sections 檢查)
        return False
    rel = bio.split("### 人物關係")[-1]
    # 名冊傾倒:模型把整個人物表都列進關係節,徵兆是頓號極多
    #(正常關係節個位數頓號,退化頁上看到 75~194);連結與裸名都算
    if rel.count("、") > 40:
        return True
    from collections import Counter
    counts = Counter(NAME.findall(rel)).most_common()
    # 描述式關係節裡,主角自己的名字會反覆出現(最高頻那個),不算退化;
    # 迴圈退化是「別人的名字」被重複刷屏,看第二高頻即可(正常描述式上限約 5)
    return len(counts) > 1 and counts[1][1] > 8


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
                if not isinstance(fact, str) or NON_EVENT_RE.search(fact):
                    continue
                per_char[canon].append((num, fact))
    return per_char


def make_prompt(canon, aliases, facts):
    fact_lines = "\n".join(f"第{n}回:{fa}" for n, fa in facts)
    names = "、".join(CHARACTERS.keys())
    alias_str = f"(別名:{'、'.join(aliases)})" if aliases else ""
    return (
        f"以下是從《{BOOK_TITLE}》原文逐回摘出的、關於「{canon}」{alias_str}的全部內容,"
        "每條都附回數出處。請據此撰寫 wiki 條目,繁體中文(正體字,嚴禁出現任何簡體字)、Markdown,只輸出三節:\n\n"
        "### 生平概述\n(2-4 段,按時間順序綜述)\n\n"
        "### 重要事蹟\n(條列,每項務必註明回數,內容直接取自下方)\n\n"
        f"### 人物關係\n(條列:{'、'.join(RELATION_KINDS)},只列有依據的)\n\n"
        "嚴格規則:\n"
        "1. 只能根據下方資料撰寫,其中沒有的情節、關係、稱號一律不寫,即使你認為是常識\n"
        "2. 資料不足的節就寫短一點,不要填補\n"
        "3. 親屬稱謂只能照抄資料裡的說法,不得由互動推論\n"
        "   (例如資料只說 A 對 B 說話,不可以推論兩人是夫妻、父子)\n"
        f"4. 提及他人時用 [[人物名]] 格式,僅限這些正名:{names}\n"
        "   不在上列名單中的人名,以及所有地名、物名、書名,一律用純文字,不加 [[]]\n"
        "5. 回數出處一律寫成「(第N回)」的純文字,不可以寫成 [[第N回]] 這種連結\n"
        "6. 直接從人物在書中的身分寫起,不要說明他是小說人物,不要提到作者、朝代、書名\n"
        "7. 這是給讀者看的條目,不是工作報告:輸出中不得出現「清單」「事實清單」「資料」\n"
        "   「記載」「來源」等指涉本次作業過程的字眼,也不得加註記說明某項為何缺漏\n"
        "8. 不要輸出任何前言、結語、註記或自我修正;拿不準的內容直接省略,不要解釋為什麼省略\n\n"
        f"=== 逐回內容 ===\n{fact_lines}"
    )


def main():
    # --force:連已寫過生平的人物頁一併重生成(提示詞改版後全量重跑用)
    force = "--force" in sys.argv
    if force:
        sys.argv.remove("--force")
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10**9
    per_char = load_facts()
    ranked = sorted(per_char, key=lambda c: -len(per_char[c]))[:limit]
    done = skipped = 0
    for canon in ranked:
        page = VAULT / "人物" / f"{canon}.md"
        if not page.exists():
            continue
        text = page.read_text(encoding="utf-8")
        if MARKER in text and not force:
            skipped += 1
            continue
        facts = per_char[canon]
        print(f"composing {canon} ({len(facts)} facts) ...", flush=True)
        try:
            prompt = make_prompt(canon, CHARACTERS[canon], facts)
            bio = call_llm(prompt, max_tokens=4000, extra=PENALTY)
            if (is_degenerate(bio) or has_meta(bio) or has_outside_view(bio)
                    or missing_sections(bio) or find_simplified(bio)):
                print("  degenerate/meta/缺節, retrying ...", flush=True)
                bio = call_llm(prompt, max_tokens=4000, temperature=0.7, extra=PENALTY)
            if is_degenerate(bio):
                bio = dedupe_relations(bio)
                print("  still degenerate, deduped", flush=True)
            if has_meta(bio):
                # 保底:整行剔除仍含碎念的條目
                bio = "\n".join(ln for ln in bio.splitlines() if not META_RE.search(ln))
                print("  still meta, lines dropped", flush=True)
            bio = fix_simplified(remap_alias_links(fix_chapter_refs(fix_links(bio))))
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
