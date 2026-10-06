from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent / "online_v2_arm" / "src"))
sys.path.insert(0, str(ROOT.parent / "lean_integration" / "src"))
sys.path.insert(0, str(ROOT.parent / "ce_arm" / "src"))
