"""Unit tests for JWT lifecycle: creation, decoding, expiry, tamper, revocation."""

import time
import unittest
from unittest.mock import patch

import jwt as pyjwt  # noqa: F401  (import guard only)

from app.services import token_store
from app.services.auth import create_access_token, decode_access_token
from app.services.token_store import TokenBlacklist


class TestAccessToken(unittest.TestCase):
    def test_roundtrip_and_jti(self):
        token = create_access_token("11111111-1111-1111-1111-111111111111", "a@b.c")
        payload = decode_access_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(payload["email"], "a@b.c")
        self.assertTrue(payload.get("jti"))
        self.assertEqual(payload.get("typ"), "access")

    def test_garbage_token_rejected(self):
        self.assertIsNone(decode_access_token("not-a-token"))
        self.assertIsNone(decode_access_token(""))

    def test_tampered_token_rejected(self):
        token = create_access_token("11111111-1111-1111-1111-111111111111", "a@b.c")
        tampered = token[:-4] + "AAAA"
        self.assertIsNone(decode_access_token(tampered))

    def test_expired_token_rejected(self):
        with patch("app.services.auth.get_settings") as gs:
            s = gs.return_value
            s.jwt_secret = "test-secret"
            s.jwt_algorithm = "HS256"
            s.jwt_expires_minutes = -1  # already expired
            token = create_access_token("11111111-1111-1111-1111-111111111111", "a@b.c")
        payload = decode_access_token(token)
        self.assertIsNone(payload)


class TestTokenBlacklist(unittest.TestCase):
    def setUp(self):
        # Force the in-memory backend for the entire test (Redis may be live on
        # this host; tests must not read from or pollute it).
        patcher = patch.object(token_store, "_redis_client", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_revoke_then_check(self):
        bl = TokenBlacklist()
        exp = time.time() + 60
        bl.revoke("jti-1", exp)
        self.assertTrue(bl.is_revoked("jti-1"))
        self.assertFalse(bl.is_revoked("jti-2"))

    def test_expired_blacklist_entry_not_revoked(self):
        bl = TokenBlacklist()
        bl.revoke("jti-old", time.time() - 1)
        self.assertFalse(bl.is_revoked("jti-old"))

    def test_memory_bounded(self):
        bl = TokenBlacklist()
        for i in range(10500):
            bl.revoke(f"jti-{i}", time.time() + 3600)
        self.assertLessEqual(len(bl._memory), 10_000)


if __name__ == "__main__":
    unittest.main()
