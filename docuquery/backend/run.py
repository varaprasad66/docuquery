"""Dev server entrypoint.

Windows: psycopg's async driver requires a SelectorEventLoop, but uvicorn's
CLI only switches event-loop policies in --reload mode. Setting the policy
here makes `python run.py` work in every mode. On Linux (Docker/prod) the
policy call is a no-op.
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8100, reload=True)
