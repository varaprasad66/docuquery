"""LOCAL TEST ONLY: real test database, deterministic fake AI. Never deploy this."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ["USE_CELERY"] = "False"
os.environ["QUERY_REWRITE_ENABLED"] = "False"
from tests_integration import SCHEMA_READY, force_memory_backends, patch_ai_fakes, patch_embeds_everywhere
if not SCHEMA_READY:
    raise SystemExit("Disposable test database unavailable")
force_memory_backends()
patch_ai_fakes()
patch_embeds_everywhere()
import uvicorn
import asyncio
server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=8100, loop="asyncio"))
asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
