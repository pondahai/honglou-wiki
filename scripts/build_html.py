# -*- coding: utf-8 -*-
"""vault/ 的 Markdown → site/ 靜態 HTML 網站(零相依,瀏覽器直接開)"""
import html
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import BOOK_TITLE, VAULT, SITE

CSS = """
:root { --bg:#fff; --fg:#222; --link:#8b2500; --muted:#888; --line:#ddd; --card:#f7f5f0; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#1a1a1e; --fg:#ddd; --link:#e8a87c; --muted:#777; --line:#333; --card:#242428; }
}
* { box-sizing:border-box; }
body { margin:0 auto; max-width:46em; padding:1.5em 1em 4em; background:var(--bg); color:var(--fg);
  font-family:"Noto Serif TC","PMingLiU",serif; line-height:1.9; font-size:1.05rem; }
h1,h2,h3 { font-family:"Noto Sans TC","Microsoft JhengHei",sans-serif; line-height:1.4; }
h1 { border-bottom:2px solid var(--line); padding-bottom:.3em; }
a { color:var(--link); text-decoration:none; }
a:hover { text-decoration:underline; }
a.missing { color:var(--muted); border-bottom:1px dotted var(--muted); cursor:default; }
hr { border:0; border-top:1px solid var(--line); margin:2em 0; }
nav.top { font-family:"Noto Sans TC",sans-serif; font-size:.9rem; margin-bottom:1em;
  padding:.5em .8em; background:var(--card); border-radius:8px; }
ul { padding-left:1.4em; }
.search { width:100%; padding:.5em .8em; font-size:1rem; margin:.5em 0 1em;
  border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--fg); }
"""

SEARCH_JS = """
function filterList(q) {
  q = q.trim();
  document.querySelectorAll('ul.filterable li').forEach(li => {
    li.style.display = !q || li.textContent.includes(q) ? '' : 'none';
  });
}
"""


def collect_pages():
    """回傳 {頁面名(不含.md): 相對路徑(不含副檔名)}"""
    pages = {}
    for md in VAULT.rglob("*.md"):
        rel = md.relative_to(VAULT).with_suffix("")
        pages[md.stem] = rel.as_posix()
    return pages


def md_to_html(text, pages, depth):
    """極簡 Markdown 轉換:標題/清單/粗體/wiki連結/分隔線/段落"""
    prefix = "../" * depth

    def wikilink(m):
        target, _, label = m.group(1).partition("|")
        label = label or target
        if target in pages:
            href = prefix + urllib.parse.quote(pages[target]) + ".html"
            return f'<a href="{href}">{html.escape(label)}</a>'
        return f'<a class="missing" title="尚無此條目">{html.escape(label)}</a>'

    out = []
    in_list = False
    for line in text.splitlines():
        s = line.strip()
        esc = html.escape(s)
        esc = re.sub(r"\[\[([^\]]+)\]\]", lambda m: wikilink(m), esc)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
        is_li = s.startswith(("- ", "* ")) or re.match(r"^\*\s{2,}", s)
        if in_list and not is_li:
            out.append("</ul>")
            in_list = False
        if not s or s.startswith("<!--"):  # HTML 註解(如來源標記)不輸出
            continue
        if s == "---":
            out.append("<hr>")
        elif s.startswith("### "):
            out.append(f"<h3>{esc[4:]}</h3>")
        elif s.startswith("## "):
            out.append(f"<h2>{esc[3:]}</h2>")
        elif s.startswith("# "):
            out.append(f"<h1>{esc[2:]}</h1>")
        elif is_li:
            if not in_list:
                cls = ' class="filterable"' if depth == 0 else ""
                out.append(f"<ul{cls}>")
                in_list = True
            out.append("<li>" + re.sub(r"^[-*]\s+", "", esc) + "</li>")
        else:
            out.append(f"<p>{esc}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def wrap(title, body, depth, searchable=False):
    prefix = "../" * depth
    search = (
        f'<input class="search" placeholder="篩選…" oninput="filterList(this.value)">'
        f"<script>{SEARCH_JS}</script>"
    ) if searchable else ""
    return (
        f'<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)} - {html.escape(BOOK_TITLE)}</title><style>{CSS}</style></head><body>"
        f'<nav class="top"><a href="{prefix}index.html">首頁</a> · '
        f'<a href="{prefix}%E5%9B%9E%E7%9B%AE%E7%B4%A2%E5%BC%95.html">回目索引</a> · '
        f'<a href="{prefix}%E4%BA%BA%E7%89%A9%E7%B4%A2%E5%BC%95.html">人物索引</a></nav>'
        f"{search}{body}</body></html>"
    )


def main():
    pages = collect_pages()
    for md in VAULT.rglob("*.md"):
        rel = md.relative_to(VAULT).with_suffix("")
        depth = len(rel.parts) - 1
        body = md_to_html(md.read_text(encoding="utf-8"), pages, depth)
        out = SITE / rel.parent / (rel.stem + ".html")
        out.parent.mkdir(parents=True, exist_ok=True)
        searchable = md.stem in ("人物索引", "回目索引")
        out.write_text(wrap(md.stem, body, depth, searchable), encoding="utf-8")
    # 首頁副本為 index.html
    home = SITE / "首頁.html"
    if home.exists():
        (SITE / "index.html").write_text(home.read_text(encoding="utf-8"), encoding="utf-8")
    n = len(list(SITE.rglob("*.html")))
    print(f"{n} pages -> {SITE}")


if __name__ == "__main__":
    main()
