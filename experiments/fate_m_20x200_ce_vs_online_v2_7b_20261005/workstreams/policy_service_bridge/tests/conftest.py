from __future__ import annotations

import sys
from pathlib import Path


HERE = Path(__file__).resolve()
WORKSTREAMS = HERE.parents[2]
for path in (
    HERE.parents[1] / "src",
    WORKSTREAMS / "shared_actor_bridge" / "src",
):
    sys.path.insert(0, str(path))
