"""Authentication lifecycle: registration policy, lockout, refresh rotation,
logout revocation, and deactivated-user handling."""

import uuid

from tests_integration import IntegrationTestCase


class TestAuthLifecycle(IntegrationTestCase):
    async def test_register_login_me_roundtrip(self):
        email = f"u-{uuid.uuid4().hex[:8]}@test.io"
        reg = await self.register(email)
        self.assertIn("access_token", reg)
        self.assertIn("refresh_token", reg)
        self.assertEqual(reg["user"]["email"], email)

        me = await self.client.get("/api/auth/me", headers=self.auth_headers(reg["access_token"]))
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["email"], email)

    async def test_duplicate_email_rejected(self):
        email = f"u-{uuid.uuid4().hex[:8]}@test.io"
        await self.register(email)
        r = await self.client.post(
            "/api/auth/register", json={"email": email, "password": "Password12345"}
        )
        self.assertEqual(r.status_code, 409)

    async def test_weak_passwords_rejected(self):
        for weak in ["short1a", "onlylettersnopass", "12345678901", "abcdefghijkl"]:
            r = await self.client.post(
                "/api/auth/register", json={"email": f"w-{uuid.uuid4().hex[:6]}@t.io", "password": weak}
            )
            self.assertEqual(r.status_code, 422, f"password {weak!r} must be rejected")

    async def test_wrong_password_401(self):
        email = f"u-{uuid.uuid4().hex[:8]}@test.io"
        await self.register(email)
        r = await self.client.post(
            "/api/auth/login", json={"email": email, "password": "WrongPass12345"}
        )
        self.assertEqual(r.status_code, 401)

    async def test_lockout_after_repeated_failures(self):
        email = f"u-{uuid.uuid4().hex[:8]}@test.io"
        await self.register(email)
        for _ in range(5):
            r = await self.client.post(
                "/api/auth/login", json={"email": email, "password": "WrongPass12345"}
            )
            self.assertEqual(r.status_code, 401)
        # 6th attempt: locked, even with the CORRECT password.
        r = await self.client.post(
            "/api/auth/login", json={"email": email, "password": "Password12345"}
        )
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r.headers)

    async def test_refresh_rotation(self):
        email = f"u-{uuid.uuid4().hex[:8]}@test.io"
        reg = await self.register(email)
        old_refresh = reg["refresh_token"]

        r = await self.client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
        self.assertEqual(r.status_code, 200)
        new = r.json()
        self.assertNotEqual(new["refresh_token"], old_refresh)

        # Old (rotated) token reuse -> 401, and it revokes the whole family.
        r = await self.client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
        self.assertEqual(r.status_code, 401)

        # The rotated successor is dead too (family revoked after theft signal).
        r = await self.client.post("/api/auth/refresh", json={"refresh_token": new["refresh_token"]})
        self.assertEqual(r.status_code, 401)

    async def test_logout_revokes_access_token(self):
        email = f"u-{uuid.uuid4().hex[:8]}@test.io"
        reg = await self.register(email)
        token = reg["access_token"]
        headers = self.auth_headers(token)

        me = await self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(me.status_code, 200)

        r = await self.client.post(
            "/api/auth/logout", json={"refresh_token": reg["refresh_token"]}, headers=headers
        )
        self.assertEqual(r.status_code, 204)

        me = await self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(me.status_code, 401, "Access token must be revoked after logout")

    async def test_deactivated_user_becomes_anonymous(self):
        from sqlalchemy import select

        from app.db.models import User
        from app.db.session import SessionLocal

        email = f"u-{uuid.uuid4().hex[:8]}@test.io"
        reg = await self.register(email)
        headers = self.auth_headers(reg["access_token"])

        async with SessionLocal() as db:
            row = (await db.execute(select(User).where(User.email == email))).scalar_one()
            row.is_active = False
            await db.commit()

        me = await self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(me.status_code, 401)

    async def test_case_insensitive_email_login(self):
        email = f"Case{uuid.uuid4().hex[:8]}@Test.IO"
        await self.register(email.lower())
        r = await self.client.post(
            "/api/auth/login", json={"email": email.upper(), "password": "Password12345"}
        )
        self.assertEqual(r.status_code, 200)
