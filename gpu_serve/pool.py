"""``gpu-serve pool``：GPU 训练池管理（pull / push / merge-logprobs / stats）。

冻结命令面见 ``docs/architecture/core-and-branches.md`` §1；池子三层与单向同步
见 §2；shard + manifest 格式见 ``docs/new-python-version-mcts-plan/05`` §1/§3。

命令::

    gpu-serve pool pull  --source <hf-repo|local-dir> --manifest <path> --dest <dir>
    gpu-serve pool push  --src <dir> --repo <hf-repo> [--dry-run]
    gpu-serve pool merge-logprobs --pool <dir> --logprobs <jsonl> [--out ...]
    gpu-serve pool stats --pool <dir>

边界：本模块只做「池子 I/O + 校验 + 回填」，不 import ``lmc``、不改 CE/MCTS；
merge-logprobs 的 join 语义对齐 ``lmc/store.py``（本仓库独立实现）：

- join 键 ``(search_id, state_hash, action)``；
- search_id 解析：``search_id`` → ``extra.search_id`` → ``ref.search_id`` →
  ``extra.ref.search_id``（``extra.attempt_id`` 作为另存的回退键，FATE 证据用）；
- state hash：``extra.state_hash`` → ``extra.state_before_sha256`` → 顶层
  ``state_hash`` / ``state_before_sha256`` → ``extra.selected_path_step.state_before_sha256``；
- 两侧都缺 search_id 时仍按 ``(None, state_hash, action)`` join，报告里计入
  ``joined_without_search_id``（去重提示）。

pull/push 的网络实现优先 ``huggingface_hub``，缺失时回退 ``hf`` CLI；token 只从
环境变量 ``HF_TOKEN`` 读（不落盘、不回显；未设置时由 huggingface_hub 走匿名/缓存登录）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional

DEFAULT_REPO_TYPE = "dataset"
_MANIFEST_NAME = "manifest.json"


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def count_lines(path: str | os.PathLike[str]) -> int:
    n = 0
    with open(path, "rb") as fh:
        for _ in fh:
            n += 1
    return n


def read_jsonl(path: str | os.PathLike[str]) -> list[dict]:
    """逐行读 JSONL（保留全部字段；解析失败直接报错，不静默丢行）。"""
    rows: list[dict] = []
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"pool: {p}:{lineno} JSON 解析失败: {exc}") from exc
            if not isinstance(row, dict):
                raise SystemExit(f"pool: {p}:{lineno} 不是 JSON 对象")
            rows.append(row)
    return rows


def _write_jsonl(path: str | os.PathLike[str], rows: Iterable[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


def _write_json(path: str | os.PathLike[str], obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, p)


def resolve_pool_files(path: str | os.PathLike[str]) -> list[Path]:
    """池文件解析：目录（``shard-*.jsonl`` 优先，否则 ``*.jsonl``）或单个文件。"""
    p = Path(path)
    if p.is_dir():
        files = sorted(p.glob("shard-*.jsonl"))
        if not files:
            files = sorted(f for f in p.glob("*.jsonl") if not f.name.endswith(".tmp"))
        if not files:
            raise SystemExit(f"pool: 目录里没有 *.jsonl: {p}")
        return files
    if p.is_file():
        return [p]
    raise SystemExit(f"pool: 池不存在: {p}")


def _copy_into(src: str | os.PathLike[str], dst: Path) -> None:
    """临时文件 + ``os.replace`` 原子落盘（跨文件系统时 copyfile 到同目录）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    shutil.copyfile(str(src), str(tmp))
    os.replace(tmp, dst)


def _token_from_env() -> Optional[str]:
    token = os.environ.get("HF_TOKEN")
    return token or None


def _token_source() -> str:
    return "HF_TOKEN env" if os.environ.get("HF_TOKEN") else "匿名 / 缓存登录（HF_TOKEN 未设置）"


# --------------------------------------------------------------------------- #
# manifest：加载与校验
# --------------------------------------------------------------------------- #
def load_manifest(path: str | os.PathLike[str]) -> dict:
    """读 manifest 并做结构性检查（不触网）。"""
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"pool: manifest 不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"pool: manifest JSON 解析失败 {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"pool: manifest 不是 JSON 对象: {p}")
    shards = data.get("shards")
    if not isinstance(shards, list) or not shards:
        raise SystemExit(f"pool: manifest 缺 shards[]: {p}")
    for i, entry in enumerate(shards):
        if not isinstance(entry, dict) or not entry.get("file"):
            raise SystemExit(f"pool: manifest shards[{i}] 缺 file: {p}")
    return data


