"""Authorization helpers: deny-by-default access decisions for documents.

Access rules:
- read:    owner, admin, public-visibility documents, or legacy unowned (public) docs
- mutate:  owner or admin only — never a guest
- guests:  read public content only; any mutation -> 401
- private documents of other users return 404 (existence is not leaked)
"""

from uuid import UUID

from fastapi import HTTPException, status

from app.db.models import Document, DocumentVisibility, User, UserRole


def is_admin(user: User | None) -> bool:
    return user is not None and user.role == UserRole.admin.value


def _is_public(doc: Document) -> bool:
    return doc.visibility == DocumentVisibility.public.value


def _is_guest_space(doc: Document) -> bool:
    """Unowned private documents = guest uploads. Visible to guests (their space)
    and admins only — never to other authenticated tenants."""
    return doc.owner_id is None


def can_read(user: User | None, doc: Document) -> bool:
    if is_admin(user):
        return True
    if _is_public(doc):
        return True
    if _is_guest_space(doc):
        return user is None
    return user is not None and doc.owner_id == user.id


def can_mutate(user: User | None, doc: Document) -> bool:
    if user is None:
        return False
    if is_admin(user):
        return True
    return doc.owner_id == user.id


def ensure_document_read(user: User | None, doc: Document) -> None:
    if not can_read(user, doc):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")


def ensure_document_mutate(user: User | None, doc: Document) -> None:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for this action.",
        )
    if not can_mutate(user, doc):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")


def scope_clause(owner_id: UUID | None) -> str:
    """SQL fragment (with named binds :owner_id) limiting retrieval to documents the
    requester may see. Used by every retrieval query — the tenant-isolation control.

    Guest scope = public documents + the "guest space" (unowned documents, which
    includes guest uploads and the seeded demo corpus). Authenticated users never
    match the unowned branch, so guest uploads stay invisible to accounts.
    """
    if owner_id is None:
        return "(d.visibility = 'public' OR d.owner_id IS NULL)"
    return "(d.owner_id = CAST(:owner_id AS uuid) OR d.visibility = 'public')"
