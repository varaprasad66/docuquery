#!/usr/bin/env python3
"""DocuQuery test runner.

Phase 1 — unit tests (tests/): fast, no database required.
Phase 2 — integration tests (tests_integration/): real Postgres test database
          (docuquery_test, auto-created), deterministic fake AI providers,
          memory-isolated rate-limit backends. Runs in a fresh subprocess so the
          test DATABASE_URL is bound before any app module is imported.

Exit code is non-zero if any phase fails. Integration tests skip cleanly
(with a message) when Postgres is unreachable.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def run_unit() -> int:
    print("=" * 60)
    print("PHASE 1/2 — Unit tests (tests/)")
    print("=" * 60)
    import unittest

    suite = unittest.defaultTestLoader.discover(str(BACKEND_DIR / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def run_integration() -> int:
    print()
    print("=" * 60)
    print("PHASE 2/2 — Integration tests (tests_integration/)")
    print("=" * 60)
    proc = subprocess.run(
        [
            sys.executable, "-m", "unittest", "discover",
            "-s", "tests_integration", "-t", ".",
            "-v",
        ],
        cwd=str(BACKEND_DIR),
    )
    return proc.returncode


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    failures = 0
    if only in ("all", "unit"):
        failures += run_unit()
    if only in ("all", "integration"):
        failures += run_integration()
    sys.exit(1 if failures else 0)