def shard_expected_count(entry: dict) -> Optional[int]:
    """manifest 的条目计数：兼容 ``count``（05 规格）与 ``lines``（发布规格）。"""
    for key in ("count", "lines"):
        if entry.get(key) is not None:
            return int(entry[key])
    return None


def verify_shard_file(path: str | os.PathLike[str], entry: dict) -> dict:
    """核对单个 shard 的 sha256 / 计数 / 字节数；返回人读 report dict。"""
    p = Path(path)
    report: dict = {
        "file": str(entry.get("file")),
        "ok": True,
        "errors": [],
        "warnings": [],
    }
    if not p.is_file():
        report["ok"] = False
        report["errors"].append(f"shard 不存在: {p}")
        return report
    actual_sha = sha256_file(p)
    actual_count = count_lines(p)
    actual_bytes = p.stat().st_size
    report.update({"sha256": actual_sha, "count": actual_count, "bytes": actual_bytes})
    expect_sha = entry.get("sha256")
    if expect_sha:
        if actual_sha != str(expect_sha):
            report["ok"] = False
            report["errors"].append(
                f"sha256 不一致（manifest {str(expect_sha)[:12]}…, 实际 {actual_sha[:12]}…）"
            )
    else:
        report["warnings"].append("manifest 无 sha256，未校验内容哈希")
    expect_count = shard_expected_count(entry)
    if expect_count is None:
        report["warnings"].append("manifest 无 count/lines，未校验行数")
    elif actual_count != expect_count:
        report["ok"] = False
        report["errors"].append(f"行数不一致（manifest {expect_count}, 实际 {actual_count}）")
    expect_bytes = entry.get("bytes")
    if expect_bytes is not None and int(expect_bytes) != actual_bytes:
        report["ok"] = False
        report["errors"].append(f"字节数不一致（manifest {int(expect_bytes)}, 实际 {actual_bytes}）")
    return report


def check_manifest_totals(manifest: dict, shard_reports: list[dict]) -> list[str]:
    """对照 ``counts``：transitions 合计与 shards 数（有则校验）。"""
    errors: list[str] = []
    counts = manifest.get("counts") or {}
    total = sum(int(r.get("count") or 0) for r in shard_reports)
    if counts.get("transitions") is not None and int(counts["transitions"]) != total:
        errors.append(
            f"counts.transitions={counts['transitions']} 与 shard 行数合计 {total} 不一致"
        )
    n_shards = len(manifest.get("shards") or [])
    if counts.get("shards") is not None and int(counts["shards"]) != n_shards:
        errors.append(f"counts.shards={counts['shards']} 与 shards[] 条数 {n_shards} 不一致")
    return errors


def verify_manifest_dir(manifest_path: str | os.PathLike[str]) -> dict:
    """校验一个 manifest 及其同目录下的全部 shard（push 前 / 审计用）。"""
    mp = Path(manifest_path)
    manifest = load_manifest(mp)
    base = mp.parent
    reports: list[dict] = []
    for entry in manifest["shards"]:
        report = verify_shard_file(base / str(entry["file"]), entry)
        reports.append(report)
    errors = [f"{r['file']}: {e}" for r in reports for e in r["errors"]]
    errors += check_manifest_totals(manifest, reports)
    return {
        "manifest": str(mp),
        "ok": not errors,
        "shards": reports,
        "errors": errors,
        "schema": manifest.get("schema") or manifest.get("schema_version"),
        "counts": manifest.get("counts"),
        "versions": manifest.get("versions"),
        "policy_version": manifest.get("policy_version"),
    }


# --------------------------------------------------------------------------- #
# HF 网络后端（pull 下载 / push 上传）
# --------------------------------------------------------------------------- #
def _hf_cli() -> Optional[str]:
    exe = shutil.which("hf") or str(Path.home() / ".local" / "bin" / "hf")
    return exe if Path(exe).exists() else None


