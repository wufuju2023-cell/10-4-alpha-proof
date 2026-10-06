#!/usr/bin/env python3
"""Fetch the pinned FATE-M JSON and retain only the frozen 20 source problems."""

from __future__ import annotations

import hashlib
import json
import pathlib
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "selection.json"
OUTPUT_PATH = ROOT / "data" / "originals.json"


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    source = config["source"]
    commit = source["submodule_commit"]
    url = f"https://raw.githubusercontent.com/frenzymath/FATE-M/{commit}/FATE-M.json"

    with urllib.request.urlopen(url, timeout=60) as response:
        raw = response.read()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != source["json_sha256"]:
        raise SystemExit(
            f"FATE-M.json hash mismatch: expected {source['json_sha256']}, got {digest}"
        )

    records = json.loads(raw.decode("utf-8-sig"))
    by_id = {int(record["id"]): record for record in records}
    selected_ids = config["selected_fate_ids"]
    missing = [problem_id for problem_id in selected_ids if problem_id not in by_id]
    if missing:
        raise SystemExit(f"Pinned FATE-M JSON is missing selected ids: {missing}")

    payload = {
        "source": source,
        "selected_fate_ids": selected_ids,
        "problems": [by_id[problem_id] for problem_id in selected_ids],
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUTPUT_PATH} ({len(selected_ids)} problems)")


if __name__ == "__main__":
    main()
