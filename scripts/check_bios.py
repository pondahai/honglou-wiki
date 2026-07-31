# -*- coding: utf-8 -*-
"""體檢 vault/人物/*.md 的生平內文是否被 LLM 的 max_tokens 截斷。

事實越多的人物(主角)生平越長,最容易撞上 budget 上限而被硬切在句子中間,
所以輸出依「事實數」由多到少排序 —— 排前面的請優先重生成。

用法:
  python check_bios.py            # 列出有問題的人物
  python check_bios.py --all      # 連同正常的一起列,附事實數與字數
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from compose_bios import MARKER, VAULT, current_bio, load_facts, looks_truncated


def main():
    show_all = "--all" in sys.argv
    per_char = load_facts()
    rows = []
    for page in sorted((VAULT / "人物").glob("*.md")):
        text = page.read_text(encoding="utf-8")
        n_facts = len(per_char.get(page.stem, []))
        if MARKER not in text:
            rows.append((n_facts, page.stem, "未生成", 0))
            continue
        bio = current_bio(text)
        bad = looks_truncated(bio)
        if bad or show_all:
            rows.append((n_facts, page.stem, "截斷" if bad else "ok", len(bio)))

    rows.sort(key=lambda r: -r[0])
    print(f"人物生平體檢:{len(rows)} 筆(依事實數排序)\n")
    for n_facts, canon, status, size in rows:
        print(f"  {status:4}  {canon:10} 事實 {n_facts:4} 條 / 生平 {size:5} 字")

    broken = [r[1] for r in rows if r[2] == "截斷"]
    if broken:
        print(f"\n共 {len(broken)} 人被截斷,重生成:")
        print("  python compose_bios.py --only " + " ".join(broken))
    else:
        print("\n沒有偵測到截斷。")


if __name__ == "__main__":
    main()
