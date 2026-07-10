# -*- coding: utf-8 -*-
"""全書設定:換一本書時,只需要改這個檔 + characters.py。

紅樓夢(程高本 120 回)的回目是上下聯(「第一回 甄士隱夢幻識通靈 賈雨村風塵懷閨秀」),
HEADING regex 有兩個標題群組;build_wiki 會自動串接所有非空群組。
無卷首文字,PREFACE_TITLE 設為 None。
第 100 回起回數用位值寫法(第一零零回~第一二零回),cn2int 已支援。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 書 ──────────────────────────────────────────────
BOOK_TITLE = "紅樓夢"            # 用於 prompt 與網頁標題
N_CHAPTERS = 120                 # 全書回數(解析後檢查用)
RAW = ROOT / "data" / "honglou_raw.txt"     # 原文純文字
EDITION_NOTE = "維基文庫程高本(zh-hant),全 120 回。"  # 首頁副標

# 章回標題:第(中文數字)回 上聯 下聯(fetch 已正規化為半形空格)
# 第 100 回起維基文庫用位值寫法(一零零、一一零、一二零),需含「零」
HEADING = re.compile(r"^第([一二三四五六七八九十百零]+)回\s+(\S+)\s+(\S+)\s*$")

# 卷首文字(第一回之前)的頁面標題;無卷首的書設為 None
PREFACE_TITLE = None

# ── 路徑 ────────────────────────────────────────────
VAULT = ROOT / "vault"
SITE = ROOT / "site"
FACTS = ROOT / "data" / "facts"

# ── LLM 端點(OpenAI 相容)──────────────────────────
API = "http://100.89.149.50:8002/v1/chat/completions"
MODEL = "nvidia/Qwen3.6-35B-A3B-NVFP4"
