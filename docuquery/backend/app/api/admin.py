"""Enterprise admin console API: users, global analytics, audit log, document triage.

Every route requires the admin role AND the enterprise edition. Non-admins and lower
editions receive 404 (the surface is not advertised).
"""

import logging
import uuid as uuidlib

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_admin_user
from app.api.schemas import AdminUserOut, AdminUserPatch, AuditOut, DocumentOut
from app.db.models import AuditLog, Document, DocumentStatus, User
from app.db.session import get_db
from app.editions import require_enterprise
from app.services.audit import record_audit
from app.workers.tasks import process_document, process_document_core

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_enterprise), Depends(get_admin_user)],
)


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.created_at.desc()).limit(500))
    return list(result.scalars().all())


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def patch_user(
    user_id: uuidlib.UUID,
    body: AdminUserPatch,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if user.id == admin.id and body.is_active is False:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "You cannot disable your own account.")
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.role is not None:
        user.role = body.role
    if body.display_name is not None:
        user.display_name = body.display_name
    await record_audit(
        db, "admin.user_patch", actor_id=admin.id, actor_email=admin.email,
        resource_type="user", resource_id=str(user.id), detail=body.model_dump(exclude_none=True),
    )
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/documents", response_model=list[DocumentOut])
async def list_all_documents(
    status_filter: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Document).order_by(Document.created_at.desc()).limit(500)
    if status_filter:
        query = query.where(Document.status == status_filter)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/documents/{document_id}/reprocess", response_model=DocumentOut)
async def reprocess_document(
    document_id: uuidlib.UUID,
    background_tasks: BackgroundTasks,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    doc.status = DocumentStatus.processing
    doc.error = None
    doc.progress = 0.05
    await record_audit(
        db, "admin.doc_reprocess", actor_id=admin.id, actor_email=admin.email,
        resource_type="document", resource_id=str(doc.id),
    )
    await db.commit()
    await db.refresh(doc)
    try:
        process_document.delay(str(doc.id))
    except Exception:
        background_tasks.add_task(process_document_core, str(doc.id))
    return doc


@router.get("/audit", response_model=list[AuditOut])
async def list_audit(
    action: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(limit, 500))
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if action:
        query = query.where(AuditLog.action == action)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/overview")
async def admin_overview(db: AsyncSession = Depends(get_db)):
    """Global numbers for the admin dashboard."""
    users_total = (await db.execute(select(func.count(User.id)))).scalar_one()
    users_active = (
        await db.execute(select(func.count(User.id)).where(User.is_active.is_(True)))
    ).scalar_one()
    docs_total = (await db.execute(select(func.count(Document.id)))).scalar_one()
    docs_failed = (
        await db.execute(select(func.count(Document.id)).where(Document.status == "failed"))
    ).scalar_one()
    audits = (await db.execute(select(func.count(AuditLog.id)))).scalar_one()
    return {
        "users_total": int(users_total),
        "users_active": int(users_active),
        "documents_total": int(docs_total),
        "documents_failed": int(docs_failed),
        "audit_events": int(audits),
    }
