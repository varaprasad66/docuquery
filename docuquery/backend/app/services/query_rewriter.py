"""Follow-up question rewriter.

Before retrieval, if the conversation has history, the latest question is
rewritten into a fully self-contained search query ("what about page 2?" ->
"What does page 2 of <document> say?") using the LLM. Skipped when there is no
history or the feature is disabled; any failure or timeout falls back to the
original question so chat never breaks because of the rewriter.
"""

import asyncio
import logging

from app.config import get_settings
from app.db.models import Message
from app.services.llm import llm_client

logger = logging.getLogger(__name__)

_REWRITE_SYSTEM = (
    "You rewrite a follow-up question into ONE self-contained search query. "
    "Resolve every pronoun and ellipsis using the conversation history, keep the "
    "user's language, and output ONLY the rewritten query with no explanation."
)

MAX_HISTORY_TURNS = 4


def needs_rewrite(history: list[Message]) -> bool:
    """True when there is prior context to resolve (the just-asked message is
    the last element of history, so at least two messages are required)."""
    s = get_settings()
    return bool(s.query_rewrite_enabled) and len(history) > 1


async def rewrite_question(question: str, history: list[Message]) -> str:
    """Return a standalone version of `question`, or the original on any failure."""
    s = get_settings()
    if not needs_rewrite(history):
        return question

    turns = history[-1 - MAX_HISTORY_TURNS : -1]  # exclude the just-asked question
    transcript = "\n".join(
        f"{'USER' if m.role == 'user' else 'ASSISTANT'}: {m.content[:500]}" for m in turns
    )
    try:
        client = llm_client()
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=s.llm_model,
                messages=[
                    {"role": "system", "content": _REWRITE_SYSTEM},
                    {
                        "role": "user",
                        "content": f"CONVERSATION:\n{transcript}\n\nLATEST QUESTION: {question}",
                    },
                ],
                temperature=0.0,
                max_tokens=200,
                stream=False,
            ),
            timeout=s.query_rewrite_timeout_seconds,
        )
        rewritten = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("Query rewrite failed (%s); using the original question", exc)
        return question

    if not rewritten or len(rewritten) > 1000 or "\n\n" in rewritten:
        return question
    return rewritten
