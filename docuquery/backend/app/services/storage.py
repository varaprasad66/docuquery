"""Server-only storage. Locators are generated internally, never accepted from clients."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

import httpx

from app.config import get_settings


def locator(owner_id, document_id, extension: str) -> str:
    s = get_settings()
    if s.storage_backend == "supabase":
        return f"supabase://{s.supabase_storage_bucket}/{owner_id}/{document_id}{extension}"
    if s.storage_backend != "local":
        raise ValueError("Unknown storage backend")
    return str(s.upload_dir / f"{document_id}{extension}")


def _remote(location: str):
    s = get_settings()
    if not s.supabase_url.startswith("https://") or not s.supabase_secret_key:
        raise RuntimeError("Private storage configuration is missing")
    path = location.removeprefix("supabase://")
    if ".." in path.split("/") or len(path.split("/")) < 3:
        raise ValueError("Invalid object locator")
    headers = {"apikey": s.supabase_secret_key}
    # Legacy service_role JWTs require Authorization; new secret keys use apikey.
    if not s.supabase_secret_key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {s.supabase_secret_key}"
    return f"{s.supabase_url.rstrip('/')}/storage/v1/object/{quote(path, safe='/')}", headers


def _local(location: str) -> Path:
    path = Path(location).resolve()
    if not path.is_relative_to(get_settings().upload_dir.resolve()):
        raise ValueError("Source is outside the configured upload directory")
    return path


def put(location: str, content: bytes) -> None:
    if location.startswith("supabase://"):
        url, headers = _remote(location)
        with httpx.Client(timeout=60) as client:
            response = client.post(url, headers={**headers, "Content-Type": "application/octet-stream"}, content=content)
            response.raise_for_status()
    else:
        path = _local(location)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def delete(location: str) -> None:
    if location.startswith("supabase://"):
        url, headers = _remote(location)
        bucket_url, key = url.split("/object/", 1)
        bucket, key = key.split("/", 1)
        from urllib.parse import unquote
        with httpx.Client(timeout=30) as client:
            response = client.request("DELETE", f"{bucket_url}/object/{bucket}", headers=headers, json={"prefixes": [unquote(key)]})
            response.raise_for_status()
    else:
        _local(location).unlink(missing_ok=True)


@contextmanager
def materialize(location: str):
    if not location.startswith("supabase://"):
        yield _local(location)
        return
    url, headers = _remote(location)
    with TemporaryDirectory(prefix="docuquery-") as directory:
        path = Path(directory) / "source"
        with httpx.Client(timeout=60) as client, client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            consumed = 0
            with path.open("wb") as output:
                for chunk in response.iter_bytes(64 * 1024):
                    consumed += len(chunk)
                    if consumed > get_settings().max_upload_mb * 1024 * 1024:
                        raise ValueError("Stored source exceeds upload limit")
                    output.write(chunk)
        yield path
