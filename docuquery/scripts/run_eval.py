#!/usr/bin/env python3
"""CLI wrapper to run the DocuQuery RAG golden eval harness."""

import asyncio
from pathlib import Path
import sys

# Ensure backend app is on sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

# Psycopg's async driver requires the selector event loop on Windows (same
# requirement as run.py and the integration test harness).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.evals.eval_runner import run_evaluation

if __name__ == "__main__":
    from app.config import get_settings
    from app.evals.eval_runner import run_judged_evaluation

    top_k = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    summary = asyncio.run(run_evaluation(top_k=top_k))
    if get_settings().eval_judge_enabled:
        asyncio.run(run_judged_evaluation(top_k=top_k))
    # Eval gate: fail when hit@6 drops below the configured threshold.
    sys.exit(0 if summary.get("gate_passed", False) else 1)
