"""Compile authoritative FATE-M JSONL records into Reap search sessions."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SOURCE_BODY = ":= by\n  sorry"
REAP_BODY = ":= by\n  reapTrainingMCTS"


@dataclass(frozen=True)
class SearchOptions:
    num_samples: int = 64
    max_tokens: int = 256
    max_steps: int = 64
    max_goals: int = 64
    temperature_percent: int = 150
    num_premises: int = 0
    visit_discount: int = 990

    def validate(self) -> None:
        positive = ("num_samples", "max_tokens", "max_steps", "max_goals")
        for name in positive:
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        if not 0 <= self.temperature_percent <= 1000:
            raise ValueError("temperature_percent must be between 0 and 1000")
        if self.num_premises < 0:
            raise ValueError("num_premises must be nonnegative")

    def lean_block(self) -> str:
        self.validate()
        return "\n".join(
            [
                'set_option reap.model "REAL-Prover"',
                f"set_option reap.num_premises {self.num_premises}",
                f"set_option reap.num_samples {self.num_samples}",
                f"set_option reap.max_tokens {self.max_tokens}",
                f"set_option reap.temperature {self.temperature_percent}",
                f"set_option reap.max_steps {self.max_steps}",
                f"set_option reap.max_goals {self.max_goals}",
                f"set_option reap.visit_discount {self.visit_discount}",
            ]
        )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compile_source(formal_statement: str, options: SearchOptions) -> str:
    """Make the only permitted source transformation, failing on ambiguity."""
    source = formal_statement.replace("\r\n", "\n")
    if not source.startswith("import Mathlib\n"):
        raise ValueError("formal_statement must start with exactly `import Mathlib`")
    if source.count(SOURCE_BODY) != 1:
        raise ValueError("formal_statement must contain exactly one canonical sorry body")
    source = source.replace("import Mathlib\n", "import ReapRuntime\n", 1)
    source = source.replace(SOURCE_BODY, REAP_BODY, 1)
    return source.replace(
        "import ReapRuntime\n",
        "import ReapRuntime\n\n" + options.lean_block() + "\n",
        1,
    )


def load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
        required = {"id", "family_index", "variant_index", "formal_statement", "sha256"}
        missing = required.difference(record)
        if missing:
            raise ValueError(f"{path}:{line_number}: missing {sorted(missing)}")
        source_id = str(record["id"])
        if source_id in seen:
            raise ValueError(f"{path}:{line_number}: duplicate id {source_id}")
        actual = sha256_text(str(record["formal_statement"]))
        if actual != str(record["sha256"]).lower():
            raise ValueError(f"{path}:{line_number}: source hash mismatch for {source_id}")
        seen.add(source_id)
        records.append(record)
    if not records:
        raise ValueError(f"no records in {path}")
    return records


def select_records(
    records: Iterable[dict],
    *,
    variant_start: int,
    variant_end: int,
    families: set[int] | None,
) -> list[dict]:
    if variant_start < 1 or variant_end < variant_start:
        raise ValueError("invalid inclusive variant range")
    selected = [
        record
        for record in records
        if variant_start <= int(record["variant_index"]) <= variant_end
        and (families is None or int(record["family_index"]) in families)
    ]
    return sorted(selected, key=lambda x: (int(x["variant_index"]), int(x["family_index"])))


def write_sessions(
    records: Iterable[dict],
    output_dir: Path,
    manifest: Path,
    options: SearchOptions,
    policy_base_url: str,
    value_base_url: str,
    problems_sha256: str,
    model_sha256: str,
    *,
    force: bool = False,
) -> int:
    if len(problems_sha256) != 64 or any(c not in "0123456789abcdef" for c in problems_sha256):
        raise ValueError("problems_sha256 must be 64 lowercase hex characters")
    if len(model_sha256) != 64 or any(c not in "0123456789abcdef" for c in model_sha256):
        raise ValueError("model_sha256 must be 64 lowercase hex characters")
    output_dir.mkdir(parents=True, exist_ok=True)
    if manifest.exists() and not force:
        raise FileExistsError(f"refusing to replace manifest: {manifest}")
    lines: list[str] = []
    for record in records:
        source_id = str(record["id"])
        family_index = int(record["family_index"])
        variant_index = int(record["variant_index"])
        family_dir = output_dir / f"P{family_index:02d}"
        family_dir.mkdir(parents=True, exist_ok=True)
        theorem_path = family_dir / f"v{variant_index:03d}.lean"
        if theorem_path.exists() and not force:
            raise FileExistsError(f"refusing to replace theorem: {theorem_path}")
        generated = compile_source(str(record["formal_statement"]), options)
        theorem_path.write_text(generated, encoding="utf-8", newline="\n")
        item = {
            "schema_version": "fate.reap.session.v1",
            "session_id": source_id,
            "source_id": source_id,
            "family_index": family_index,
            "variant_index": variant_index,
            "theorem_file": str(theorem_path.resolve()),
            "source_sha256": str(record["sha256"]).lower(),
            "generated_sha256": sha256_text(generated),
            "problems_sha256": problems_sha256,
            "model_sha256": model_sha256,
            "policy_base_url": policy_base_url.rstrip("/"),
            "value_base_url": value_base_url.rstrip("/"),
            "search_options": asdict(options),
        }
        lines.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
    if not lines:
        raise ValueError("selection produced no sessions")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return len(lines)


def _parse_families(text: str) -> set[int] | None:
    if not text:
        return None
    values = {int(part) for part in text.split(",")}
    if any(value < 1 for value in values):
        raise argparse.ArgumentTypeError("families must be positive integers")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problems", type=Path, required=True)
    parser.add_argument("--expected-problems-sha256", required=True)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--policy-base-url", required=True)
    parser.add_argument("--value-base-url", required=True)
    parser.add_argument("--variant-start", type=int, default=1)
    parser.add_argument("--variant-end", type=int, default=200)
    parser.add_argument("--families", type=_parse_families, default=None)
    parser.add_argument("--num-samples", type=int, default=64)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--max-steps", type=int, default=64)
    parser.add_argument("--max-goals", type=int, default=64)
    parser.add_argument("--temperature-percent", type=int, default=150)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    options = SearchOptions(
        num_samples=args.num_samples,
        max_tokens=args.max_tokens,
        max_steps=args.max_steps,
        max_goals=args.max_goals,
        temperature_percent=args.temperature_percent,
    )
    actual_problems_sha256 = sha256_file(args.problems)
    if actual_problems_sha256 != args.expected_problems_sha256.lower():
        raise SystemExit("top-level problems.jsonl hash mismatch")
    selected = select_records(
        load_records(args.problems),
        variant_start=args.variant_start,
        variant_end=args.variant_end,
        families=args.families,
    )
    count = write_sessions(
        selected,
        args.output_dir,
        args.manifest,
        options,
        args.policy_base_url,
        args.value_base_url,
        actual_problems_sha256,
        args.model_sha256.lower(),
        force=args.force,
    )
    print(json.dumps({"sessions": count, "manifest": str(args.manifest.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
