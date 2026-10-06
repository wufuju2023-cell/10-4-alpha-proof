# Formal actor assets

Status: the common REAL-Prover 7B initial LoRA is frozen; the upstream
LeanSearch-PS retrieval assets are not present and are unsafe to download under
the current ModelScope persistent-storage budget.

## Authoritative paths

- Base model: `/mnt/workspace/models/REAL-Prover-fe76f68d`
- Common initial adapter:
  `/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/assets/initial_lora_r16_a32_seed20261004`
- Full remote adapter manifest:
  `/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005/assets/initial_lora_r16_a32_seed20261004/formal_initial_adapter_manifest.json`
- Reproduction script: `scripts/create_initial_lora.py`
- REAL-Prover upstream: commit `3e5987dc1009a7addc935bcd7b4ae777738a0848`
- Ephemeral Reap runtime:
  `/tmp/fate-m-reap428/runtime/.lake/build/lib/lean/ReapRuntime.olean`

The adapter is an untrained r16/alpha32 LoRA created with seed `20261004`.
All LoRA-B tensors were checked to be exactly zero before saving, so the
initial evaluation function is the base model while the 40,370,176 trainable
parameters and their random LoRA-A initialization are byte-pinned. CE and
Online-v2 must load the exact same adapter bytes independently. The saved
adapter occupies about 155 MiB; its safetensors SHA-256 is
`326e08d17a74eec08d52127c7e011462bf1d207266cf0ca9a3a84ddc0ddde2dd`.

The only existing adapters discovered elsewhere on the instance belong to an
older Qwen3-1.7B experiment. They are architecture-incompatible and are not
valid initialization candidates.

## Retrieval decision

The pinned REAL-Prover default TOML requests `use_retrieval=true`, but the
checkout contains only LeanSearch-PS server source. The embedding model,
e5-mistral-7b asset, FAISS index and `answer.json` are absent. Downloading an
additional 7B retrieval model while the ModelScope UI reports 88.4/100G would
violate the storage guard. Before any formal arm update, the paired protocol
therefore records the same explicit `use_retrieval=false` deviation for both
arms. PromptManage's in-context Qwen format, generation settings and search
budgets remain common.

## Transfer note

The current SSH alias executes commands normally, but Windows `scp` closed the
connection before copying this script. A tar stream over the same SSH channel
worked and the remote SHA-256 matched the local script hash
`601df65c46c304638cb3abe04c1c9e4ff9fbb29e3114747d94f3aa19b5bf8fe6`.
