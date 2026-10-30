"""Unit tests for the deny-by-default access-control matrix."""

import unittest
import uuid
from unittest.mock import MagicMock

from app.db.models import Document, DocumentStatus, User
from app.services.security import (
    can_mutate,
    can_read,
    is_admin,
    scope_clause,
)


def _user(role: str = "user") -> User:
    u = User(email=f"{uuid.uuid4().hex}@test.io", password_hash="x", role=role)
    u.id = uuid.uuid4()
    return u


def _doc(owner_id: uuid.UUID | None, visibility: str) -> Document:
    d = Document(
        original_name="doc.pdf",
        stored_path="/tmp/x.pdf",
        status=DocumentStatus.ready,
        owner_id=owner_id,
        visibility=visibility,
    )
    return d


class TestAccessMatrix(unittest.TestCase):
    def setUp(self):
        self.admin = _user("admin")
        self.alice = _user("user")
        self.bob = _user("user")
        self.alice_private = _doc(self.alice.id, "private")
        self.alice_public = _doc(self.alice.id, "public")
        self.legacy_unowned = _doc(None, "public")
        self.bob_private = _doc(self.bob.id, "private")

    # --- read matrix ---
    def test_owner_reads_own_private(self):
        self.assertTrue(can_read(self.alice, self.alice_private))

    def test_other_user_cannot_read_private(self):
        self.assertFalse(can_read(self.bob, self.alice_private))

    def test_guest_cannot_read_private(self):
        self.assertFalse(can_read(None, self.alice_private))

    def test_admin_reads_everything(self):
        self.assertTrue(can_read(self.admin, self.alice_private))
        self.assertTrue(can_read(self.admin, self.bob_private))

    def test_public_readable_by_all(self):
        self.assertTrue(can_read(None, self.alice_public))
        self.assertTrue(can_read(self.bob, self.alice_public))
        self.assertTrue(can_read(self.alice, self.alice_public))

    def test_legacy_unowned_docs_are_public(self):
        self.assertTrue(can_read(None, self.legacy_unowned))
        self.assertTrue(can_read(self.bob, self.legacy_unowned))

    # --- mutate matrix ---
    def test_guest_cannot_mutate_anything(self):
        self.assertFalse(can_mutate(None, self.legacy_unowned))
        self.assertFalse(can_mutate(None, self.alice_public))
        self.assertFalse(can_mutate(None, self.alice_private))

    def test_owner_can_mutate_own(self):
        self.assertTrue(can_mutate(self.alice, self.alice_private))
        self.assertTrue(can_mutate(self.alice, self.alice_public))

    def test_user_cannot_mutate_others(self):
        self.assertFalse(can_mutate(self.bob, self.alice_private))
        self.assertFalse(can_mutate(self.alice, self.legacy_unowned))

    def test_admin_can_mutate_anything(self):
        self.assertTrue(can_mutate(self.admin, self.alice_private))
        self.assertTrue(can_mutate(self.admin, self.legacy_unowned))

    # --- roles / scope clause ---
    def test_is_admin(self):
        self.assertTrue(is_admin(self.admin))
        self.assertFalse(is_admin(self.alice))
        self.assertFalse(is_admin(None))

    def test_scope_clause_guest_is_public_plus_guest_space(self):
        clause = scope_clause(None)
        self.assertIn("d.visibility = 'public'", clause)
        self.assertIn("d.owner_id IS NULL", clause)

    def test_guest_space_private_docs_hidden_from_users(self):
        # Unowned private documents (guest uploads) are invisible to accounts:
        # neither public nor owner-matched for an authenticated user.
        guest_upload = _doc(None, "private")
        self.assertTrue(can_read(None, guest_upload), "guests read the guest space")
        self.assertFalse(can_read(self.bob, guest_upload))
        self.assertTrue(can_read(self.admin, guest_upload))

    def test_scope_clause_user_includes_own_and_public(self):
        clause = scope_clause(self.alice.id)
        self.assertIn("d.owner_id", clause)
        self.assertIn("d.visibility = 'public'", clause)
        self.assertIn(":owner_id", clause)


if __name__ == "__main__":
    unittest.main()
