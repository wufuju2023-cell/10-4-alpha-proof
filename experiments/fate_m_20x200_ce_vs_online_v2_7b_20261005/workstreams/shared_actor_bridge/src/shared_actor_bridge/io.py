"""Crash-safe immutable publication for actor envelopes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .contract import validate_unsigned_envelope


def write_immutable_envelope(path: str | Path, envelope: Mapping[str, Any], *, tokenizer: Any | None = None) -> Path:
    target = Path(path).resolve()
    validate_unsigned_envelope(envelope, tokenizer=tokenizer)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"refusing to replace immutable actor envelope: {target}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(envelope), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Exclusive link makes races fail without overwriting an existing file.
        os.link(temporary, target)
        if os.name != "nt":
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return target
