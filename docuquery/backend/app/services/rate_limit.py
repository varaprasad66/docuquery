"""Distributed sliding-window rate limiting and login lockout.

Backend: Redis sorted sets (correct across workers/replicas, bounded memory) with a
transparent in-process fallback when Redis is unreachable — the fallback keeps a hard
cap on tracked identities so it cannot grow unboundedly.
"""

import logging
import time
from collections import OrderedDict

from redis import Redis

from app.config import get_settings

logger = logging.getLogger(__name__)

_MEMORY_MAX_IDENTITIES = 10_000


def _redis_client() -> Redis | None:
    try:
        client = Redis.from_url(
            get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1, db=5
        )
        client.ping()
        return client
    except Exception:
        return None


class RateLimiter:
    """Sliding window limiter keyed by explicit identity strings."""

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._redis: Redis | None = None
        self._redis_checked = False
        # Bounded LRU for the no-Redis fallback.
        self._memory: OrderedDict[str, list[float]] = OrderedDict()

    def _get_redis(self) -> Redis | None:
        if not self._redis_checked:
            self._redis = _redis_client()
            self._redis_checked = True
            if self._redis is None:
                logger.warning("Redis unavailable for rate limiting; using in-process fallback")
        return self._redis

    def _check_memory(self, key: str, now: float) -> tuple[bool, float]:
        window_start = now - self.window_seconds
        stamps = [t for t in self._memory.get(key, []) if t > window_start]
        allowed = len(stamps) < self.max_requests
        if allowed:
            stamps.append(now)
        self._memory[key] = stamps
        self._memory.move_to_end(key)
        while len(self._memory) > _MEMORY_MAX_IDENTITIES:
            self._memory.popitem(last=False)
        retry_after = max(1, int(self.window_seconds - (now - stamps[0]))) if stamps else 1
        return allowed, retry_after if not allowed else 0

    def check(self, key: str) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds). retry_after is 0 when allowed."""
        now = time.time()
        redis = self._get_redis()
        if redis is not None:
            try:
                redis_key = f"rl:{key}"
                window_start = now - self.window_seconds
                pipe = redis.pipeline()
                pipe.zremrangebyscore(redis_key, 0, window_start)
                pipe.zcard(redis_key)
                pipe.zadd(redis_key, {str(now): now})
                pipe.expire(redis_key, self.window_seconds + 1)
                _, count, _, _ = pipe.execute()
                if count >= self.max_requests:
                    oldest = redis.zrange(redis_key, 0, 0, withscores=True)
                    retry_after = (
                        max(1, int(self.window_seconds - (now - oldest[0][1])))
                        if oldest
                        else 1
                    )
                    return False, retry_after
                return True, 0
            except Exception:
                logger.warning("Redis rate-limit check failed; falling back to memory")
                self._redis = None
        return self._check_memory(key, now)


class LoginLockout:
    """Progressive lockout: N failed attempts within the window locks the identity."""

    def __init__(self, max_attempts: int, lockout_seconds: int):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._memory: dict[str, tuple[int, float]] = {}

    def _get_redis(self) -> Redis | None:
        return _redis_client()

    def is_locked(self, key: str) -> int:
        """Returns remaining lockout seconds (0 if not locked)."""
        redis = self._get_redis()
        if redis is not None:
            try:
                ttl = redis.ttl(f"lock:{key}")
                return ttl if ttl and ttl > 0 else 0
            except Exception:
                pass
        entry = self._memory.get(key)
        if entry:
            count, locked_until = entry
            remaining = locked_until - time.time()
            if remaining > 0:
                return int(remaining)
        return 0

    def register_failure(self, key: str) -> None:
        redis = self._get_redis()
        if redis is not None:
            try:
                redis_key = f"lock:{key}"
                count = redis.incr(redis_key)
                if count == 1:
                    redis.expire(redis_key, self.lockout_seconds)
                if count >= self.max_attempts:
                    redis.expire(redis_key, self.lockout_seconds)
                return
            except Exception:
                pass
        count, _ = self._memory.get(key, (0, 0.0))
        count += 1
        locked_until = time.time() + self.lockout_seconds if count >= self.max_attempts else 0.0
        self._memory[key] = (count, locked_until)

    def clear(self, key: str) -> None:
        redis = self._get_redis()
        if redis is not None:
            try:
                redis.delete(f"lock:{key}")
            except Exception:
                pass
        self._memory.pop(key, None)


def rate_limit_dependency(scope: str, max_requests: int | None = None):
    """FastAPI dependency factory. Identity: user id when a valid token is present,
    otherwise client IP. Import inside api modules to avoid circular imports."""
    from fastapi import Header, HTTPException, Request, status

    from app.services.auth import decode_access_token

    settings = get_settings()
    configured = {
        "auth": settings.rate_limit_auth,
        "chat": settings.rate_limit_chat,
        "upload": settings.rate_limit_upload,
        "search": settings.rate_limit_search,
    }
    limit = max_requests if max_requests is not None else configured.get(scope, 60)
    limiter = RateLimiter(max_requests=limit, window_seconds=settings.rate_limit_window_seconds)

    async def dependency(
        request: Request, authorization: str | None = Header(None)
    ) -> None:
        identity = None
        if authorization:
            parts = authorization.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                payload = decode_access_token(parts[1])
                identity = payload.get("sub") if payload else None
        key_identity = identity or (request.client.host if request.client else "unknown")
        allowed, retry_after = limiter.check(f"{scope}:{key_identity}")
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Retry in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )

    dependency.limiter = limiter  # exposed for tests/ops introspection
    return dependency
