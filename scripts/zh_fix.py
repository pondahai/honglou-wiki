# -*- coding: utf-8 -*-
"""簡體字防線:LLM 輸出中「原文裡不存在、且 OpenCC 可轉換」的字視為簡體亂入,
以 s2tw(台灣標準,片語級)只轉換含這些字的行。無 opencc 時降級為不動作。
"""
from pathlib import Path

try:
    from opencc import OpenCC
    _s2t, _s2tw = OpenCC("s2t"), OpenCC("s2tw")
except ImportError:
    _s2t = _s2tw = None

_ROOT = Path(__file__).resolve().parent.parent
_NL = chr(10)
_rawset = None


def _load():
    global _rawset
    if _rawset is None:
        _rawset = set()
        for p in (_ROOT / "data").glob("*_raw.txt"):
            _rawset |= set(p.read_text(encoding="utf-8"))
    return _rawset


def find_simplified(text):
    """回傳 text 中疑似簡體的字(原文出現 0 次且可轉換);無 opencc 回傳空"""
    if _s2t is None:
        return []
    rs = _load()
    return [c for c in set(text)
            if "一" <= c <= "鿿" and c not in rs
            and (_s2t.convert(c) != c or _s2tw.convert(c) != c)]


def fix_simplified(text):
    """只對含疑似簡體字的行做 s2tw 整行轉換,其餘行原樣保留"""
    if _s2tw is None:
        return text
    bad = set(find_simplified(text))
    if not bad:
        return text
    return _NL.join(_s2tw.convert(ln) if (set(ln) & bad) else ln
                    for ln in text.split(_NL))
