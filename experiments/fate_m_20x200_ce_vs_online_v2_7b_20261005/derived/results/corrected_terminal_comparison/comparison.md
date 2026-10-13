# Guarded single-wave terminal outcome

| policy | solved /40 | pass@1 | pass@2 | pass@4 | evidence |
|---|---:|---:|---:|---:|---|
| initial | 24/40 | 0.250 | 0.500 | 0.600 | independent40-task evaluation |
| corrected CE | 25/40 | 0.275 | 0.525 | 0.625 | independent40-task evaluation |
| Online rollback deployment | 24/40 | 0.250 | 0.500 | 0.600 | initial evidence reused by exact policy identity |

Online update was rejected by the frozen KL guard and exactly rolled back. Its deployed-policy result reuses initial evidence by verified identity, not an independent Online heldout run. CE versus baseline is descriptive single-seed within-family transfer; no accepted CE-versus-Online learning comparison, significance, robustness, or algorithm-superiority claim is supported.

Original protocol and both-accepted-update scope: **INCOMPLETE**.
