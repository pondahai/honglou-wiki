# -*- coding: utf-8 -*-
"""exit 0 = 所有有 facts 的人物頁都已寫入生平(run_bios.cmd 的完成檢查)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from compose_bios import load_facts, MARKER
from config import VAULT

missing = [
    c for c in load_facts()
    if (VAULT / "人物" / f"{c}.md").exists()
    and MARKER not in (VAULT / "人物" / f"{c}.md").read_text(encoding="utf-8")
]
print("missing:", len(missing))
sys.exit(1 if missing else 0)