def download_hf_file(
    repo: str,
    filename: str,
    repo_type: str,
    token: Optional[str],
    dst: str | os.PathLike[str],
) -> None:
    """从 HF 仓库下载单个文件到 ``dst``（优先 huggingface_hub，回退 hf CLI）。"""
    errors: list[str] = []
    try:
        from huggingface_hub import hf_hub_download

        cached = hf_hub_download(
            repo_id=repo, filename=filename, repo_type=repo_type, token=token
        )
        _copy_into(cached, Path(dst))
        return
    except ImportError as exc:
        errors.append(f"huggingface_hub 不可用（{exc}）")
    except Exception as exc:  # noqa: BLE001 - 记录后回退 CLI
        errors.append(f"huggingface_hub 下载失败（{exc}）")

    hf = _hf_cli()
    if hf is None:
        errors.append("hf CLI 不可用")
        raise SystemExit("pool: 下载失败：" + "；".join(errors))

    with tempfile.TemporaryDirectory(prefix="gpu-serve-hf-") as td:
        cmd = [hf, "download", repo, filename, "--repo-type", repo_type, "--local-dir", td]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            tail = (exc.stderr or exc.stdout or "").strip().splitlines()[-3:]
            errors.append(f"hf CLI 下载失败（exit {exc.returncode}: {' | '.join(tail)}）")
            raise SystemExit("pool: 下载失败：" + "；".join(errors)) from exc
        src = Path(td) / filename
        if not src.is_file():
            matches = list(Path(td).rglob(Path(filename).name))
            if not matches:
                raise SystemExit(f"pool: hf CLI 未产出文件: {filename}")
            src = matches[0]
        _copy_into(src, Path(dst))


def _upload_files(repo: str, repo_type: str, plan: list[tuple[Path, str]], token: Optional[str]) -> str:
    """把 ``plan``（本地路径 → 仓库内路径）逐个上传；返回后端名。

    tests 通过 monkeypatch 本函数避免触网。
    """
    try:
        from huggingface_hub import HfApi

        api = HfApi(token=token)
        api.create_repo(repo_id=repo, repo_type=repo_type, private=False, exist_ok=True)
        for local, repo_path in plan:
            api.upload_file(
                path_or_fileobj=str(local),
                path_in_repo=repo_path,
                repo_id=repo,
                repo_type=repo_type,
                commit_message=f"upload {repo_path}",
            )
        return "huggingface_hub"
    except ImportError:
        pass

    hf = _hf_cli()
    if hf is None:
        raise SystemExit("pool: 上传需要 huggingface_hub 或 hf CLI（都没有找到）")
    env = dict(os.environ)
    try:
        subprocess.run(
            [hf, "repos", "create", repo, "--type", repo_type, "--public", "--exist-ok"],
            check=True, env=env,
        )
    except subprocess.CalledProcessError:
        subprocess.run(
            [hf, "repo", "create", repo, "--type", repo_type, "--public", "--exist-ok"],
            check=True, env=env,
        )
    for local, repo_path in plan:
        subprocess.run(
            [hf, "upload", repo, str(local), repo_path, "--type", repo_type,
             "--commit-message", f"upload {repo_path}"],
            check=True, env=env,
        )
    return "hf CLI"


# --------------------------------------------------------------------------- #
# pull
# --------------------------------------------------------------------------- #
def _looks_like_hf_repo(source: str) -> bool:
    if source.startswith(("/", "./", "../", "~")):
        return False
    return source.count("/") == 1 and " " not in source


def _resolve_local_manifest(source: Path, manifest_arg: str) -> Path:
    m = Path(manifest_arg)
    if not m.is_absolute():
        cand = source / m
        if cand.is_file():
            return cand
    if m.is_file():
        return m
    raise SystemExit(f"pool pull: manifest 不存在: {source / m if not m.is_absolute() else m}")


