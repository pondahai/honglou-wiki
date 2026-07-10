# -*- coding: utf-8 -*-
"""Map 階段:逐回讓 LLM 全文閱讀,抽取每個人物的結構化事實。
輸出 data/facts/ch_NNN.json;可重跑,已有的回自動跳過。
用法: python extract_facts.py [起始回] [結束回]
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from characters import CHARACTERS
from config import API, MODEL, BOOK_TITLE, N_CHAPTERS, RAW, FACTS
from zh_fix import fix_simplified
from build_wiki import parse_chapters, reflow, build_alias_map


def call_llm(prompt, max_tokens=6000, temperature=0.2, extra=None):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    payload.update(extra or {})
    body = json.dumps(payload).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        out = json.load(r)["choices"][0]["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()


def parse_json(raw):
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.M)
    start, end = raw.find("{"), raw.rfind("}")
    return json.loads(raw[start : end + 1], strict=False)  # 容忍字串內的換行等控制字元


def make_prompt(num, title, text, present):
    names = "、".join(present)
    return (
        f"以下是《{BOOK_TITLE}》第{num}回「{title}」的完整正文。請通讀全文,"
        f"為下列每位在本回登場的人物,抽取本回中關於他的重要事實(情節行動、職位變動、"
        "結盟或反目、勝敗、死亡等),每條一句話、具體明確。\n\n"
        "嚴格規則:\n"
        "0. 所有輸出一律使用繁體中文(正體字),嚴禁出現任何簡體字\n"
        "1. 只能根據本回正文,一個字都不可以引入正文之外的知識\n"
        "2. 只在本回無關緊要、僅被提及名字的人物,輸出空陣列\n"
        "3. 事實必須是該人物「本人」的言行;別人的言行、或不確定主語是誰的,一律不寫\n"
        "   (特別注意:正文泛稱「菩薩」「大仙」「老者」等時,先確認指的是誰,對不上的不寫)\n"
        f"4. 只輸出 JSON,格式:{{\"人物名\": [\"事實\", ...], ...}},鍵只能是:{names}\n\n"
        f"=== 第{num}回正文 ===\n{text}"
    )


def main():
    # --only <名單檔>:只抽名單中的人物,結果另存 ch_NNN.<檔名>.json(增量擴充用,不動舊事實)
    only, suffix = None, ""
    if "--only" in sys.argv:
        i = sys.argv.index("--only")
        only_file = Path(sys.argv[i + 1])
        only = set(only_file.read_text(encoding="utf-8").split())
        suffix = "." + only_file.stem
        del sys.argv[i : i + 2]
    lo = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else N_CHAPTERS
    FACTS.mkdir(exist_ok=True)
    chapters = parse_chapters(RAW.read_text(encoding="utf-8"))
    amap, pattern = build_alias_map()
    failed = []
    for num, title, body in chapters:
        if not (lo <= num <= hi):
            continue
        out = FACTS / f"ch_{num:03d}{suffix}.json"
        if out.exists():
            continue
        text = "\n".join(reflow(body))
        present = sorted({amap[m] for m in pattern.findall(text)})
        if only is not None:
            present = sorted(set(present) & only)
            if not present:
                out.write_text("{}", encoding="utf-8")
                continue
        print(f"ch {num} ({len(text)} chars, {len(present)} chars present) ...", flush=True)
        try:
            raw = call_llm(make_prompt(num, title, text, present))
            data = parse_json(raw)
            # LLM 偶爾把值輸出成單一字串而非清單,一律正規化
            data = {k: ([v] if isinstance(v, str) else v) for k, v in data.items() if k in CHARACTERS and v}
            data = {k: [fix_simplified(x) for x in v] for k, v in data.items()}
        except Exception as e:
            print(f"  FAILED ch{num}: {e}", flush=True)
            (FACTS / f"ch_{num:03d}.err").write_text(str(e), encoding="utf-8")
            failed.append(num)
            continue
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  ok: {sum(len(v) for v in data.values())} facts / {len(data)} chars", flush=True)
    print("failed:", failed if failed else "none")


if __name__ == "__main__":
    main()
