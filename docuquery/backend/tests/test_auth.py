"""Unit tests for authentication and JWT helpers."""

import unittest
import uuid

from app.services.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


class TestAuthService(unittest.TestCase):
    def test_password_hashing_and_verification(self):
        password = "SecurePassword123!"
        hashed = hash_password(password)

        self.assertNotEqual(password, hashed)
        self.assertTrue(verify_password(password, hashed))
        self.assertFalse(verify_password("WrongPassword", hashed))

    def test_jwt_lifecycle(self):
        user_id = uuid.uuid4()
        email = "developer@docuquery.local"

        token = create_access_token(user_id, email)
        self.assertIsInstance(token, str)

        payload = decode_access_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], str(user_id))
        self.assertEqual(payload["email"], email)

    def test_jwt_invalid_token(self):
        invalid_token = "invalid.token.signature"
        payload = decode_access_token(invalid_token)
        self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()