def run_pull(args, cfg) -> int:  # noqa: ARG001 - cfg 预留（TOML 钩子）
    source = str(args.source)
    manifest_arg = str(args.manifest)
    dest = Path(args.dest)
    repo_type = getattr(args, "repo_type", None) or DEFAULT_REPO_TYPE
    src_path = Path(source)
    is_local = src_path.is_dir()
    if not is_local and not _looks_like_hf_repo(source):
        raise SystemExit(f"pool pull: source 既不是本地目录也不是 HF repo id: {source}")

    token = _token_from_env()
    tmpdir: Optional[str] = None
    try:
        if is_local:
            manifest_path = _resolve_local_manifest(src_path, manifest_arg)
            manifest_base = manifest_path.parent
            manifest_rel_dir = PurePosixPath(".")
        else:
            if manifest_arg.startswith(("/", "~")):
                raise SystemExit("pool pull: HF 源的 --manifest 必须是仓库内相对路径")
            tmpdir = tempfile.mkdtemp(prefix="gpu-serve-pull-")
            manifest_rel = manifest_arg.replace("\\", "/").lstrip("/")
            manifest_path = Path(tmpdir) / _MANIFEST_NAME
            download_hf_file(source, manifest_rel, repo_type, token, manifest_path)
            manifest_base = None  # 占位：HF 用仓库相对路径
            manifest_rel_dir = PurePosixPath(manifest_rel).parent

        manifest = load_manifest(manifest_path)
        dest.mkdir(parents=True, exist_ok=True)

        shard_reports: list[dict] = []
        for entry in manifest["shards"]:
            fname = str(entry["file"]).replace("\\", "/")
            dest_file = dest / Path(*PurePosixPath(fname).parts)
            status = "downloaded"
            if dest_file.is_file() and not getattr(args, "force", False):
                existing_sha = sha256_file(dest_file)
                expect_sha = entry.get("sha256")
                if (expect_sha and existing_sha == str(expect_sha)) or not expect_sha:
                    status = "skipped"
            if status != "skipped":
                if is_local:
                    src_file = manifest_base / fname  # type: ignore[operator]
                    if not src_file.is_file():
                        raise SystemExit(f"pool pull: 源 shard 不存在: {src_file}")
                    _copy_into(src_file, dest_file)
                else:
                    repo_file = str(PurePosixPath(manifest_rel_dir) / fname)
                    download_hf_file(source, repo_file, repo_type, token, dest_file)
            report = verify_shard_file(dest_file, entry)
            report["status"] = status
            shard_reports.append(report)

        errors = [f"{r['file']}: {e}" for r in shard_reports for e in r["errors"]]
        errors += check_manifest_totals(manifest, shard_reports)

        manifest_copy = dest / _MANIFEST_NAME
        if not errors:
            same = is_local and manifest_path.resolve() == manifest_copy.resolve()
            if not same:  # 本地源与 dest 同目录时 manifest 已在位，无需自复制
                _copy_into(manifest_path, manifest_copy)

        report = {
            "command": "pool pull",
            "source": source,
            "manifest": manifest_arg,
            "dest": str(dest),
            "repo_type": repo_type if not is_local else None,
            "token": _token_source() if not is_local else None,
            "ok": not errors,
            "shards": [
                {
                    "file": r["file"],
                    "status": r["status"],
                    "count": r.get("count"),
                    "bytes": r.get("bytes"),
                    "sha256": r.get("sha256"),
                    "verified": bool(r["ok"]),
                    "warnings": r["warnings"],
                }
                for r in shard_reports
            ],
            "errors": errors,
        }
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0 if report["ok"] else 1
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# push
# --------------------------------------------------------------------------- #
def _visible(rel: Path) -> bool:
    return not any(part.startswith(".") for part in rel.parts) and "__pycache__" not in rel.parts


def collect_files(src: Path) -> list[Path]:
    files = [p for p in sorted(src.rglob("*")) if p.is_file() and _visible(p.relative_to(src))]
    return files


def repo_path_of(src: Path, path: Path, prefix: str) -> str:
    rel = path.relative_to(src).as_posix()
    prefix = prefix.strip("/")
    return f"{prefix}/{rel}" if prefix else rel


