"""Best-effort local Ollama context warning, with no secret/text logging."""
import logging
from urllib.parse import urlsplit
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)


async def check_ollama_context():
    s = get_settings()
    url = urlsplit(s.llm_base_url)
    if not s.ollama_context_check or url.hostname not in ("localhost", "127.0.0.1", "host.docker.internal"):
        return
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.post(f"{url.scheme}://{url.netloc}/api/show", json={"model": s.llm_model})
            response.raise_for_status()
            data = response.json()
        import re
        match = re.search(r"num_ctx\s+(\d+)", data.get("parameters", ""))
        if match and int(match[1]) < s.llm_context_budget_tokens:
            logger.warning("reason=ollama_context_too_small context_tokens=%d budget_tokens=%d", int(match[1]), s.llm_context_budget_tokens)
        elif not match:
            logger.warning("reason=ollama_context_unknown budget_tokens=%d", s.llm_context_budget_tokens)
    except Exception:
        return
