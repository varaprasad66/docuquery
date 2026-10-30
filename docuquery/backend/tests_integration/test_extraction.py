"""Integration tests for the structured extraction API (professional edition):
sync run with fake LLM (valid / invalid-then-fixed / failing), async job mode
with the status endpoint, and edition gating."""

import io
import json
import unittest.mock as mock
import uuid

from tests_integration import IntegrationTestCase, make_pdf


def _payload(data: dict, confidence: str = "high", sources: dict | None = None) -> str:
    return json.dumps({"data": data, "confidence": confidence, "field_sources": sources or {}})


class TestExtractionAPI(IntegrationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.email = f"ex-{uuid.uuid4().hex[:8]}@test.io"
        self.user = await self.register(self.email)
        self.token = self.user["access_token"]
        doc = await self.upload_pdf(
            self.token,
            filename="invoice.pdf",
            text="Invoice INV-2026-042 from Acme Corp. Total amount due: 1250.00 USD. Due 2026-12-01.",
        )
        await self.process_document(doc["id"])
        self.doc_id = doc["id"]

    def _patch_ask(self, responder):
        """Patch the LLM client used by the extract endpoints."""
        import app.api.extract as extract_api

        class _Completions:
            async def create(self, **kwargs):
                return mock.Mock(
                    choices=[mock.Mock(message=mock.Mock(content=responder(kwargs)))]
                )

        class _Client:
            chat = mock.Mock()
            chat.completions = _Completions()

        return mock.patch.object(extract_api, "llm_client", return_value=_Client())

    async def test_sync_extraction_valid(self):
        with self._patch_ask(
            lambda kwargs: _payload(
                {"invoice_number": "INV-2026-042", "total_amount": 1250.0},
                "high",
                {"invoice_number": 1, "total_amount": 1},
            )
        ):
            r = await self.client.post(
                "/api/extract",
                json={"document_ids": [self.doc_id], "template": "invoice"},
                headers=self.auth_headers(self.token),
            )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["data"]["invoice_number"], "INV-2026-042")
        self.assertEqual(body["confidence"], "high")
        self.assertEqual(body["template"], "invoice")
        self.assertEqual(body["citations"][0]["document_name"], "invoice.pdf")

    async def test_sync_extraction_invalid_then_fixed(self):
        calls = {"n": 0}

        def responder(kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _payload({"invoice_number": "INV-2026-042", "total_amount": "NaN-ish"})
            return _payload({"invoice_number": "INV-2026-042", "total_amount": 1250.0})

        with self._patch_ask(responder):
            r = await self.client.post(
                "/api/extract",
                json={"document_ids": [self.doc_id], "template": "invoice"},
                headers=self.auth_headers(self.token),
            )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["total_amount"], 1250.0)
        self.assertEqual(calls["n"], 2)

    async def test_sync_extraction_always_invalid_422(self):
        with self._patch_ask(lambda kwargs: "no json here"):
            r = await self.client.post(
                "/api/extract",
                json={"document_ids": [self.doc_id], "template": "invoice"},
                headers=self.auth_headers(self.token),
            )
        self.assertEqual(r.status_code, 422)
        self.assertIn("schema validation", r.json()["detail"])

    async def test_custom_schema(self):
        schema = {"type": "object", "properties": {"company": {"type": ["string", "null"]}}}
        with self._patch_ask(
            lambda kwargs: _payload({"company": "Acme Corp"}, "medium", {"company": 1})
        ):
            r = await self.client.post(
                "/api/extract",
                json={"document_ids": [self.doc_id], "json_schema": schema},
                headers=self.auth_headers(self.token),
            )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["company"], "Acme Corp")
        self.assertEqual(r.json()["template"], "custom")

    async def test_template_listed(self):
        r = await self.client.get("/api/extract/templates", headers=self.auth_headers(self.token))
        self.assertEqual(r.status_code, 200)
        names = set(r.json())
        self.assertEqual(names, {"invoice", "contract_clauses"})

    async def test_async_job_mode_completes(self):
        with self._patch_ask(
            lambda kwargs: _payload({"invoice_number": "INV-2026-042"}, "high")
        ):
            r = await self.client.post(
                "/api/extract/jobs",
                json={"document_ids": [self.doc_id], "template": "invoice"},
                headers=self.auth_headers(self.token),
            )
        self.assertEqual(r.status_code, 201, r.text)
        job_id = r.json()["id"]

        job = None
        for _ in range(40):
            rr = await self.client.get(
                f"/api/extract/jobs/{job_id}", headers=self.auth_headers(self.token)
            )
            self.assertEqual(rr.status_code, 200)
            job = rr.json()
            if job["status"] in ("completed", "failed"):
                break
            import asyncio

            await asyncio.sleep(0.1)
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertEqual(job["result"]["data"]["invoice_number"], "INV-2026-042")

    async def test_async_job_failure_recorded(self):
        # The worker path builds a sync client via app.services.llm.sync_llm_client.
        import app.services.llm as llm_svc

        def responder(kwargs):
            return "still not json"

        class _SyncCompletions:
            def create(self, **kwargs):
                return mock.Mock(
                    choices=[mock.Mock(message=mock.Mock(content=responder(kwargs)))]
                )

        class _SyncClient:
            chat = mock.Mock()
            chat.completions = _SyncCompletions()

        with mock.patch.object(llm_svc, "sync_llm_client", return_value=_SyncClient()):
            r = await self.client.post(
                "/api/extract/jobs",
                json={"document_ids": [self.doc_id], "template": "invoice"},
                headers=self.auth_headers(self.token),
            )
        self.assertEqual(r.status_code, 201, r.text)
        job_id = r.json()["id"]

        job = None
        for _ in range(40):
            rr = await self.client.get(
                f"/api/extract/jobs/{job_id}", headers=self.auth_headers(self.token)
            )
            job = rr.json()
            if job["status"] in ("completed", "failed"):
                break
            import asyncio

            await asyncio.sleep(0.1)
        self.assertEqual(job["status"], "failed")
        self.assertTrue(job["error"])

    async def test_other_users_job_is_404(self):
        other = await self.register(f"other-{uuid.uuid4().hex[:8]}@test.io")
        with self._patch_ask(lambda kwargs: _payload({"invoice_number": None})):
            r = await self.client.post(
                "/api/extract/jobs",
                json={"document_ids": [self.doc_id], "template": "invoice"},
                headers=self.auth_headers(self.token),
            )
        job_id = r.json()["id"]
        rr = await self.client.get(
            f"/api/extract/jobs/{job_id}", headers=self.auth_headers(other["access_token"])
        )
        self.assertEqual(rr.status_code, 404)

    async def test_unready_document_rejected(self):
        # A document whose processing failed is never "ready" for extraction.
        broken = await self.upload_pdf(
            self.token, filename="broken.pdf", content=b"%PDF-1.7 garbage not a real pdf"
        )
        with self._patch_ask(lambda kwargs: _payload({})):
            r = await self.client.post(
                "/api/extract",
                json={"document_ids": [broken["id"]], "template": "invoice"},
                headers=self.auth_headers(self.token),
            )
        self.assertEqual(r.status_code, 409)

    async def test_gated_on_basic_edition(self):
        from unittest.mock import patch

        import app.editions

        with patch.object(app.editions, "current_edition", return_value="basic"):
            r = await self.client.post(
                "/api/extract",
                json={"document_ids": [self.doc_id], "template": "invoice"},
                headers=self.auth_headers(self.token),
            )
        self.assertEqual(r.status_code, 404)  # lower editions see 404, not 403
