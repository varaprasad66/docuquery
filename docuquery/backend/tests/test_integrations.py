"""Unit tests for Phase 4 integrations: API key hashing/scoping and webhook
signing/verification. No database required."""

import json
import unittest
from unittest import mock

from app.services import webhooks
from app.services.api_keys import SCOPES, generate_api_key, hash_key, normalize_scopes
from app.services.webhooks import EVENTS, sign, verify_signature


class TestApiKeyMaterial(unittest.TestCase):
    def test_generated_key_shape_and_hash_roundtrip(self):
        raw, prefix, key_hash = generate_api_key()
        self.assertTrue(raw.startswith("dq_"))
        self.assertEqual(prefix, raw[:11])
        self.assertNotIn(raw, key_hash)
        self.assertEqual(key_hash, hash_key(raw))

    def test_keys_are_unique(self):
        seen = {generate_api_key()[0] for _ in range(50)}
        self.assertEqual(len(seen), 50)

    def test_normalize_scopes(self):
        self.assertEqual(normalize_scopes(None), ["read"])
        self.assertEqual(normalize_scopes([]), ["read"])
        self.assertEqual(normalize_scopes(["chat", "read", "chat"]), ["chat", "read"])
        with self.assertRaisesRegex(ValueError, "Unknown scope"):
            normalize_scopes(["admin"])

    def test_scope_catalog(self):
        self.assertEqual(set(SCOPES), {"read", "chat", "upload"})


class TestWebhookSigning(unittest.TestCase):
    def test_sign_and_verify_roundtrip(self):
        secret = "whsec_abc"
        body = json.dumps({"event": "document.ready"}).encode()
        header = sign(secret, body, timestamp="1700000000")
        self.assertTrue(header.startswith("t=1700000000,v1="))
        self.assertTrue(verify_signature(secret, body, header, tolerance_seconds=10**9))

    def test_verify_rejects_tampered_body(self):
        header = sign("whsec_abc", b'{"a": 1}')
        self.assertFalse(verify_signature("whsec_abc", b'{"a": 2}', header, tolerance_seconds=10**9))

    def test_verify_rejects_wrong_secret(self):
        header = sign("whsec_abc", b"x")
        self.assertFalse(verify_signature("whsec_other", b"x", header, tolerance_seconds=10**9))

    def test_verify_rejects_stale_timestamp(self):
        header = sign("whsec_abc", b"x", timestamp="1000000000")
        self.assertFalse(verify_signature("whsec_abc", b"x", header, tolerance_seconds=300))

    def test_verify_rejects_malformed_header(self):
        self.assertFalse(verify_signature("s", b"x", "garbage"))

    def test_event_catalog(self):
        self.assertEqual(set(EVENTS), {"document.ready", "document.failed"})


class TestDeliveryRetries(unittest.TestCase):
    """Retry/backoff logic via a patched _post."""

    def _webhook(self) -> object:
        from app.db.models import Webhook

        import uuid

        return Webhook(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            url="http://8.8.8.8/hook",
            secret="whsec_test",
            events=["document.ready"],
            is_active=True,
        )

    def test_retries_then_succeeds(self):
        webhook = self._webhook()
        responses = [(500, None), (None, "timeout"), (200, None)]
        calls = []

        def fake_post(url, headers, body, timeout):
            calls.append(url)
            return responses[len(calls) - 1]

        with mock.patch.object(webhooks, "_post", side_effect=fake_post), mock.patch.object(
            webhooks.time, "sleep"
        ):
            status_code, error, attempts = webhooks._deliver_with_retries(
                webhook, "document.ready", b"{}"
            )
        self.assertEqual(status_code, 200)
        self.assertIsNone(error)
        self.assertEqual(attempts, 3)

    def test_all_attempts_fail_reports_last_error(self):
        webhook = self._webhook()

        def fake_post(url, headers, body, timeout):
            return (500, None)

        with mock.patch.object(webhooks, "_post", side_effect=fake_post), mock.patch.object(
            webhooks.time, "sleep"
        ):
            status_code, error, attempts = webhooks._deliver_with_retries(
                webhook, "document.ready", b"{}"
            )
        self.assertEqual(status_code, 500)
        self.assertEqual(attempts, 3)

    def test_signature_header_present_on_attempts(self):
        webhook = self._webhook()
        captured = {}

        def fake_post(url, headers, body, timeout):
            captured["signature"] = headers["X-DocuQuery-Signature"]
            captured["event"] = headers["X-DocuQuery-Event"]
            return (200, None)

        with mock.patch.object(webhooks, "_post", side_effect=fake_post):
            webhooks._deliver_with_retries(webhook, "document.ready", b"{}")
        self.assertEqual(captured["event"], "document.ready")
        self.assertTrue(captured["signature"].startswith("t="))


if __name__ == "__main__":
    unittest.main()
