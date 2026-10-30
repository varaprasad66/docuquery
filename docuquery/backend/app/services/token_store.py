"""Access-token revocation store (jti blacklist).

Revoked access-token IDs are held until their natural expiry. Backend: Redis with TTL;
in-process fallback (bounded) when Redis is unavailable.
"""

import time
from collections import OrderedDict

from app.services.rate_limit import _redis_client

_MEMORY_MAX = 10_000


class TokenBlacklist:
    def __init__(self) -> None:
        self._memory: OrderedDict[str, float] = OrderedDict()  # jti -> expiry epoch

    def revoke(self, jti: str, expires_at_epoch: float) -> None:
        redis = _redis_client()
        if redis is not None:
            try:
                ttl = max(1, int(expires_at_epoch - time.time()))
                redis.setex(f"jti:{jti}", ttl, "1")
                return
            except Exception:
                pass
        self._memory[jti] = expires_at_epoch
        self._memory.move_to_end(jti)
        while len(self._memory) > _MEMORY_MAX:
            self._memory.popitem(last=False)

    def is_revoked(self, jti: str) -> bool:
        redis = _redis_client()
        if redis is not None:
            try:
                return redis.exists(f"jti:{jti}") > 0
            except Exception:
                pass
        self._evict()
        return jti in self._memory

    def _evict(self) -> None:
        now = time.time()
        for jti in list(self._memory):
            if self._memory[jti] <= now:
                del self._memory[jti]


token_blacklist = TokenBlacklist()