def run_push(args, cfg) -> int:  # noqa: ARG001
    src = Path(args.src)
    if not src.is_dir():
        raise SystemExit(f"pool push: src 不是目录: {src}")
    repo = str(args.repo)
    repo_type = getattr(args, "repo_type", None) or DEFAULT_REPO_TYPE
    prefix = (getattr(args, "path_prefix", None) or "").strip("/")
    dry_run = bool(getattr(args, "dry_run", False))

    files = collect_files(src)
    if not files:
        raise SystemExit(f"pool push: src 为空: {src}")
    manifests = [p for p in files if p.name == _MANIFEST_NAME]
    if not manifests:
        raise SystemExit(
            f"pool push: 未找到 {_MANIFEST_NAME}（训练池目录应含 manifest.json + shard-*.jsonl）"
        )

    verify_reports = [verify_manifest_dir(mp) for mp in manifests]
    errors = [f"{r['manifest']}: {e}" for r in verify_reports for e in r["errors"]]

    plan = [(p, repo_path_of(src, p, prefix)) for p in files]
    total_bytes = sum(p.stat().st_size for p, _ in plan)
    for p, repo_path in plan:
        print(
            f"[plan] {p.stat().st_size:>10} B  {p.relative_to(src).as_posix()}  ->  {repo_path}",
            flush=True,
        )

    if errors:
        for err in errors:
            print(f"[fail] {err}", file=sys.stderr)
        report = {
            "command": "pool push",
            "repo": repo,
            "src": str(src),
            "path_prefix": prefix,
            "dry_run": dry_run,
            "ok": False,
            "files": len(plan),
            "bytes": total_bytes,
            "errors": errors,
        }
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 1

    token = _token_from_env()
    if dry_run:
        report = {
            "command": "pool push",
            "repo": repo,
            "repo_type": repo_type,
            "src": str(src),
            "path_prefix": prefix,
            "token": _token_source(),
            "dry_run": True,
            "ok": True,
            "files": len(plan),
            "bytes": total_bytes,
            "uploader": None,
            "errors": [],
        }
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0

    uploader = _upload_files(repo, repo_type, plan, token)
    report = {
        "command": "pool push",
        "repo": repo,
        "repo_type": repo_type,
        "src": str(src),
        "path_prefix": prefix,
        "token": _token_source(),
        "dry_run": False,
        "ok": True,
        "files": len(plan),
        "bytes": total_bytes,
        "uploader": uploader,
        "errors": [],
    }
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0


# --------------------------------------------------------------------------- #
# merge-logprobs
# --------------------------------------------------------------------------- #
def _extra_of(row: dict) -> dict:
    extra = row.get("extra")
    return extra if isinstance(extra, dict) else {}


def transition_search_id(row: dict, *, use_attempt: bool = False) -> Optional[str]:
    """search_id 解析（对齐 lmc ``extra.search_id or ref.search_id``，GPU 侧兼容回退）。

    ``use_attempt=True`` 时追加 ``extra.attempt_id`` 回退（FATE/CE 证据无 search_id）。
    """
    extra = _extra_of(row)
    ref = row.get("ref") if isinstance(row.get("ref"), dict) else {}
    extra_ref = extra.get("ref") if isinstance(extra.get("ref"), dict) else {}
    value = (
        row.get("search_id")
        or extra.get("search_id")
        or ref.get("search_id")
        or extra_ref.get("search_id")
    )
    if value is None and use_attempt:
        value = extra.get("attempt_id")
    return str(value) if value is not None else None


def transition_state_hash(row: dict) -> Optional[str]:
    """state hash 解析：lmc 用 extra.state_hash；FATE/CE 证据用 state_before_sha256。"""
    extra = _extra_of(row)
    step = extra.get("selected_path_step")
    step = step if isinstance(step, dict) else {}
    value = (
        extra.get("state_hash")
        or extra.get("state_before_sha256")
        or row.get("state_hash")
        or row.get("state_before_sha256")
        or step.get("state_before_sha256")
    )
    return str(value) if value is not None else None


def transition_key(row: dict, *, use_attempt: bool = False) -> Optional[tuple]:
    state = transition_state_hash(row)
    action = row.get("action")
    if state is None or action is None:
        return None
    return (transition_search_id(row, use_attempt=use_attempt), state, str(action))


def logprob_row_key(row: dict) -> Optional[tuple]:
    return transition_key(row)


