#!/usr/bin/env python3
"""Terminal entrypoint for the fail-closed Reap candidate-facts collector."""

from fate_reap.candidate_facts import main


if __name__ == "__main__":
    raise SystemExit(main())
