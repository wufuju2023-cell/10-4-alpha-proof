"""Atomic REAL-Prover generation and exact behavior rescoring."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib, json, math, time
from typing import Any, Callable, ContextManager

from .contract import ActorContractError, BehaviorIdentity, GenerationParameters, canonical_sha256

@dataclass(frozen=True)
class RawGeneration:
    request_id: str
    candidate_index: int
    request_seed: int
    prompt_token_ids: tuple[int, ...]
    raw_completion_token_ids: tuple[int, ...]
    raw_completion_old_logprobs: tuple[float, ...]
    raw_completion_sampling_logprobs: tuple[float, ...]
    finish_reason: str
    service_candidate_sha256: str
    wall_seconds: float
    gpu_seconds: float

def trainable_state_sha256(model: Any, *, max_bytes: int=512*1024*1024) -> str:
    params=[(n,p) for n,p in model.named_parameters() if p.requires_grad]
    total=sum(int(p.numel())*int(p.element_size()) for _,p in params)
    if not params or total>max_bytes: raise ActorContractError(f"expected bounded named behavior adapter, got {total} trainable bytes")
    import torch
    digest=hashlib.sha256()
    for name,value in sorted(params):
        tensor=value.detach().cpu().contiguous(); header=canonical_sha256({"name":name,"dtype":str(tensor.dtype),"shape":list(tensor.shape)})
        digest.update(bytes.fromhex(header)); digest.update(tensor.view(dtype=torch.uint8).numpy().tobytes())
    return digest.hexdigest()

def _device(model: Any):
    try: return next(model.parameters()).device
    except (AttributeError,StopIteration) as exc: raise ActorContractError("model lacks parameter device") from exc

def _warped_logprobs(logits, targets, *, temperature: float, top_p: float):
    """Temperature then top-p, matching Transformers generation warper order."""
    import torch
    scores=logits.float()/temperature
    sorted_logits,sorted_indices=torch.sort(scores,descending=True,dim=-1)
    cumulative=torch.softmax(sorted_logits,dim=-1).cumsum(dim=-1)
    remove=cumulative>top_p
    remove[...,1:]=remove[...,:-1].clone(); remove[...,0]=False
    sorted_logits=sorted_logits.masked_fill(remove,float("-inf"))
    warped=torch.full_like(scores,float("-inf")).scatter(-1,sorted_indices,sorted_logits)
    return torch.log_softmax(warped,dim=-1).gather(-1,targets.unsqueeze(-1)).squeeze(-1)

def generate_raw_candidates(*, model: Any, tokenizer: Any, prompt: str,
    generation: GenerationParameters, request_id: str, request_seed: int,
    expected_identity: BehaviorIdentity,
    model_transaction: Callable[[], ContextManager[Any]],
    activate_behavior: Callable[[], None],
    live_identity: Callable[[], BehaviorIdentity],
    rescore_micro_batch: int=8) -> tuple[RawGeneration,...]:
    """Hold the updater's shared lock across activation, generate and rescoring."""
    generation.validate()
    if not request_id or type(request_seed) is not int or request_seed<0 or type(rescore_micro_batch) is not int or rescore_micro_batch<1:
        raise ActorContractError("invalid request id/seed/micro-batch")
    expected_identity.validate()
    import torch
    device=_device(model); is_gpu=getattr(device,"type",str(device).split(":")[0])=="cuda"
    sync=getattr(torch.cuda,"synchronize",lambda *_:None)
    with model_transaction():
        activate_behavior()
        before=live_identity(); before.validate()
        if before != expected_identity: raise ActorContractError("live behavior/base/tokenizer identity differs before capture")
        if getattr(model,"training",False): raise ActorContractError("frozen behavior capture requires model.eval()")
        encoded=tokenizer(prompt,add_special_tokens=True,return_tensors="pt")
        input_ids=encoded["input_ids"].to(device); attention=encoded.get("attention_mask",torch.ones_like(input_ids)).to(device)
        if input_ids.ndim!=2 or input_ids.shape[0]!=1 or attention.shape!=input_ids.shape or not bool(torch.all(attention==1)):
            raise ActorContractError("capture requires one unpadded prompt")
        prompt_ids=input_ids[0].detach().cpu().tolist(); eos=tokenizer.eos_token_id
        if type(eos) is not int or eos<0: raise ActorContractError("tokenizer lacks EOS")
        if is_gpu: sync(device)
        started=time.perf_counter(); generator=torch.Generator(device=device).manual_seed(request_seed)
        kwargs=dict(input_ids=input_ids,attention_mask=attention,do_sample=True,temperature=generation.temperature,top_p=generation.top_p,
                    max_new_tokens=generation.max_new_tokens,num_return_sequences=generation.num_return_sequences,
                    pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else eos,eos_token_id=eos,
                    generator=generator,return_dict_in_generate=True,output_scores=False)
        devices=[device.index if device.index is not None else torch.cuda.current_device()] if is_gpu else []
        with torch.random.fork_rng(devices=devices):
            torch.manual_seed(request_seed)
            if is_gpu: torch.cuda.manual_seed(request_seed)
            with torch.inference_mode():
                try: generated=model.generate(**kwargs)
                except ValueError as exc:
                    message=str(exc)
                    if "model_kwargs" not in message or "generator" not in message or "not used" not in message: raise
                    kwargs.pop("generator")
                    # The compatibility retry gets a fresh identical RNG state.
                    torch.manual_seed(request_seed)
                    if is_gpu: torch.cuda.manual_seed(request_seed)
                    generated=model.generate(**kwargs)
        sequences=generated.sequences
        if sequences.shape[0]!=generation.num_return_sequences: raise ActorContractError("model returned wrong candidate count")
        raws=[]
        for index in range(generation.num_return_sequences):
            ids=sequences[index,len(prompt_ids):].detach().cpu().tolist()
            if eos in ids: ids=ids[:ids.index(eos)+1]
            if not ids: raise ActorContractError("empty completion")
            eos_positions=[i for i,x in enumerate(ids) if x==eos]
            if eos_positions and eos_positions != [len(ids)-1]: raise ActorContractError("EOS must occur once at raw tail")
            if not eos_positions and len(ids)!=generation.max_new_tokens: raise ActorContractError("non-EOS completion must reach max_new_tokens")
            raws.append(ids)
        old_all=[]; warped_all=[]; pad=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else eos
        for offset in range(0,len(raws),rescore_micro_batch):
            chunk=raws[offset:offset+rescore_micro_batch]; rows=[prompt_ids+x for x in chunk]; width=max(map(len,rows))
            full=torch.full((len(rows),width),pad,dtype=torch.long,device=device); mask=torch.zeros_like(full)
            for row_index,row in enumerate(rows): full[row_index,:len(row)]=torch.tensor(row,device=device); mask[row_index,:len(row)]=1
            with torch.inference_mode(): logits=model(input_ids=full,attention_mask=mask,use_cache=False,return_dict=True).logits[:,:-1,:]
            targets=full[:,1:]; old=torch.log_softmax(logits.float(),dim=-1).gather(-1,targets.unsqueeze(-1)).squeeze(-1)
            warped=_warped_logprobs(logits,targets,temperature=generation.temperature,top_p=generation.top_p)
            start=len(prompt_ids)-1
            for row_index,ids in enumerate(chunk):
                old_all.append(old[row_index,start:start+len(ids)].detach().cpu().tolist())
                warped_all.append(warped[row_index,start:start+len(ids)].detach().cpu().tolist())
        if is_gpu: sync(device)
        elapsed=time.perf_counter()-started
        after=live_identity(); after.validate()
        if after != expected_identity: raise ActorContractError("live identity changed during atomic capture")
    allocation=elapsed/generation.num_return_sequences; output=[]
    for index,(ids,old,warped) in enumerate(zip(raws,old_all,warped_all,strict=True)):
        if any(not math.isfinite(float(x)) for x in old+warped): raise ActorContractError("sampled token fell outside recomputed sampling support")
        finish="stop" if ids[-1]==eos else "length"
        evidence={"request_id":request_id,"candidate_index":index,"request_seed":request_seed,"prompt_token_ids":prompt_ids,
                  "raw_completion_token_ids":ids,"raw_completion_old_logprobs":old,"raw_completion_sampling_logprobs":warped,
                  "finish_reason":finish,"generation_params_sha256":generation.sha256,"behavior_identity_sha256":canonical_sha256(asdict(expected_identity))}
        output.append(RawGeneration(request_id,index,request_seed,tuple(prompt_ids),tuple(ids),tuple(map(float,old)),tuple(map(float,warped)),finish,
                                    canonical_sha256(evidence),allocation,allocation if is_gpu else 0.0))
    return tuple(output)
