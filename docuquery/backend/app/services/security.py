"""Authorization helpers: deny-by-default access decisions for documents.

Access rules:
- read:    owner, admin, or public-visibility documents
- mutate:  owner or admin only — never a guest
- guests:  read explicitly public content only; any mutation -> 401
- private documents of other users return 404 (existence is not leaked)

There is no shared "guest space": documents with no owner are treated like any
other private document — invisible to guests and to other tenants. Guest
surface = explicitly public documents (read-only).
"""

from uuid import UUID

from fastapi import HTTPException, status

from app.config import get_settings
from app.db.models import Document, DocumentVisibility, User, UserRole


def is_admin(user: User | None) -> bool:
    return user is not None and user.role == UserRole.admin.value


def _is_public(doc: Document) -> bool:
    return doc.visibility == DocumentVisibility.public.value


def can_read(user: User | None, doc: Document) -> bool:
    if is_admin(user):
        return True
    if get_settings().public_document_sharing_enabled and _is_public(doc):
        return True
    if user is None:
        return False
    return doc.owner_id == user.id


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

    Guest scope = explicitly public documents only. Unowned documents are not a
    shared guest space; a guest never matches them.
    """
    if not get_settings().public_document_sharing_enabled:
        return "FALSE" if owner_id is None else "d.owner_id = CAST(:owner_id AS uuid)"
    if owner_id is None:
        return "d.visibility = 'public'"
    return "(d.owner_id = CAST(:owner_id AS uuid) OR d.visibility = 'public')"
