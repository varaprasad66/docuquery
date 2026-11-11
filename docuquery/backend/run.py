"""Local development server with a Windows-compatible PostgreSQL event loop."""
import asyncio
import sys
import uvicorn

if __name__ == "__main__":
    config = uvicorn.Config("app.main:app", host="127.0.0.1", port=8100, loop="asyncio")
    server = uvicorn.Server(config)
    asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
