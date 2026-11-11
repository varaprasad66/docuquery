import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.config import Settings
from app.services import storage


class TestStorage(unittest.TestCase):
    def test_local_roundtrip_and_path_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(upload_dir=Path(directory), storage_backend="local")
            with patch.object(storage, "get_settings", return_value=settings):
                location = storage.locator("owner", "document", ".txt")
                storage.put(location, b"source")
                with storage.materialize(location) as source:
                    self.assertEqual(source.read_bytes(), b"source")
                with self.assertRaises(ValueError):
                    storage.put(str(Path(directory).parent / "outside.txt"), b"no")
                storage.delete(location)
                self.assertFalse(Path(location).exists())

    def test_remote_download_is_bounded_and_temporary(self):
        settings = Settings(storage_backend="supabase", supabase_url="https://example.supabase.co", supabase_secret_key="sb_secret_test", max_upload_mb=1)
        original_client = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"source"))
        with patch.object(storage, "get_settings", return_value=settings), patch.object(storage.httpx, "Client", side_effect=lambda **kwargs: original_client(transport=transport, **kwargs)):
            with storage.materialize("supabase://documents/owner/id.txt") as source:
                self.assertEqual(source.read_bytes(), b"source")
            self.assertFalse(source.exists())
        oversized = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (1024 * 1024 + 1)))
        with patch.object(storage, "get_settings", return_value=settings), patch.object(storage.httpx, "Client", side_effect=lambda **kwargs: original_client(transport=oversized, **kwargs)):
            with self.assertRaises(ValueError):
                with storage.materialize("supabase://documents/owner/id.txt"):
                    self.fail("Oversize object must be rejected")
