"""Integration tests for programmatic access: API key lifecycle and scopes,
revocation, webhook delivery with signature verification, and SSRF rejection."""

import json
import uuid

from tests_integration import IntegrationTestCase, make_pdf


class TestApiKeys(IntegrationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.email = f"key-{uuid.uuid4().hex[:8]}@test.io"
        self.user = await self.register(self.email)
        self.token = self.user["access_token"]
        doc = await self.upload_pdf(
            self.token, filename="handbook.pdf",
            text="Annual leave is 25 days per year marker zqkeytest.",
        )
        await self.process_document(doc["id"])
        self.doc_id = doc["id"]

    async def _create_key(self, scopes, name="test key") -> dict:
        r = await self.client.post(
            "/api/keys",
            json={"name": name, "scopes": scopes},
            headers=self.auth_headers(self.token),
        )
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    async def test_create_list_revoke(self):
        created = await self._create_key(["read", "chat"])
        self.assertTrue(created["key"].startswith("dq_"))

        listed = await self.client.get("/api/keys", headers=self.auth_headers(self.token))
        self.assertEqual(listed.status_code, 200)
        entries = [k for k in listed.json() if k["id"] == created["id"]]
        self.assertEqual(len(entries), 1)
        self.assertNotIn("key", entries[0])  # raw key never listed again
        self.assertNotIn("key_hash", entries[0])

        r = await self.client.delete(
            f"/api/keys/{created['id']}", headers=self.auth_headers(self.token)
        )
        self.assertEqual(r.status_code, 204)
        # Second revoke is idempotent 404-free? Key exists but revoked: revoke again → 204
        r2 = await self.client.delete(
            f"/api/keys/{created['id']}", headers=self.auth_headers(self.token)
        )
        self.assertEqual(r2.status_code, 204)

    async def test_revoked_key_fails_closed_with_401(self):
        created = await self._create_key(["read"])
        raw = created["key"]

        ok = await self.client.get(
            "/api/documents", headers=self.auth_headers(raw)
        )
        self.assertEqual(ok.status_code, 200)

        await self.client.delete(
            f"/api/keys/{created['id']}", headers=self.auth_headers(self.token)
        )
        revoked = await self.client.get(
            "/api/documents", headers=self.auth_headers(raw)
        )
        self.assertEqual(revoked.status_code, 401)

    async def test_scoped_key_cannot_use_other_scopes(self):
        read_only = await self._create_key(["read"], "read-only")
        # read works
        r = await self.client.get(
            "/api/documents", headers=self.auth_headers(read_only["key"])
        )
        self.assertEqual(r.status_code, 200)
        # chat denied
        r = await self.client.post(
            "/api/chat",
            json={"question": "How many leave days?"},
            headers=self.auth_headers(read_only["key"]),
        )
        self.assertEqual(r.status_code, 403)
        # upload denied
        r = await self.client.post(
            "/api/documents",
            files={"file": ("x.pdf", b"%PDF-1.7 probe", "application/pdf")},
            headers=self.auth_headers(read_only["key"]),
        )
        self.assertEqual(r.status_code, 403)

    async def test_full_scope_key_can_chat_and_search(self):
        full = await self._create_key(["read", "chat", "upload"])
        r = await self.client.post(
            "/api/search",
            json={"query": "annual leave days"},
            headers=self.auth_headers(full["key"]),
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["results"])

        events = await self.chat_sse("How many annual leave days?", token=full["key"])
        self.assertIn("done", [e["type"] for e in events])

    async def test_unknown_key_fails_closed(self):
        r = await self.client.get(
            "/api/documents", headers=self.auth_headers("dq_totally-unknown-key")
        )
        self.assertEqual(r.status_code, 401)

    async def test_api_key_cannot_manage_keys(self):
        full = await self._create_key(["read", "chat", "upload"])
        r = await self.client.get("/api/keys", headers=self.auth_headers(full["key"]))
        self.assertEqual(r.status_code, 401)


class TestWebhooks(IntegrationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.email = f"wh-{uuid.uuid4().hex[:8]}@test.io"
        self.user = await self.register(self.email)
        self.token = self.user["access_token"]

    async def _create_webhook(self, url="http://8.8.8.8/hook", events=None) -> dict:
        r = await self.client.post(
            "/api/webhooks",
            json={"url": url, "events": events or ["document.ready", "document.failed"]},
            headers=self.auth_headers(self.token),
        )
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    async def test_create_rejects_private_target(self):
        for url in ("http://127.0.0.1/hook", "http://169.254.169.254/x", "ftp://x/hook"):
            r = await self.client.post(
                "/api/webhooks",
                json={"url": url, "events": ["document.ready"]},
                headers=self.auth_headers(self.token),
            )
            self.assertEqual(r.status_code, 422, url)

    async def test_ready_event_delivered_and_signed(self):
        import unittest.mock as mock

        from app.services import webhooks as wh

        created = await self._create_webhook()
        deliveries: list[dict] = []
        received: dict = {}

        def fake_post(url, headers, body, timeout):
            received["headers"] = headers
            received["body"] = body
            return (200, None)

        with mock.patch.object(wh, "_post", side_effect=fake_post):
            doc = await self.upload_pdf(
                self.token, filename="signed.pdf", text="Webhook payload marker zqwebhook."
            )

        self.assertEqual(received["headers"]["X-DocuQuery-Event"], "document.ready")
        payload = json.loads(received["body"])
        self.assertEqual(payload["event"], "document.ready")
        self.assertEqual(payload["data"]["document_name"], "signed.pdf")
        self.assertTrue(
            wh.verify_signature(created["secret"], received["body"],
                                received["headers"]["X-DocuQuery-Signature"])
        )

        # Delivery log shows the successful series.
        r = await self.client.get(
            f"/api/webhooks/{created['id']}/deliveries",
            headers=self.auth_headers(self.token),
        )
        self.assertEqual(r.status_code, 200)
        deliveries = r.json()
        self.assertTrue(deliveries)
        self.assertEqual(deliveries[0]["status"], "delivered")
        self.assertEqual(deliveries[0]["attempts"], 1)

    async def test_failed_event_retries_then_records_failure(self):
        import unittest.mock as mock

        from app.services import webhooks as wh

        created = await self._create_webhook(events=["document.failed"])
        calls = {"n": 0}

        def fake_post(url, headers, body, timeout):
            calls["n"] += 1
            return (500, None)

        with mock.patch.object(wh, "_post", side_effect=fake_post), mock.patch.object(
            wh.time, "sleep"
        ):
            # The upload dispatches processing as a background task, which
            # fails on this garbage PDF and fires document.failed exactly once.
            doc = await self.upload_pdf(
                self.token, filename="doomed.pdf", content=b"%PDF-1.7 garbage"
            )

        self.assertEqual(calls["n"], 3)  # default WEBHOOK_MAX_ATTEMPTS
        r = await self.client.get(
            f"/api/webhooks/{created['id']}/deliveries",
            headers=self.auth_headers(self.token),
        )
        entries = r.json()
        self.assertEqual(entries[0]["status"], "failed")
        self.assertEqual(entries[0]["attempts"], 3)

    async def test_webhook_management_requires_jwt(self):
        r = await self.client.get("/api/webhooks")
        self.assertEqual(r.status_code, 401)
