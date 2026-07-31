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
from build_wiki import EXCLUDE_RE, build_alias_map
from characters import CHARACTERS
from config import BOOK_TITLE, VAULT, FACTS, RELATION_KINDS
from extract_facts import call_llm
from zh_fix import find_simplified, fix_simplified

MARKER = "<!-- source: map-reduce facts -->"
SECTION = re.compile(r"(## 生平\n).*?(\n## 出場章回)", re.S)
NAME = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")

BASE_TOKENS = 8000    # 起始 budget
MAX_TOKENS = 24000    # 重試上限;每次截斷就加倍
ENDINGS = "。）)」』】.!?！？"  # 正常收尾的標點
SFN = re.compile(r"(?:\s*\{\{sfn\|[^}]*\}\})+\s*$")  # 行尾出處標記,判斷收尾時先剝掉
# 取樣參數。原為 {"repetition_penalty": 1.08, "presence_penalty": 0.5},只移除前者:
#
# repetition_penalty 1.08 偏離官方——Qwen3.6-35B-A3B 的 model card 在思考/非思考
# 三種模式一律建議 1.0(即不使用),故拿掉改用預設值。這是「對齊官方文件」的改動,
# 不是實測結論:我們確實觀察過一次推理鏈暴衝(56586 字、產出 0 字),但相同設定
# 重跑並未重現(15445 字、正常產出),那是單次離群值,不足以歸因於任何參數。
#
# presence_penalty 0.5 保留:它落在官方建議範圍內(一般任務 1.5、精確任務 0.0),
# 且沒有任何可信證據支持更動(見下方警告)。
#
# ⚠ 本模型的 run-to-run 變異極大:同一頁同一設定,思考長度可差 3.7 倍。
#   任何「每格跑一次」的參數比較都不可信,要調參請每格跑 3-5 次再看。
# 註:temperature 0.2 低於官方建議的 0.6,係刻意壓低創造性,未一併更動。
SAMPLING = {"presence_penalty": 0.5}


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


ALIAS_MAP, ALIAS_RE = build_alias_map()
EXISTING_LINK = re.compile(r"\[\[[^\]]*\]\]")


def autolink(bio, canon):
    """補上人名連結:模型時常整篇忘了加 [[ ]](提示詞規則 4 形同虛設,約四分之一
    的頁面零連結),與其反覆重試不如事後補。每行首次出現才連,已是連結的區段、
    EXCLUDE_PHRASES 的同形處、以及人物自己的名字都跳過。"""
    def link_line(line):
        if line.lstrip().startswith("#"):
            return line
        blocked = [m.span() for m in EXISTING_LINK.finditer(line)]
        if EXCLUDE_RE:
            blocked += [m.span() for m in EXCLUDE_RE.finditer(line)]
        # 本行已連過的名字不再連第二次(否則同一行會出現兩個連結)
        linked = set(NAME.findall(line))

        def repl(m):
            word = m.group(0)
            if any(s <= m.start() < e for s, e in blocked):
                return word
            target = ALIAS_MAP[word]
            if target == canon or target in linked:
                return word
            linked.add(target)
            return f"[[{target}]]" if word == target else f"[[{target}|{word}]]"

        return ALIAS_RE.sub(repl, line)

    # keepends 保留原本的換行,不動空行與結尾
    return "".join(link_line(ln) for ln in bio.splitlines(keepends=True))


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


def relink_all():
    """只跑 autolink 補既有生平節的連結,不呼叫 LLM(補連結上線後的一次性修補)"""
    changed = 0
    for page in sorted((VAULT / "人物").glob("*.md")):
        text = page.read_text(encoding="utf-8")

        def repl(m, canon=page.stem):
            body = m.group(0)[len(m.group(1)):-len(m.group(2))]
            return m.group(1) + autolink(body, canon) + m.group(2)

        new = SECTION.sub(repl, text, count=1)
        if new != text:
            page.write_text(new, encoding="utf-8")
            changed += 1
            print(f"  relinked {page.stem}", flush=True)
    print(f"relinked={changed}")


