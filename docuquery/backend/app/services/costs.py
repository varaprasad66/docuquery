"""Token cost calculation for usage analytics."""

from app.config import get_settings


def calculate_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    """Cost of one LLM exchange using the configured per-1k-token prices."""
    s = get_settings()
    cost = (prompt_tokens / 1000) * s.llm_cost_prompt_per_1k + (
        completion_tokens / 1000
    ) * s.llm_cost_completion_per_1k
    return round(cost, 6)


def estimate_cost_saved_usd(prompt_tokens: int, completion_tokens: int) -> float:
    """Cost avoided by serving a cached answer instead of calling the model."""
    return calculate_cost_usd(prompt_tokens, completion_tokens)
