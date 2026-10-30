"""Authentication routes: register, login, refresh, logout, and user profile."""

import hashlib
import logging
import secrets
import uuid as uuidlib
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import client_identity, get_current_user_optional_dep, rate_limit_auth
from app.api.schemas import RefreshRequest, TokenResponse, UserLogin, UserOut, UserRegister
from app.config import get_settings
from app.db.models import RefreshToken, User, UserRole
from app.db.session import get_db
from app.services.audit import record_audit
from app.services.auth import create_access_token, hash_password, verify_password
from app.services.rate_limit import LoginLockout

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

lockout = LoginLockout(
    max_attempts=get_settings().login_max_attempts,
    lockout_seconds=get_settings().login_lockout_seconds,
)

GENERIC_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password.",
)


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


async def _issue_refresh_token(db: AsyncSession, user_id: uuidlib.UUID) -> str:
    settings = get_settings()
    token = _new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=_hash_refresh_token(token),
            family_id=uuidlib.uuid4(),
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.refresh_token_expires_days),
        )
    )
    return token


async def _authenticate(body: UserLogin, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.email == body.email.lower()))
    user = result.scalar_one_or_none()
    if user is None:
        # Equalize timing between "no such user" and "wrong password" paths.
        hash_password(body.password)
        raise GENERIC_CREDENTIALS_ERROR
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This account is disabled."
        )
    if not verify_password(body.password, user.password_hash):
        raise GENERIC_CREDENTIALS_ERROR
    return user


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit_auth)])
async def register(
    body: UserRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.email == body.email.lower()))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    settings = get_settings()
    is_admin = body.email.lower() in [e.lower() for e in settings.admin_emails]
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        display_name=body.display_name or body.email.split("@")[0],
        role=UserRole.admin.value if is_admin else UserRole.user.value,
    )
    db.add(user)
    ip, ua = await client_identity(request)
    await record_audit(
        db, "auth.register", actor_email=user.email, resource_type="user", ip=ip, user_agent=ua
    )
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id, user.email)
    refresh = await _issue_refresh_token(db, user.id)
    await db.commit()
    return TokenResponse(access_token=token, refresh_token=refresh, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit_auth)])
async def login(
    body: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip, ua = await client_identity(request)
    lock_key = f"{(body.email or '').lower()}|{ip or ''}"

    remaining = lockout.is_locked(lock_key)
    if remaining > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Try again in {remaining}s.",
            headers={"Retry-After": str(remaining)},
        )

    try:
        user = await _authenticate(body, db)
    except HTTPException:
        lockout.register_failure(lock_key)
        await record_audit(
            db, "auth.login_failed", actor_email=body.email.lower(), ip=ip, user_agent=ua
        )
        await db.commit()
        raise

    lockout.clear(lock_key)
    token = create_access_token(user.id, user.email)
    refresh = await _issue_refresh_token(db, user.id)
    await record_audit(
        db, "auth.login", actor_id=user.id, actor_email=user.email, ip=ip, user_agent=ua
    )
    await db.commit()
    return TokenResponse(access_token=token, refresh_token=refresh, user=UserOut.model_validate(user))


@router.post("/refresh", response_model=TokenResponse, dependencies=[Depends(rate_limit_auth)])
async def refresh(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """Rotate a refresh token. Reuse of an already-rotated/revoked token revokes the
    whole family (theft detection) and requires re-login."""
    token_hash = _hash_refresh_token(body.refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()

    if stored is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token.")

    if stored.revoked or stored.expires_at < datetime.now(timezone.utc):
        # Replayed token: revoke the entire family.
        if stored.revoked:
            await db.execute(
                select(RefreshToken).where(RefreshToken.family_id == stored.family_id)
            )
            for row in (
                await db.execute(
                    select(RefreshToken).where(RefreshToken.family_id == stored.family_id)
                )
            ).scalars():
                row.revoked = True
            await db.commit()
            logger.warning("Refresh token replay detected; family %s revoked", stored.family_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token.")

    user = await db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token.")

    # Rotate: revoke the presented token, issue a new one in the same family.
    stored.revoked = True
    settings = get_settings()
    new_token = _new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_refresh_token(new_token),
            family_id=stored.family_id,
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.refresh_token_expires_days),
        )
    )
    await db.commit()

    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token, refresh_token=new_token, user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: RefreshRequest,
    request: Request,
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the presented refresh token (whole family if it was already revoked)
    and blacklist the caller's access token until its natural expiry."""
    ip, ua = await client_identity(request)

    # 1. Refresh token revocation.
    token_hash = _hash_refresh_token(body.refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    if stored is not None:
        if stored.revoked:
            rows = (
                await db.execute(
                    select(RefreshToken).where(RefreshToken.family_id == stored.family_id)
                )
            ).scalars()
            for row in rows:
                row.revoked = True
        else:
            stored.revoked = True

    # 2. Access token revocation until expiry.
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            from app.services.auth import decode_access_token

            payload = decode_access_token(parts[1])
            if payload and payload.get("jti"):
                from app.services.token_store import token_blacklist

                token_blacklist.revoke(payload["jti"], float(payload.get("exp", 0)))

    await record_audit(
        db,
        "auth.logout",
        actor_id=stored.user_id if stored else None,
        ip=ip,
        user_agent=ua,
    )
    await db.commit()
    return None


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user_optional_dep)):
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
