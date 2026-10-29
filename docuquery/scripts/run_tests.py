#!/usr/bin/env python3
"""Test runner for DocuQuery backend test suite."""

import asyncio
from pathlib import Path
import sys
import unittest

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

if __name__ == "__main__":
    test_dir = backend_dir / "tests"
    suite = unittest.defaultTestLoader.discover(str(test_dir), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
