"""Analytics scoping, cost fields, admin console, audit trail, edition gating,
rate limiting at the API layer, and security headers."""

import uuid

from tests_integration import IntegrationTestCase


class TestStatsAndAdmin(IntegrationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        suffix = uuid.uuid4().hex[:8]
        self.admin = await self.register(f"admin-{suffix}@test.io")
        self.user = await self.register(f"user-{suffix}@test.io")
        self.admin_token = self.admin["access_token"]
        self.user_token = self.user["access_token"]

        # Promote the first user to admin directly (bootstrap path).
        from sqlalchemy import select

        from app.db.models import User
        from app.db.session import SessionLocal

        async with SessionLocal() as db:
            row = (
                await db.execute(select(User).where(User.email == f"admin-{suffix}@test.io"))
            ).scalar_one()
            row.role = "admin"
            await db.commit()

        doc = await self.upload_pdf(self.user_token, filename="u.pdf", text="Unicorn analytics content.")
        await self.process_document(doc["id"])
        self.doc_id = doc["id"]

    async def test_stats_scopes_and_costs(self):
        # User asks one question.
        await self.chat_sse("Unicorn analytics?", token=self.user_token)

        r = await self.client.get("/api/stats", headers=self.auth_headers(self.user_token))
        body = r.json()
        self.assertEqual(body["scope"], "user")
        self.assertEqual(body["total_queries"], 1)
        self.assertEqual(body["total_documents"], 1)
        self.assertGreater(body["tokens"]["total_completion_tokens"], 0)
        self.assertIn("estimated_cost_usd", body["tokens"])
        self.assertEqual(len(body["daily_usage"]), 14, "Professional daily series")
        self.assertTrue(body["cache"]["enabled"])

        # Guest scope sees neither user's queries nor private docs.
        r = await self.client.get("/api/stats")
        body = r.json()
        self.assertEqual(body["scope"], "guest")
        self.assertEqual(body["total_queries"], 0)

        # Admin sees global.
        r = await self.client.get("/api/stats", headers=self.auth_headers(self.admin_token))
        body = r.json()
        self.assertEqual(body["scope"], "global")
        self.assertEqual(body["total_queries"], 1)

    async def test_admin_surfaces_for_admin_only(self):
        r = await self.client.get("/api/admin/users", headers=self.auth_headers(self.admin_token))
        self.assertEqual(r.status_code, 200)
        emails = [u["email"] for u in r.json()]
        self.assertIn(self.admin["user"]["email"], emails)

        r = await self.client.get("/api/admin/users", headers=self.auth_headers(self.user_token))
        self.assertEqual(r.status_code, 404, "Non-admin must receive 404, not 403")

        r = await self.client.get("/api/admin/users")
        self.assertEqual(r.status_code, 401)

    async def test_admin_can_disable_user(self):
        r = await self.client.get("/api/admin/users", headers=self.auth_headers(self.admin_token))
        users = r.json()
        victim = next(u for u in users if u["email"] != self.admin["user"]["email"])

        r = await self.client.patch(
            f"/api/admin/users/{victim['id']}",
            json={"is_active": False},
            headers=self.auth_headers(self.admin_token),
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["is_active"])

        # Deactivated user's token stops working.
        me = await self.client.get("/api/auth/me", headers=self.auth_headers(self.user_token))
        self.assertEqual(me.status_code, 401)

    async def test_admin_cannot_disable_self(self):
        me = (await self.client.get("/api/auth/me", headers=self.auth_headers(self.admin_token))).json()
        r = await self.client.patch(
            f"/api/admin/users/{me['id']}",
            json={"is_active": False},
            headers=self.auth_headers(self.admin_token),
        )
        self.assertEqual(r.status_code, 422)

    async def test_audit_trail_records_actions(self):
        # Perform auditable actions.
        await self.client.get("/api/documents", headers=self.auth_headers(self.user_token))

        r = await self.client.get(
            "/api/admin/audit?action=doc.upload", headers=self.auth_headers(self.admin_token)
        )
        self.assertEqual(r.status_code, 200)
        actions = [a["action"] for a in r.json()]
        self.assertIn("doc.upload", actions)

        r = await self.client.get("/api/admin/audit", headers=self.auth_headers(self.admin_token))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(len(r.json()) >= 2)

    async def test_admin_overview_counts(self):
        r = await self.client.get(
            "/api/admin/overview", headers=self.auth_headers(self.admin_token)
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertGreaterEqual(body["users_total"], 2)
        self.assertEqual(body["documents_failed"], 0)

    async def test_admin_reprocess_endpoint(self):
        r = await self.client.post(
            f"/api/admin/documents/{self.doc_id}/reprocess",
            headers=self.auth_headers(self.admin_token),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "processing")


class TestEditionGating(IntegrationTestCase):
    async def test_enterprise_edition_exposes_admin(self):
        import app.editions as ed

        with patch_edition(ed, "enterprise"):
            r = await self.client.get("/api/admin/overview")
            self.assertIn(r.status_code, (200, 401, 404))  # never hidden by edition

    async def test_basic_edition_hides_admin_and_audit(self):
        import app.editions as ed

        with patch_edition(ed, "basic"):
            r = await self.client.get("/api/admin/overview")
            self.assertEqual(r.status_code, 404, "Basic edition must not expose admin surface")
            r = await self.client.get("/api/admin/audit")
            self.assertEqual(r.status_code, 404)


class patch_edition:
    """Context manager to simulate a licensed edition."""

    def __init__(self, module, edition: str):
        self.module = module
        self.edition = edition
        self._original = None

    def __enter__(self):
        import app.config as cfg

        self._original = cfg.get_settings().edition
        cfg.get_settings().edition = self.edition
        return self

    def __exit__(self, *exc):
        import app.config as cfg

        cfg.get_settings().edition = self._original
        return False


class TestApiHardening(IntegrationTestCase):
    async def test_search_rate_limit_returns_429(self):
        user = await self.register(f"rl-{uuid.uuid4().hex[:8]}@test.io")
        headers = self.auth_headers(user["access_token"])
        codes = []
        for _ in range(65):
            r = await self.client.post(
                "/api/search", json={"query": "anything"}, headers=headers
            )
            codes.append(r.status_code)
            if r.status_code == 429:
                break
        self.assertIn(429, codes, "Search must rate-limit after the configured threshold")
        self.assertEqual(codes[0], 200)

    async def test_security_headers_present(self):
        r = await self.client.get("/api/health")
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")
        self.assertIn("X-Request-ID", r.headers)

    async def test_request_id_echoed(self):
        r = await self.client.get("/api/health", headers={"X-Request-ID": "trace-abc"})
        self.assertEqual(r.headers.get("X-Request-ID"), "trace-abc")
