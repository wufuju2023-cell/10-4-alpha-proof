# Orchestration test report

- Date: 2026-10-05
- Command: `python -m unittest discover -s tests -v`
- Result: 23 discovered; 21 passed; 2 platform-specific POSIX tests skipped on Windows; 0 failed.
- Paired integration covered: protocol fairness/freeze, supervisor-to-worker-to-native success, atomic unit-0/unit-1 receipts, and fail-then-native-resume without a same-unit evidence fork.
- Syntax check: `python -m compileall -q src tests` passed.
