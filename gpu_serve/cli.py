"""``gpu-serve`` 命令行入口（console script / ``python -m gpu_serve``）。

命令面（冻结清单见 docs/architecture/core-and-branches.md §1）::

    gpu-serve serve-eval  [--base ...] [--lora ...|none] [--head ...] [--dtype ...]
                          [--host 127.0.0.1] [--port 18080] [--token-file F]
    gpu-serve train offline --pool <dir> [...]
    gpu-serve train online  --events <jsonl> [...]
    gpu-serve eval heldout  --pool <dir> [...]
    gpu-serve pool pull --source <hf-repo|local-dir> --manifest <path> --dest <dir>
    gpu-serve pool push --src <dir> --repo <hf-repo> [--dry-run]
    gpu-serve pool merge-logprobs --pool <dir> --logprobs <jsonl> [--out ...]
    gpu-serve pool stats --pool <dir>
    gpu-serve versions [--base ...] [--head ...] [--lora ...]
    gpu-serve smoke --tiny | --base ... --head ...

``pool`` 为 GPU 训练池管理（规格：docs/new-python-version-mcts-plan/05；
命令面冻结：docs/architecture/core-and-branches.md §1）。所有命令支持
``--config <TOML>``（可写在子命令前或后），CLI 显式 flag 覆盖 TOML，
TOML 覆盖代码默认值。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional

from . import __version__
from .config import (
    build_lora_spec,
    build_model_setup,
    build_serve_options,
    load_toml,
)

LOG = logging.getLogger("gpu_serve")


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def _model_parent() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--config", default=None, metavar="F", help="可选 TOML 配置（CLI 覆盖 TOML）")
    p.add_argument("--base", default=None, metavar="PATH_OR_ID",
                   help="base model（HF 路径或 id）；可指向 PEFT adapter 目录")
    p.add_argument("--lora", default=None, metavar="PATH|none",
                   help="LoRA adapter 目录或裸 state_dict；none=不挂载")
    p.add_argument("--head", default=None, metavar="PATH",
                   help="64 桶 value head 权重（.pt/.bin）")
    p.add_argument("--tokenizer", default=None, metavar="PATH_OR_ID",
                   help="tokenizer 路径/名称；默认同 --base")
    p.add_argument("--dtype", default=None, choices=["auto", "bf16", "fp16", "fp32"],
                   help="推理/训练精度；auto=GPU bf16 / CPU fp32")
    p.add_argument("--device", default=None, help="auto|cuda|cuda:N|cpu")
    p.add_argument("--hidden-size", dest="hidden_size", type=int, default=None,
                   help="value head 输入维度；默认从 base config 推断")
    p.add_argument("--value-mid", dest="value_mid", type=int, default=None,
                   help="value head 中间层宽度（默认 256）")
    p.add_argument("--value-bins", dest="value_bins", type=int, default=None,
                   help="value head 桶数（默认 64，冻结口径）")
    p.add_argument("--allow-download", dest="allow_download", action="store_true", default=None,
                   help="允许联网下载 HF 组件（默认 local_files_only）")
    p.add_argument("--trust-remote-code", dest="trust_remote_code", action="store_true",
                   default=None, help="允许 base 的 remote code")
    p.add_argument("--revision", default=None, help="HF revision（分支/commit）")
    p.add_argument("--strict-head", dest="strict_head",
                   action=argparse.BooleanOptionalAction, default=None,
                   help="value head 键不匹配时直接失败（默认只警告记录）")
    p.add_argument("--tiny", action="store_true", default=None,
                   help="使用 alphaproof.tiny 小模型（无需 HF 权重；测试/smoke）")
    p.add_argument("--rank", type=int, default=None,
                   help="裸 LoRA state_dict 的 rank（目录式 adapter 自动读取）")
    p.add_argument("--lora-alpha", dest="lora_alpha", type=int, default=None,
                   help="裸 LoRA state_dict 的 alpha（默认 32）")
    p.add_argument("--lora-dropout", dest="lora_dropout", type=float, default=None,
                   help="裸 LoRA 构造用 dropout（默认 0.02）")
    p.add_argument("--lora-target-modules", dest="lora_target_modules", default=None,
                   metavar="a,b,c", help="裸 LoRA 注入模块（逗号分隔）")
    p.add_argument("--verbose", dest="verbose_sub", action="store_true", default=None,
                   help="INFO 级日志（可放在子命令后）")
    return p


def _train_parent() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--steps", type=int, default=None, help="offline：optimizer step 数（默认 1）")
    p.add_argument("--batch-size", dest="batch_size", type=int, default=None,
                   help="offline：每 step 样本数；online：learn_batch_size（几条事件一更新）")
    p.add_argument("--micro-batch-size", dest="micro_batch_size", type=int, default=None,
                   help="单次 backward 的最大条数（梯度累积切块）")
    p.add_argument("--lr", type=float, default=None, help="policy 学习率（默认 1e-4）")
    p.add_argument("--value-lr", dest="value_lr", type=float, default=None,
                   help="value head 学习率（默认 3e-4）")
    p.add_argument("--value-coef", dest="value_coef", type=float, default=None,
                   help="value 损失权重（默认 1e-3，官方 Table 6）")
    p.add_argument("--beta-kl", dest="beta_kl", type=float, default=None,
                   help="在线模式 KL 锚（默认 0.05）")
    p.add_argument("--sft-mix", dest="sft_mix", type=float, default=None,
                   help="SFT 混比（默认 0.1，官方 Table 6）")
    p.add_argument("--disproof-weight", dest="disproof_weight", type=float, default=None,
                   help="反证样本损失权重（默认 1.0）")
    p.add_argument("--include-disproof", dest="include_disproof",
                   action=argparse.BooleanOptionalAction, default=None,
                   help="反证轨迹进训练（默认开）")
    p.add_argument("--include-timeout", dest="include_timeout",
                   action=argparse.BooleanOptionalAction, default=None,
                   help="超时轨迹进训练（默认关）")
    p.add_argument("--max-grad-norm", dest="max_grad_norm", type=float, default=None,
                   help="梯度裁剪（默认 1.0）")
    p.add_argument("--seed", type=int, default=None, help="回放采样种子（默认 0）")
    return p


def build_parser() -> argparse.ArgumentParser:
    model_parent = _model_parent()
    train_parent = _train_parent()

    parser = argparse.ArgumentParser(
        prog="gpu-serve",
        description="GPU 侧参数化 CLI：base/LoRA/value head 可插拔，CE 更新逻辑冻结（只调用）。",
    )
    parser.add_argument("--version", action="version", version=f"gpu-serve {__version__}")
    parser.add_argument("--config", dest="config_root", default=None, metavar="F",
                        help="可选 TOML 配置（也可写在子命令后）")
    parser.add_argument("--verbose", action="store_true", default=False,
                        help="INFO 级日志（也可写在子命令后）")
    sub = parser.add_subparsers(dest="command", required=True)

    # serve-eval
    p = sub.add_parser("serve-eval", parents=[model_parent],
                       help="启动 /eval 契约 HTTP 服务")
    p.add_argument("--host", default=None, help="绑定地址（默认 127.0.0.1）")
    p.add_argument("--port", type=int, default=None, help="端口（默认 18080）")
    p.add_argument("--token-file", dest="token_file", default=None, metavar="F",
                   help="Bearer token 文件（600；不配置则不鉴权）")
    p.add_argument("--engine", default=None, choices=["auto", "hf", "tiny", "mock"],
                   help="引擎；auto=tiny 模式→tiny，有 --base→hf")
    p.add_argument("--max-new-tokens", dest="max_new_tokens", type=int, default=None,
                   help="候选 tactic 生成长度（默认 64）")
    p.add_argument("--stop-at-newline", dest="stop_at_newline",
                   action=argparse.BooleanOptionalAction, default=None,
                   help="候选截断到首个换行（默认开）")
    p.add_argument("--model-revision", dest="model_revision", default=None,
                   help="响应中的 model_revision（默认 base 目录名）")
    p.add_argument("--adapter", default=None, help="响应中的 adapter 标识（默认 LoRA 目录名）")
    p.set_defaults(func=_cmd_serve_eval)

    # train offline / online
    train = sub.add_parser("train", help="训练子命令（调用既有 learner）")
    train_sub = train.add_subparsers(dest="train_command", required=True)

    off = train_sub.add_parser("offline", parents=[model_parent, train_parent],
                               help="离线专家迭代（update_offline）")
    off.add_argument("--pool", required=True, metavar="DIR|JSONL",
                     help="训练池（shard-*.jsonl 目录或单个 jsonl）")
    off.add_argument("--sft-pool", dest="sft_pool", default=None, metavar="DIR|JSONL",
                     help="SFT 混比池（可选；sft_mix 生效）")
    off.add_argument("--out", default=None, metavar="F", help="回执 JSONL 路径")
    off.add_argument("--save-dir", dest="save_dir", default=None, metavar="DIR",
                     help="保存训练产物（value-head.pt [+ adapter/] + meta.json）")
    off.set_defaults(func=_cmd_train_offline)

    on = train_sub.add_parser("online", parents=[model_parent, train_parent],
                              help="在线更新（参数化壳 + 事件回放）")
    on.add_argument("--events", required=True, metavar="JSONL",
                    help="事件 JSONL（Transition，需带 logprob_old/reward）")
    on.add_argument("--out", default=None, metavar="F", help="回执 JSONL 路径")
    on.add_argument("--save-dir", dest="save_dir", default=None, metavar="DIR",
                    help="保存训练产物")
    on.set_defaults(func=_cmd_train_online)

    # eval heldout
    ev = sub.add_parser("eval", help="评测子命令")
    ev_sub = ev.add_subparsers(dest="eval_command", required=True)
    held = ev_sub.add_parser("heldout", parents=[model_parent],
                             help="留出池评测（policy CE / value CE / d_hat）")
    held.add_argument("--pool", required=True, metavar="DIR|JSONL", help="留出池")
    held.add_argument("--micro-batch-size", dest="micro_batch_size", type=int, default=None,
                      help="前向切块大小（默认 8）")
    held.add_argument("--policy-only", dest="policy_only", action="store_true",
                      help="只评 policy（不加载/不评 value head）")
    held.add_argument("--out", default=None, metavar="F", help="报告 JSON 路径")
    held.set_defaults(func=_cmd_eval_heldout)

    # versions
    ver = sub.add_parser("versions", parents=[model_parent],
                         help="工具链与组件版本清单")
    ver.set_defaults(func=_cmd_versions)

    # smoke
    sm = sub.add_parser("smoke", parents=[model_parent, train_parent],
                        help="冒烟：tiny 全回路 / 真实 7B 装载+前向")
    sm.add_argument("--pool", default=None, metavar="DIR|JSONL",
                    help="真实模式可选：跑 1 步 offline 更新")
    sm.add_argument("--out", default=None, metavar="F", help="报告 JSON 路径")
    sm.add_argument("--prompt", default=None, help="前向探针 prompt")
    sm.add_argument("--action", default=None, help="前向探针 action")
    sm.add_argument("--skip-update", dest="skip_update", action="store_true",
                    help="只验证装载与前向，不跑更新")
    sm.set_defaults(func=_cmd_smoke)

    # pool（GPU 训练池：pull/push/merge-logprobs/stats）
    pool = sub.add_parser("pool", help="GPU 训练池管理（HF/本地拉取、上传、logprob 回填、统计）")
    pool_sub = pool.add_subparsers(dest="pool_command", required=True)

    pp = pool_sub.add_parser("pull", help="拉取 shard 到训练池并校验 sha256/计数（05 §3）")
    pp.add_argument("--source", required=True, metavar="HF_REPO|LOCAL_DIR",
                    help="HF repo id（org/name，匿名可读）或本地导出目录")
    pp.add_argument("--manifest", required=True, metavar="PATH",
                    help="manifest.json：HF 源=仓库内相对路径；本地源=文件路径（相对 source 优先）")
    pp.add_argument("--dest", required=True, metavar="DIR",
                    help="目标训练池目录（建议 pool/<wave_id>）")
    pp.add_argument("--repo-type", dest="repo_type", default=None,
                    help="HF 仓库类型（默认 dataset）")
    pp.add_argument("--force", action="store_true", help="已存在且校验通过也重新拉取")
    pp.set_defaults(func=_cmd_pool_pull)

    pu = pool_sub.add_parser("push", help="上传训练池目录到 HF（public；token 读 HF_TOKEN）")
    pu.add_argument("--src", required=True, metavar="DIR",
                    help="待上传目录（含 manifest.json + shard-*.jsonl）")
    pu.add_argument("--repo", required=True, metavar="HF_REPO", help="目标仓库 org/name")
    pu.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="只本地校验 + 打印上传计划，不触网")
    pu.add_argument("--path-prefix", dest="path_prefix", default=None, metavar="P",
                    help="仓库内路径前缀（默认根目录）")
    pu.add_argument("--repo-type", dest="repo_type", default=None,
                    help="HF 仓库类型（默认 dataset）")
    pu.set_defaults(func=_cmd_pool_push)

    ml = pool_sub.add_parser("merge-logprobs",
                             help="按 (search_id, state_hash, action) 回填 logprob_old")
    ml.add_argument("--pool", required=True, metavar="DIR|JSONL", help="训练池（目录或 jsonl）")
    ml.add_argument("--logprobs", required=True, metavar="JSONL",
                    help="GPU 侧 logprob JSONL（logprob_old 或 logprob 字段）")
    ml.add_argument("--out", default=None, metavar="F",
                    help="输出 JSONL（默认 <logprobs>.merged.jsonl）")
    ml.add_argument("--strict", action="store_true",
                    help="有池行未 join 时返回非零（默认仅报告）")
    ml.set_defaults(func=_cmd_pool_merge_logprobs)

    st = pool_sub.add_parser("stats", help="池统计：shards/transitions/版本（人读表格）")
    st.add_argument("--pool", required=True, metavar="DIR|JSONL", help="训练池（目录或 jsonl）")
    st.add_argument("--json", dest="as_json", action="store_true", help="输出 JSON（默认人读表格）")
    st.set_defaults(func=_cmd_pool_stats)

    return parser


# --------------------------------------------------------------------------- #
# 子命令实现
# --------------------------------------------------------------------------- #
def _cmd_serve_eval(args, cfg) -> int:
    from . import serve as serve_mod
    from .engines import build_engine

    setup = build_model_setup(args, cfg)
    opts = build_serve_options(args, cfg)
    token = serve_mod.read_token_file(opts["token_file"]) if opts["token_file"] else None
    engine = build_engine(setup, opts["engine"],
                          max_new_tokens=opts["max_new_tokens"],
                          stop_at_newline=opts["stop_at_newline"],
                          model_revision=opts["model_revision"],
                          adapter=opts["adapter"] or None,
                          lora_spec=build_lora_spec(args, cfg))
    server = serve_mod.make_server(engine, host=opts["host"], port=opts["port"], token=token)
    banner = {
        "event": "serve-eval-ready",
        "host": opts["host"],
        "port": server.server_address[1],
        "engine": type(engine).__name__,
        "model_revision": engine.model_revision,
        "adapter": engine.adapter,
        "auth": "bearer" if token else "none",
    }
    print(json.dumps(banner, ensure_ascii=False), flush=True)
    serve_mod.serve_forever(server)
    return 0


def _cmd_train_offline(args, cfg) -> int:
    from .train import run_offline

    return run_offline(args, cfg)


def _cmd_train_online(args, cfg) -> int:
    from .train import run_online

    return run_online(args, cfg)


def _cmd_eval_heldout(args, cfg) -> int:
    from .heldout import run_heldout

    return run_heldout(args, cfg)


def _cmd_versions(args, cfg) -> int:
    from .versions import run_versions

    return run_versions(args, cfg)


def _cmd_smoke(args, cfg) -> int:
    from .smoke import run_smoke

    return run_smoke(args, cfg)


def _cmd_pool_pull(args, cfg) -> int:
    from .pool import run_pull

    return run_pull(args, cfg)


def _cmd_pool_push(args, cfg) -> int:
    from .pool import run_push

    return run_push(args, cfg)


def _cmd_pool_merge_logprobs(args, cfg) -> int:
    from .pool import run_merge_logprobs

    return run_merge_logprobs(args, cfg)


def _cmd_pool_stats(args, cfg) -> int:
    from .pool import run_stats

    return run_stats(args, cfg)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    verbose = bool(getattr(args, "verbose", False) or getattr(args, "verbose_sub", False))
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config_path = getattr(args, "config", None) or getattr(args, "config_root", None)
    cfg = {}
    if config_path:
        try:
            cfg = load_toml(config_path)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            print(f"gpu-serve: 配置错误: {exc}", file=sys.stderr)
            return 2
    LOG.debug("config: %s", json.dumps(cfg, ensure_ascii=False) if cfg else "{}")

    try:
        return int(args.func(args, cfg) or 0)
    except SystemExit as exc:  # 参数/环境类错误：保留退出码
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        if isinstance(exc.code, str):
            print(f"gpu-serve: {exc.code}", file=sys.stderr)
            return 1
        return code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