def looks_truncated(bio):
    """文字面的截斷徵兆:缺人物關係節(輸出在事蹟中途就斷了),
    或事蹟節最後一條停在句中(沒有收尾標點)。
    人物關係節的條目常以 [[名字]] 收尾、本來就沒有句號,故只看事蹟節。"""
    if not bio.strip():
        return True
    head, sep, _ = bio.partition("### 人物關係")
    if not sep:
        return True
    lines = [SFN.sub("", ln).rstrip() for ln in head.splitlines() if ln.strip()]
    lines = [ln for ln in lines if ln]
    return bool(lines) and lines[-1][-1] not in ENDINGS


def current_bio(text):
    """取出頁面中已生成的生平內容(不含 ## 標題與 marker)"""
    m = SECTION.search(text)
    return m.group(0)[len(m.group(1)):-len(m.group(2))].replace(MARKER, "").strip() if m else ""


def generate(prompt, temperature=0.2):
    """撞到 max_tokens 就加倍 budget 重試,避免長人物被硬切在句子中間"""
    budget = BASE_TOKENS
    while True:
        bio, finish = call_llm(prompt, max_tokens=budget, temperature=temperature,
                               extra=SAMPLING, return_finish=True)
        if finish != "length" and not looks_truncated(bio):
            return bio
        if budget >= MAX_TOKENS:
            print(f"  WARN: 仍被截斷(budget={budget}, finish={finish}),輸出可能不完整", flush=True)
            return bio
        budget = min(budget * 2, MAX_TOKENS)
        print(f"  truncated (finish={finish}), retrying with max_tokens={budget} ...", flush=True)


def main():
    if "--redo-truncated" in sys.argv:
        redo_truncated = True
        sys.argv.remove("--redo-truncated")
    else:
        redo_truncated = False
    only = None
    if "--only" in sys.argv:
        i = sys.argv.index("--only")
        only = set(sys.argv[i + 1:])
        del sys.argv[i:]
    # --relink:不重生成,只把 autolink 補到既有頁面
    if "--relink" in sys.argv:
        relink_all()
        return
    # --force:連已寫過生平的人物頁一併重生成(提示詞改版後全量重跑用)
    force = "--force" in sys.argv
    if force:
        sys.argv.remove("--force")
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10**9
    per_char = load_facts()
    ranked = sorted(per_char, key=lambda c: -len(per_char[c]))[:limit]
    done = skipped = 0
    for canon in ranked:
        if only is not None and canon not in only:
            continue
        page = VAULT / "人物" / f"{canon}.md"
        if not page.exists():
            continue
        text = page.read_text(encoding="utf-8")
        if MARKER in text and not force and only is None:
            if not (redo_truncated and looks_truncated(current_bio(text))):
                skipped += 1
                continue
            print(f"  {canon}: 偵測到截斷,重新生成", flush=True)
        facts = per_char[canon]
        print(f"composing {canon} ({len(facts)} facts) ...", flush=True)
        try:
            prompt = make_prompt(canon, CHARACTERS[canon], facts)
            bio = generate(prompt)
            if (is_degenerate(bio) or has_meta(bio) or has_outside_view(bio)
                    or missing_sections(bio) or find_simplified(bio)):
                print("  degenerate/meta/缺節, retrying ...", flush=True)
                bio = generate(prompt, temperature=0.7)
            if is_degenerate(bio):
                bio = dedupe_relations(bio)
                print("  still degenerate, deduped", flush=True)
            if has_meta(bio):
                # 保底:整行剔除仍含碎念的條目
                bio = "\n".join(ln for ln in bio.splitlines() if not META_RE.search(ln))
                print("  still meta, lines dropped", flush=True)
            bio = fix_simplified(remap_alias_links(fix_chapter_refs(fix_links(bio))))
            bio = autolink(bio, canon)
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
