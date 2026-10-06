# Online-v2 zero-advantage diagnosis and repair

The 233 frozen producer `action_value` values are all zero, but this is not a
valid statement that all executed actions had equal return.  The inspected Reap
runtime writes the parent-state value `p_prime` into every candidate's event
start before Lean execution.  In this one-step wave that value is therefore a
state value, not the candidate terminal return.  `compute_search_advantages`
then correctly produced zero from the incorrect inputs; its formula is not the
bug.

The immutable search/verifier/proof receipts contain 49 strictly verified proof
actions and 184 invalid tactics.  The repair keeps those receipts unchanged and
derives the effective action return only after receipt binding in
`build_rollout_samples`: verified proof/disproof `+1`, invalid tactic `-0.1`,
timeout/infrastructure error `0`, unresolved action unchanged from search Q.
Reloading all 20 downloaded join files yields 233 nonzero advantages in all 20
states (49 positive, 184 negative), with repaired wave SHA-256
`ab2cccc55ada03ea1e7298935b5760a9b4bd84b00a256607bd367689c31dcb36`.

The prior Online checkpoint did change actual LoRA tensors, not just metadata:
the runner hashes every trainable tensor name/dtype/shape/byte sequence, and its
pre/post hashes differ.  With PPO loss exactly zero, the change came from CE64
value loss back-propagating through the non-detached policy hidden state, then
KL regularization.  It is not evidence of reward-driven policy improvement.

To prevent recurrence, `OnlineV2Learner` now rejects any wave whose recomputed
advantages are all zero before starting a transaction or optimizer step.  The
focused local regressions pass: Online-v2 79 tests and shared actor bridge 17
tests.
