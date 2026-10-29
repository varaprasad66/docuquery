"""API contract and route validation tests."""

import io
import unittest
from unittest.mock import AsyncMock, MagicMock
import uuid

from fastapi import HTTPException, UploadFile
from pydantic import ValidationError

from app.api.dependencies import get_current_user
from app.api.documents import _pdf_magic
from app.api.schemas import (
    ChatRequest,
    DocumentOut,
    SearchRequest,
    StatsOverview,
    UserLogin,
    UserRegister,
)


class TestAPIContracts(unittest.TestCase):
    def test_pdf_magic_detection(self):
        valid_stream = io.BytesIO(b"%PDF-1.7\nsome binary content")
        valid_file = UploadFile(file=valid_stream, filename="valid.pdf")
        self.assertTrue(_pdf_magic(valid_file))

        invalid_stream = io.BytesIO(b"NOT A PDF")
        invalid_file = UploadFile(file=invalid_stream, filename="fake.pdf")
        self.assertFalse(_pdf_magic(invalid_file))

    def test_schema_validations(self):
        # Valid chat request
        chat_req = ChatRequest(question="What are our vacation policies?", top_k=5)
        self.assertEqual(chat_req.top_k, 5)

        # Invalid top_k (exceeds max 12)
        with self.assertRaises(ValidationError):
            ChatRequest(question="test", top_k=50)

        # Valid user register
        user_reg = UserRegister(email="alice@company.com", password="StrongPassword123")
        self.assertEqual(user_reg.email, "alice@company.com")

        # Invalid email
        with self.assertRaises(ValidationError):
            UserRegister(email="not-an-email", password="pass")

    def test_unauthorized_guard(self):
        coro = get_current_user(user=None)
        try:
            coro.send(None)
            self.fail("Expected HTTPException 401")
        except HTTPException as exc:
            self.assertEqual(exc.status_code, 401)
        except StopIteration:
            self.fail("Coroutine completed without raising HTTPException")


if __name__ == "__main__":
    unittest.main()
