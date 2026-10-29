"""Dev server entrypoint.

Windows: psycopg's async driver requires a SelectorEventLoop, but uvicorn's
CLI only switches event-loop policies in --reload mode. Setting the policy
here makes `python run.py` work in every mode. On Linux (Docker/prod) the
policy call is a no-op.
"""

import asyncio
from pathlib import Path
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    backend_dir = Path(__file__).resolve().parent
    # Only reload on actual Python code changes in app/, NEVER on uploaded PDFs
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8100,
        reload=True,
        reload_dirs=[str(backend_dir / "app")],
        reload_includes=["*.py"],
        reload_excludes=["uploads/*", "sample_data/*", "*.pdf", "*.log"],
    )