def logprob_row_value(row: dict) -> Optional[float]:
    value = row.get("logprob_old")
    if value is None:
        value = row.get("logprob")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def merge_logprobs_rows(pool_rows: Iterable[dict], logprob_rows: list[dict]) -> tuple[list[dict], dict]:
    """核心 join：返回（池行列表（已就地回填 ``logprob_old``）, 统计）。

    建两张表：``primary``（search_id 严格解析）与 ``attempt``（追加 attempt_id 回退，
    仅在键不同于 primary 时另存），池行按 primary → attempt 顺序查，保证
    「只有一侧带 attempt_id」也能 join。
    """
    primary: dict[tuple, float] = {}
    attempt: dict[tuple, float] = {}
    duplicate = 0
    malformed = 0
    for row in logprob_rows:
        value = logprob_row_value(row)
        if value is None:
            malformed += 1
            continue
        key = transition_key(row)
        alt = transition_key(row, use_attempt=True)
        if key is None and alt is None:
            malformed += 1
            continue
        if key is not None:
            if key in primary:
                duplicate += 1
            primary[key] = value
        if alt is not None and alt != key:
            if alt in attempt:
                duplicate += 1
            attempt[alt] = value

    joined = 0
    missed = 0
    joined_without_search_id = 0
    out: list[dict] = []
    for row in pool_rows:
        key = transition_key(row)
        alt = transition_key(row, use_attempt=True)
        value: Optional[float] = None
        for candidate in (key, alt):
            if candidate is None:
                continue
            if candidate in primary:
                value = primary[candidate]
                break
            if candidate in attempt:
                value = attempt[candidate]
                break
        has_search_id = transition_search_id(row) is not None
        if value is not None:
            row["logprob_old"] = value
            joined += 1
            if not has_search_id:
                joined_without_search_id += 1
        else:
            missed += 1
        out.append(row)

    stats = {
        "logprob_rows": len(logprob_rows),
        "unique_keys": len(primary) + len(attempt),
        "duplicate_logprob_keys": duplicate,
        "malformed_logprob_rows": malformed,
        "pool_rows": len(out),
        "joined": joined,
        "missed": missed,
        "joined_without_search_id": joined_without_search_id,
    }
    return out, stats


def run_merge_logprobs(args, cfg) -> int:  # noqa: ARG001
    pool_path = str(args.pool)
    logprobs_path = Path(args.logprobs)
    if not logprobs_path.is_file():
        raise SystemExit(f"pool merge-logprobs: logprobs 文件不存在: {logprobs_path}")
    files = resolve_pool_files(pool_path)
    logprob_rows = read_jsonl(logprobs_path)

    pool_rows: list[dict] = []
    for file in files:
        pool_rows.extend(read_jsonl(file))

    merged_rows, stats = merge_logprobs_rows(pool_rows, logprob_rows)

    if getattr(args, "out", None):
        out_path = Path(args.out)
    else:
        out_path = logprobs_path.with_suffix(".merged.jsonl")
    _write_jsonl(out_path, merged_rows)

    errors: list[str] = []
    if getattr(args, "strict", False) and stats["missed"]:
        errors.append(f"strict：{stats['missed']} 条池行未 join 到 logprob")

    report = {
        "command": "pool merge-logprobs",
        "pool": pool_path,
        "logprobs": str(logprobs_path),
        "out": str(out_path),
        "strict": bool(getattr(args, "strict", False)),
        "ok": not errors,
        "errors": errors,
        **stats,
    }
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["ok"] else 1


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #
def collect_stats(pool_path: str | os.PathLike[str]) -> dict:
    files = resolve_pool_files(pool_path)
    root = Path(pool_path)
    kinds: dict[str, int] = {}
    policy_versions: set[str] = set()
    versions: dict[str, str] = {}
    searches: set[str] = set()
    problems: set[str] = set()
    totals = {
        "transitions": 0,
        "solved": 0,
        "terminal_verified": 0,
        "logprob_filled": 0,
    }
    per_shard: list[dict] = []
    for file in files:
        shard = {"file": file.name, "count": 0, "bytes": 0, "sha256": ""}
        for row in read_jsonl(file):
            shard["count"] += 1
            totals["transitions"] += 1
            kind = str(row.get("kind") or "unknown")
            kinds[kind] = kinds.get(kind, 0) + 1
            if row.get("solved") is True:
                totals["solved"] += 1
            if row.get("terminal_verified") is True:
                totals["terminal_verified"] += 1
            if row.get("logprob_old") is not None:
                totals["logprob_filled"] += 1
            extra = _extra_of(row)
            pv = row.get("policy_version") or extra.get("policy_version")
            if pv:
                policy_versions.add(str(pv))
            for key in ("lean", "mathlib", "reap", "patchset_hash"):
                value = extra.get(key) or row.get(key)
                if value and key not in versions:
                    versions[key] = str(value)
            sid = transition_search_id(row, use_attempt=True)
            if sid:
                searches.add(sid)
            pid = extra.get("problem_id") or row.get("problem_id")
            if pid:
                problems.add(str(pid))
        shard["bytes"] = file.stat().st_size
        shard["sha256"] = sha256_file(file)
        per_shard.append(shard)

    manifest_info: Optional[dict] = None
    manifest_error: Optional[str] = None
    manifest_path = root / _MANIFEST_NAME if root.is_dir() else None
    if manifest_path is not None and manifest_path.is_file():
        try:
            manifest = load_manifest(manifest_path)
        except SystemExit as exc:
            manifest_error = str(exc)
        else:
            expected = {}
            for entry in manifest.get("shards") or []:
                expected[str(entry.get("file"))] = entry
            sha_ok = 0
            sha_checked = 0
            for shard in per_shard:
                entry = expected.get(shard["file"])
                if entry and entry.get("sha256"):
                    sha_checked += 1
                    if str(entry["sha256"]) == shard["sha256"]:
                        sha_ok += 1
            manifest_info = {
                "path": str(manifest_path),
                "schema": manifest.get("schema") or manifest.get("schema_version"),
                "wave_id": manifest.get("wave_id") or manifest.get("batch_id"),
                "policy_version": manifest.get("policy_version")
                or (manifest.get("versions") or {}).get("policy_version"),
                "counts": manifest.get("counts"),
                "versions": manifest.get("versions"),
                "sha256_ok": sha_ok,
                "sha256_checked": sha_checked,
                "errors": check_manifest_totals(manifest, per_shard),
            }

    # 行内无版本字段时回退 manifest.versions（旧批次如 trajectory-batch.v1 只在 manifest 里带版本）
    if manifest_info:
        m_versions = manifest_info.get("versions") or {}
        for key in ("lean", "mathlib", "reap", "patchset_hash"):
            value = m_versions.get(key)
            if value and key not in versions:
                versions[key] = str(value)
        pv = manifest_info.get("policy_version")
        if pv:
            policy_versions.add(str(pv))

    stats = {
        "pool": str(root),
        "shards": per_shard,
        "totals": totals,
        "kinds": kinds,
        "searches": len(searches),
        "problems": len(problems),
        "policy_versions": sorted(policy_versions),
        "versions": versions,
        "manifest": manifest_info,
        "manifest_error": manifest_error,
    }
    return stats


