"""gpu-serve pool 子命令测试（CPU、stdlib + mock 文件、不联网）。

覆盖：
- manifest 校验（sha256 / 计数 / 字节数、损坏与不匹配）；
- `pool pull` 本地源（幂等、manifest 绝对路径、损坏/缺文件/计数不符报错）；
- `pool push` dry-run 计划、mock 上传（不触网）、校验失败拒绝上传；
- `pool merge-logprobs` join（extra.state_hash / state_before_sha256 / ref.search_id /
  attempt_id 回退、strict、默认 --out）；
- `pool stats` 人读表格与 --json。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gpu_serve import pool as pool_mod  # noqa: E402
from gpu_serve.cli import main as cli_main  # noqa: E402


# --------------------------------------------------------------------------- #
# 构造工具
# --------------------------------------------------------------------------- #
def _sha256(path: Path) -> str:
    return pool_mod.sha256_file(path)


def _write_shard(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


def _row(pid: str, sid: str | None, state: str | None, action: str,
         value: float = -1.0, kind: str = "proof") -> dict:
    extra = {
        "problem_id": pid,
        "policy_version": "v1",
        "lean": "v4.28.0-rc1",
        "mathlib": "abc123",
        "reap": "0090d73c",
    }
    if sid is not None:
        extra["search_id"] = sid
    if state is not None:
        extra["state_hash"] = state
    return {
        "prompt": f"state-{state or pid}",
        "action": action,
        "value_target": value,
        "kind": kind,
        "solved": True,
        "terminal_verified": True,
        "logprob_old": None,
        "reward": None,
        "extra": extra,
    }


def _fate_row(pid: str, state_before: str, action: str,
              attempt_id: str = "attempt-p1-x") -> dict:
    """FATE/CE 证据风格：无 search_id，state hash 在 extra.state_before_sha256。"""
    row = _row(pid, None, None, action)
    row["extra"].pop("state_hash", None)
    row["extra"]["state_before_sha256"] = state_before
    row["extra"]["attempt_id"] = attempt_id
    return row


def _make_export(tmp_path: Path, name: str = "wave_test") -> tuple[Path, dict]:
    """构造本地导出目录：2 shards + manifest.json（CPU 池 05 §3 格式）。"""
    wave = tmp_path / "exports" / name
    wave.mkdir(parents=True)
    _write_shard(wave / "shard-0001.jsonl", [
        _row("p1", "s1", "h1", "tac1"),
        _row("p1", "s1", "h2", "tac2", value=-2.0),
    ])
    _write_shard(wave / "shard-0002.jsonl", [
        _row("p2", "s2", "h3", "tac3", value=0.0, kind="disproof"),
    ])
    shards = []
    for fname in ("shard-0001.jsonl", "shard-0002.jsonl"):
        p = wave / fname
        shards.append({
            "file": fname,
            "count": len(p.read_text(encoding="utf-8").splitlines()),
            "bytes": p.stat().st_size,
            "sha256": _sha256(p),
        })
    manifest = {
        "schema": "lmc.export_manifest.v1",
        "wave_id": name,
        "counts": {"transitions": 3, "shards": 2},
        "merge_logprobs": None,
        "policy_version": "v1",
        "versions": {"lean": "v4.28.0-rc1", "mathlib": "abc123", "reap": "0090d73c"},
        "shards": shards,
    }
    (wave / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return wave, manifest


def _pull_argv(src: Path, dest: Path, manifest: str = "manifest.json") -> list[str]:
    return ["pool", "pull", "--source", str(src), "--manifest", manifest, "--dest", str(dest)]


# --------------------------------------------------------------------------- #
# --help（无任何可选依赖）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("argv", [
    ["pool", "--help"],
    ["pool", "pull", "--help"],
    ["pool", "push", "--help"],
    ["pool", "merge-logprobs", "--help"],
    ["pool", "stats", "--help"],
])
def test_pool_help(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli_main(argv)
    assert excinfo.value.code == 0
    assert "gpu-serve" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# manifest 校验
# --------------------------------------------------------------------------- #
def test_manifest_load_and_verify(tmp_path):
    wave, loaded = _make_export(tmp_path)
    manifest = pool_mod.load_manifest(wave / "manifest.json")
    assert manifest["wave_id"] == loaded["wave_id"]
    report = pool_mod.verify_manifest_dir(wave / "manifest.json")
    assert report["ok"] is True
    assert len(report["shards"]) == 2
    assert all(r["ok"] for r in report["shards"])

    # 损坏一个 shard：sha256 / 行数 / 字节数均应报错
    (wave / "shard-0002.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    bad = pool_mod.verify_manifest_dir(wave / "manifest.json")
    assert bad["ok"] is False
    assert any("sha256" in e for e in bad["errors"])
    assert any("行数" in e for e in bad["errors"])


def test_manifest_structure_errors(tmp_path):
    missing = tmp_path / "manifest.json"
    with pytest.raises(SystemExit):
        pool_mod.load_manifest(missing)
    missing.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        pool_mod.load_manifest(missing)
    missing.write_text(json.dumps({"shards": []}), encoding="utf-8")
    with pytest.raises(SystemExit):
        pool_mod.load_manifest(missing)


# --------------------------------------------------------------------------- #
# pull
# --------------------------------------------------------------------------- #
def test_pull_local_and_idempotent(tmp_path, capsys):
    wave, manifest = _make_export(tmp_path)
    dest = tmp_path / "pool" / "wave_test"
    argv = _pull_argv(wave, dest)

    assert cli_main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert [s["status"] for s in report["shards"]] == ["downloaded", "downloaded"]
    assert [s["count"] for s in report["shards"]] == [2, 1]
    assert (dest / "shard-0001.jsonl").is_file()
    assert (dest / "shard-0002.jsonl").is_file()
    assert (dest / "manifest.json").is_file()
    assert _sha256(dest / "shard-0001.jsonl") == manifest["shards"][0]["sha256"]

    # 幂等：已存在且校验通过 → skipped（不重复拷贝）
    assert cli_main(argv) == 0
    report2 = json.loads(capsys.readouterr().out)
    assert [s["status"] for s in report2["shards"]] == ["skipped", "skipped"]


def test_pull_manifest_absolute_path(tmp_path, capsys):
    wave, _ = _make_export(tmp_path)
    dest = tmp_path / "dest_abs"
    assert cli_main(_pull_argv(wave, dest, str(wave / "manifest.json"))) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_pull_corrupt_shard_fails(tmp_path, capsys):
    wave, _ = _make_export(tmp_path)
    with (wave / "shard-0001.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"junk": 1}\n')
    dest = tmp_path / "dest"
    assert cli_main(_pull_argv(wave, dest)) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert any("sha256" in e or "行数" in e for e in report["errors"])
    # 校验失败时不回写 manifest（dest 留待重试）
    assert not (dest / "manifest.json").exists()


def test_pull_missing_source_shard_fails(tmp_path, capsys):
    wave, _ = _make_export(tmp_path)
    (wave / "shard-0002.jsonl").unlink()
    assert cli_main(_pull_argv(wave, tmp_path / "dest")) == 1
    assert "源 shard 不存在" in capsys.readouterr().err


def test_pull_count_mismatch_fails(tmp_path, capsys):
    wave, manifest = _make_export(tmp_path)
    manifest["shards"][1]["count"] = 99
    (wave / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert cli_main(_pull_argv(wave, tmp_path / "dest")) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert any("行数不一致" in e for e in report["errors"])


def test_pull_totals_mismatch_fails(tmp_path, capsys):
    wave, manifest = _make_export(tmp_path)
    manifest["counts"]["transitions"] = 99
    (wave / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert cli_main(_pull_argv(wave, tmp_path / "dest")) == 1
    report = json.loads(capsys.readouterr().out)
    assert any("counts.transitions" in e for e in report["errors"])


def test_pull_unknown_source_fails(tmp_path, capsys):
    rc = cli_main(["pool", "pull", "--source", "not-a-repo",
                   "--manifest", "manifest.json", "--dest", str(tmp_path / "d")])
    assert rc == 1
    assert "source" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# push
# --------------------------------------------------------------------------- #
def test_push_dry_run_no_network(tmp_path, capsys, monkeypatch):
    wave, _ = _make_export(tmp_path)
    calls: list = []
    monkeypatch.setattr(pool_mod, "_upload_files", lambda *a, **k: calls.append(a) or "mock")

    argv = ["pool", "push", "--src", str(wave), "--repo", "org/name", "--dry-run",
            "--path-prefix", "runs/wave_test"]
    assert cli_main(argv) == 0
    assert calls == []  # dry-run 不触网
    out = capsys.readouterr().out
    assert "[plan]" in out
    assert "runs/wave_test/shard-0001.jsonl" in out
    report = json.loads(out.strip().splitlines()[-1])
    assert report["ok"] is True and report["dry_run"] is True and report["files"] == 3


def test_push_upload_mocked(tmp_path, capsys, monkeypatch):
    wave, _ = _make_export(tmp_path)
    captured: dict = {}

    def fake_upload(repo, repo_type, plan, token):
        captured.update(repo=repo, repo_type=repo_type, plan=plan, token=token)
        return "mock-backend"

    monkeypatch.setattr(pool_mod, "_upload_files", fake_upload)
    monkeypatch.setenv("HF_TOKEN", "dummy-token-not-real")
    assert cli_main(["pool", "push", "--src", str(wave), "--repo", "org/name"]) == 0
    out = capsys.readouterr().out
    report = json.loads(out.strip().splitlines()[-1])
    assert report["ok"] is True
    assert report["uploader"] == "mock-backend"
    assert report["files"] == 3
    assert report["token"] == "HF_TOKEN env"
    assert "dummy-token-not-real" not in out  # token 不回显
    assert captured["repo"] == "org/name"
    assert captured["token"] == "dummy-token-not-real"
    assert {rp for _, rp in captured["plan"]} == {
        "manifest.json", "shard-0001.jsonl", "shard-0002.jsonl"}


def test_push_rejects_corrupt_before_upload(tmp_path, capsys, monkeypatch):
    wave, _ = _make_export(tmp_path)
    (wave / "shard-0001.jsonl").write_text("corrupt\n", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(pool_mod, "_upload_files", lambda *a, **k: calls.append(a) or "mock")
    assert cli_main(["pool", "push", "--src", str(wave), "--repo", "org/name"]) == 1
    assert calls == []
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["ok"] is False and report["errors"]


def test_push_requires_manifest(tmp_path, capsys):
    src = tmp_path / "bare"
    src.mkdir()
    _write_shard(src / "shard-0001.jsonl", [_row("p", "s", "h", "t")])
    assert cli_main(["pool", "push", "--src", str(src), "--repo", "org/name", "--dry-run"]) == 1
    assert "manifest" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# merge-logprobs
# --------------------------------------------------------------------------- #
def test_merge_logprobs_join(tmp_path, capsys):
    pool = tmp_path / "pool"
    pool.mkdir()
    _write_shard(pool / "shard-0001.jsonl", [
        _row("p1", "s1", "h1", "tac1"),
        _row("p1", "s1", "h2", "tac2"),
        _row("p2", "s2", "h3", "tac3"),
    ])
    lp = tmp_path / "gpu_logprobs.jsonl"
    _write_shard(lp, [
        {"search_id": "s1", "state_hash": "h1", "action": "tac1", "logprob_old": -1.5},
        {"ref": {"search_id": "s1"}, "state_before_sha256": "h2", "action": "tac2",
         "logprob": -2.5},  # ref 回退 + state_before 别名 + logprob 别名
        {"search_id": "s9", "state_hash": "h9", "action": "tac9", "logprob_old": -9.0},
        {"state_hash": "h1", "action": "tac1"},  # malformed：无 logprob
    ])
    out = tmp_path / "merged.jsonl"
    assert cli_main(["pool", "merge-logprobs", "--pool", str(pool),
                     "--logprobs", str(lp), "--out", str(out)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["joined"] == 2 and report["missed"] == 1
    assert report["unique_keys"] == 3 and report["malformed_logprob_rows"] == 1
    merged = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert merged[0]["logprob_old"] == -1.5
    assert merged[1]["logprob_old"] == -2.5
    assert merged[2]["logprob_old"] is None


def test_merge_logprobs_fate_style_and_strict(tmp_path, capsys):
    pool = tmp_path / "pool"
    pool.mkdir()
    _write_shard(pool / "shard-0001.jsonl", [
        _fate_row("p1", "sb1", "tacA"),
        _fate_row("p1", "sb2", "tacB"),
        _fate_row("p2", "sb3", "tacC", attempt_id="attempt-p2-y"),
    ])
    lp = tmp_path / "lp.jsonl"
    _write_shard(lp, [
        {"state_before_sha256": "sb1", "action": "tacA", "logprob_old": -0.5},
        {"extra": {"attempt_id": "attempt-p1-x"}, "state_before_sha256": "sb2",
         "action": "tacB", "logprob": -1.5},
    ])
    assert cli_main(["pool", "merge-logprobs", "--pool", str(pool), "--logprobs", str(lp)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["joined"] == 2 and report["missed"] == 1
    assert report["joined_without_search_id"] == 2  # 两侧都缺 search_id，按 (None, hash, action) join
    default_out = lp.with_suffix(".merged.jsonl")
    assert Path(report["out"]) == default_out and default_out.is_file()
    merged = [json.loads(line) for line in default_out.read_text(encoding="utf-8").splitlines()]
    assert [m["logprob_old"] for m in merged] == [-0.5, -1.5, None]

    # strict：有未 join 的池行 → 非零
    assert cli_main(["pool", "merge-logprobs", "--pool", str(pool), "--logprobs", str(lp),
                     "--out", str(tmp_path / "m2.jsonl"), "--strict"]) == 1
    strict_report = json.loads(capsys.readouterr().out)
    assert strict_report["ok"] is False and strict_report["errors"]


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #
def test_stats_table(tmp_path, capsys):
    wave, _ = _make_export(tmp_path)
    dest = tmp_path / "pool" / "wave_test"
    assert cli_main(_pull_argv(wave, dest)) == 0
    capsys.readouterr()

    assert cli_main(["pool", "stats", "--pool", str(dest)]) == 0
    out = capsys.readouterr().out
    assert "wave=wave_test" in out
    assert "shards" in out and "transitions" in out
    assert "sha256 2/2 OK" in out
    assert "policy_versions" in out and "v1" in out
    assert "lean=v4.28.0-rc1" in out
    assert "proof=2" in out and "disproof=1" in out
    assert "shard-0001.jsonl" in out


def test_stats_json(tmp_path, capsys):
    wave, _ = _make_export(tmp_path)
    assert cli_main(["pool", "stats", "--pool", str(wave), "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["totals"]["transitions"] == 3
    assert stats["totals"]["solved"] == 3
    assert stats["totals"]["terminal_verified"] == 3
    assert stats["totals"]["logprob_filled"] == 0
    assert stats["kinds"] == {"proof": 2, "disproof": 1}
    assert stats["searches"] == 2 and stats["problems"] == 2
    assert stats["manifest"]["sha256_ok"] == 2
    assert stats["manifest"]["errors"] == []
    assert len(stats["shards"]) == 2


def test_stats_versions_fallback_to_manifest(tmp_path, capsys):
    """行内无版本字段（如旧 trajectory-batch 批次）时，stats 回退 manifest.versions。"""
    wave, manifest = _make_export(tmp_path)
    for fname in ("shard-0001.jsonl", "shard-0002.jsonl"):
        p = wave / fname
        rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
        for row in rows:
            for key in ("lean", "mathlib", "reap", "patchset_hash", "policy_version"):
                row["extra"].pop(key, None)
        _write_shard(p, rows)
        entry = next(s for s in manifest["shards"] if s["file"] == fname)
        entry.update({"sha256": _sha256(p), "bytes": p.stat().st_size, "count": len(rows)})
    (wave / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert cli_main(["pool", "stats", "--pool", str(wave), "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["versions"]["lean"] == "v4.28.0-rc1"
    assert stats["versions"]["mathlib"] == "abc123"
    assert stats["policy_versions"] == ["v1"]


def test_stats_single_file_after_merge(tmp_path, capsys):
    """merge 输出（单 jsonl）可直接 stats：logprob_old 回填计数可见。"""
    pool = tmp_path / "pool"
    pool.mkdir()
    _write_shard(pool / "shard-0001.jsonl", [_row("p1", "s1", "h1", "tac1")])
    lp = tmp_path / "lp.jsonl"
    _write_shard(lp, [{"search_id": "s1", "state_hash": "h1", "action": "tac1",
                       "logprob_old": -0.5}])
    assert cli_main(["pool", "merge-logprobs", "--pool", str(pool), "--logprobs", str(lp)]) == 0
    capsys.readouterr()
    merged = lp.with_suffix(".merged.jsonl")
    assert cli_main(["pool", "stats", "--pool", str(merged), "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["totals"]["transitions"] == 1
    assert stats["totals"]["logprob_filled"] == 1
