# Asset lock v1

The frozen config points to an asset-lock JSON and pins its SHA-256 externally. The trainer rehashes every listed file before loading the model and requires exactly one of each single role plus at least one `model_shard`:

- `model_config`
- `generation_config`
- `tokenizer_config`
- `tokenizer`
- `model_index`
- `model_shard` (one or more)
- `value_head`
- `target_value_head_source`
- `initial_adapter_config`
- `initial_adapter_model`
- `initial_adapter_manifest`

Every entry contains `role`, absolute `path`, byte `size` and `sha256`. `model_root` is recursively inventoried: every regular file below it must appear exactly once under a model/tokenizer role (`model_aux` covers model-card or other auxiliary files), and the locked shard set must exactly equal `model_index.weight_map`. The lock also contains:

```json
{
  "schema_version": 1,
  "model_root": "/absolute/model/snapshot",
  "model_revision": "full 40-hex source revision",
  "initial_adapter_root": "/absolute/shared/initial/adapter",
  "files": [],
  "target_git": {
    "path": "/absolute/target/repo",
    "commit": "40-hex commit; checkout must be clean"
  },
  "runtime_versions": {
    "torch": "exact runtime string",
    "transformers": "exact version",
    "peft": "exact version",
    "cryptography": "exact version"
  }
}
```

`initial_adapter_root` is also recursively inventoried. Before a fresh update,
the trainer independently verifies the formal adapter manifest payload, its
config and safetensors hashes, base-model revision, LoRA geometry,
`trained=false`, and the creation-time trainable tensor-state hash. It loads
those exact shared bytes instead of constructing a new random LoRA. A resumed
run loads only the adapter inside the verified checkpoint lineage.

The config separately pins the course manifest, shared actor config, shared budget config, tokenizer lock and trusted verifier lock. Their files are rehashed before training. The command also requires `--expected-config-sha256`, supplied by the immutable experiment run manifest, so editing the frozen config is a hard failure.

For `shared_actor_config` and `tokenizer_lock`, the config stores both
`sha256` (the lock/config file bytes) and `receipt_identity_sha256` (the hash
domain written into signed actor receipts). These values are intentionally not
assumed equal. The smoke binder derives the canonical actor-object identity and
validates the tokenizer lock's ordered tokenizer-file manifest before freezing
both identities into the CE config.

The trusted verifier lock additionally contains `ed25519_public_key_hex`; the corresponding private key belongs only to the verifier process and must not be placed in the workspace, config, logs or receipts.