def render_stats_table(stats: dict) -> str:
    """人读表格（key : value 对齐）。"""
    rows: list[tuple[str, str]] = []
    totals = stats["totals"]
    rows.append(("pool", stats["pool"]))
    manifest = stats.get("manifest")
    if manifest:
        desc = manifest.get("schema") or "?"
        if manifest.get("wave_id"):
            desc += f" wave={manifest['wave_id']}"
        if manifest.get("policy_version"):
            desc += f" policy_version={manifest['policy_version']}"
        sha = f"sha256 {manifest['sha256_ok']}/{manifest['sha256_checked']} OK"
        rows.append(("manifest", f"{desc}；{sha}"))
        if manifest.get("errors"):
            rows.append(("manifest.errors", "；".join(manifest["errors"])))
    elif stats.get("manifest_error"):
        rows.append(("manifest", f"读取失败：{stats['manifest_error']}"))
    else:
        rows.append(("manifest", "（无）"))
    rows.append(("shards", str(len(stats["shards"]))))
    rows.append(("transitions", str(totals["transitions"])))
    rows.append(("kinds", " ".join(f"{k}={v}" for k, v in sorted(stats["kinds"].items())) or "-"))
    rows.append((
        "solved / terminal_verified",
        f"{totals['solved']}/{totals['transitions']} / {totals['terminal_verified']}/{totals['transitions']}",
    ))
    rows.append(("logprob_old", f"{totals['logprob_filled']}/{totals['transitions']} 已回填"))
    rows.append(("searches / problems", f"{stats['searches']} / {stats['problems']}"))
    rows.append(("policy_versions", ", ".join(stats["policy_versions"]) or "-"))
    versions = stats.get("versions") or {}
    rows.append((
        "versions",
        " ".join(f"{k}={v}" for k, v in versions.items()) or "-",
    ))
    width = max(len(k) for k, _ in rows)
    lines = [f"{k.ljust(width)} : {v}" for k, v in rows]
    lines.append("")
    lines.append(f"{'file'.ljust(width)} : count / bytes / sha256")
    for shard in stats["shards"]:
        lines.append(
            f"{shard['file'].ljust(width)} : {shard['count']} / {shard['bytes']} / {shard['sha256'][:16]}…"
        )
    return "\n".join(lines)


def run_stats(args, cfg) -> int:  # noqa: ARG001
    stats = collect_stats(args.pool)
    if getattr(args, "as_json", False):
        print(json.dumps(stats, ensure_ascii=False), flush=True)
    else:
        print(render_stats_table(stats), flush=True)
    return 0
