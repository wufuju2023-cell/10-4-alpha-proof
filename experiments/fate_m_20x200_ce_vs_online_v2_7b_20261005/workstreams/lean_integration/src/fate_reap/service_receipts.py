"""Strict validation for per-request policy/value service receipts."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ServiceAudit:
    valid: bool
    total: int
    policy_requests: int
    value_requests: int
    failed_requests: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def audit_service_receipts(
    path: Path,
    *,
    session_id: str,
    tree_id: str,
    expected_model_sha256: str,
    expected_policy_requests: int,
    expected_value_requests: int,
) -> ServiceAudit:
    if not path.is_file():
        return ServiceAudit(False, 0, 0, 0, 0, "missing service_requests.jsonl")
    total = policy = value = failed = 0
    request_ids: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return ServiceAudit(False, 0, 0, 0, 0, f"cannot read service receipts: {exc}")
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return ServiceAudit(False, total, policy, value, failed, f"malformed service receipt at line {number}")
        total += 1
        if not isinstance(record, dict) or record.get("schema_version") != "fate.service.request.v1":
            return ServiceAudit(False, total, policy, value, failed, f"invalid service receipt schema at line {number}")
        typed = {
            "request_id": str, "session_id": str, "tree_id": str, "service": str,
            "status": str, "parse_status": str, "model_sha256": str,
            "request_sha256": str, "response_sha256": str,
        }
        if any(type(record.get(field)) is not expected for field, expected in typed.items()):
            return ServiceAudit(False, total, policy, value, failed, f"invalid typed field at line {number}")
        if type(record.get("step")) is not int or record["step"] < 0:
            return ServiceAudit(False, total, policy, value, failed, f"invalid step at line {number}")
        if type(record.get("policy_version")) is not int or record["policy_version"] < 0:
            return ServiceAudit(False, total, policy, value, failed, f"invalid policy_version at line {number}")
        if type(record.get("terminal")) is not bool or not record["terminal"]:
            return ServiceAudit(False, total, policy, value, failed, f"request lacks terminal receipt at line {number}")
        if record["session_id"] != session_id or record["tree_id"] != tree_id:
            return ServiceAudit(False, total, policy, value, failed, f"session/tree mismatch at line {number}")
        if record["request_id"] in request_ids:
            return ServiceAudit(False, total, policy, value, failed, f"duplicate request_id at line {number}")
        request_ids.add(record["request_id"])
        if record["service"] == "policy":
            policy += 1
        elif record["service"] == "value":
            value += 1
        else:
            return ServiceAudit(False, total, policy, value, failed, f"unknown service at line {number}")
        if any(not SHA256_RE.fullmatch(record[field]) for field in ("model_sha256", "request_sha256", "response_sha256")):
            return ServiceAudit(False, total, policy, value, failed, f"invalid content hash at line {number}")
        if record["model_sha256"] != expected_model_sha256:
            return ServiceAudit(False, total, policy, value, failed, f"model hash mismatch at line {number}")
        http_status = record.get("http_status")
        choice_count = record.get("choice_count")
        ok = (
            record["status"] == "ok" and record["parse_status"] == "ok"
            and type(http_status) is int and 200 <= http_status < 300
            and type(choice_count) is int and choice_count > 0
        )
        if not ok:
            failed += 1
    if total == 0:
        return ServiceAudit(False, 0, 0, 0, 0, "no terminal service receipts")
    if failed:
        return ServiceAudit(False, total, policy, value, failed, "one or more policy/value service requests failed")
    if policy != expected_policy_requests or value != expected_value_requests:
        return ServiceAudit(
            False, total, policy, value, failed,
            f"service/log count mismatch: policy {policy}!={expected_policy_requests}, value {value}!={expected_value_requests}",
        )
    return ServiceAudit(True, total, policy, value, 0, "all issued service requests have successful terminal receipts")
