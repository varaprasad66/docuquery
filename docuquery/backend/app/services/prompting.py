"""Bound prompt size before assigning final excerpt numbers."""
import logging
from app.config import get_settings
from app.services.llm import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def build_prompt(question, standalone, history, sources, context_builder):
    s = get_settings()
    selected = list(sources)
    prior = list(history[-s.history_limit:]) if s.history_limit else []

    def assemble():
        context = context_builder(selected) if selected else "NO EXCERPTS AVAILABLE."
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += [{"role": m.role, "content": m.content[:s.history_max_chars]} for m in prior]
        rewritten = f"\nSTANDALONE QUESTION: {standalone}" if s.answer_include_rewritten_question else ""
        messages.append({"role": "user", "content": f"CONTEXT:\n{context}\n\nORIGINAL QUESTION: {question}{rewritten}\n\nAnswer using only the context. Always cite as [n], using only the supplied excerpt numbers."})
        return context, messages, sum(len(m["content"]) for m in messages)

    context, messages, chars = assemble()
    dropped = 0
    while s.prompt_budget_enabled and (chars + 3)//4 + 8*len(messages) > s.llm_context_budget_tokens:
        if selected:
            selected.pop()
        elif prior:
            prior.pop(0)
        else:
            raise ValueError("prompt_budget_exceeded")
        dropped += 1
        context, messages, chars = assemble()
    if dropped:
        logger.warning("reason=prompt_truncated dropped=%d prompt_chars=%d approximate_tokens=%d", dropped, chars, (chars+3)//4)
    return selected, context, messages, chars
