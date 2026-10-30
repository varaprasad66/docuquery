"""Product edition gating.

DocuQuery ships in three editions sold to different client segments. The codebase is
one product; the edition setting controls which *feature surfaces* are active:

- **basic**         — core product: upload, chat, search, conversations, basic stats.
- **professional**  — + semantic answer cache (cost optimization), usage quotas,
                      cost analytics & daily series, answer feedback loop,
                      search filters.
- **enterprise**    — + admin console (user management, global analytics, document
                      triage), audit log.

Security fundamentals (tenant isolation, rate limiting, lockout, sanitized errors,
security headers) are enabled in every edition — they are correctness, not features.

A lower edition receives 404 for a higher edition's surface (not 403): we do not
advertise the existence of features a client has not purchased.
"""

from fastapi import HTTPException, status

from app.config import get_settings

BASIC = "basic"
PROFESSIONAL = "professional"
ENTERPRISE = "enterprise"

_EDITION_RANK = {BASIC: 1, PROFESSIONAL: 2, ENTERPRISE: 3}


def current_edition() -> str:
    edition = get_settings().edition.lower()
    if edition not in _EDITION_RANK:
        return ENTERPRISE
    return edition


def edition_rank(edition: str) -> int:
    return _EDITION_RANK.get(edition, 3)


def has_edition(minimum: str) -> bool:
    return edition_rank(current_edition()) >= edition_rank(minimum)


async def require_professional() -> None:
    """FastAPI dependency: surface available from the Professional edition up."""
    if not has_edition(PROFESSIONAL):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found.",
        )


async def require_enterprise() -> None:
    """FastAPI dependency: surface available in the Enterprise edition only."""
    if not has_edition(ENTERPRISE):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found.",
        )
