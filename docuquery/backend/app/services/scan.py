"""Optional upload malware scanning via ClamAV (clamd TCP INSTREAM).

Enabled with CLAMAV_ENABLED=True. Fail-open by design: if the clamd service is
unreachable the upload proceeds and the failure is logged — availability beats
a silently broken scanner. Quarantine is the caller's job (see documents API).
"""

import logging
import socket

from app.config import get_settings

logger = logging.getLogger(__name__)

_MAX_CHUNK = 1024 * 64


def scan_bytes(content: bytes) -> tuple[bool, str | None]:
    """Scan in-memory content. Returns (clean, threat_name_or_error_note).

    clean=True with a note means the scanner was unavailable (fail-open).
    """
    s = get_settings()
    if not s.clamav_enabled:
        return True, None
    try:
        with socket.create_connection(
            (s.clamav_host, s.clamav_port), timeout=s.clamav_timeout_seconds
        ) as sock:
            sock.sendall(b"zINSTREAM\0")
            for offset in range(0, len(content), _MAX_CHUNK):
                chunk = content[offset : offset + _MAX_CHUNK]
                sock.sendall(len(chunk).to_bytes(4, "big") + chunk)
            sock.sendall((0).to_bytes(4, "big"))
            # clamd replies are null-terminated: "stream: OK\0" / "stream: NAME FOUND\0"
            response = (
                sock.recv(4096).decode(errors="replace").replace("\x00", "").strip()
            )
    except Exception as exc:
        logger.warning("ClamAV unreachable (%s); failing open", exc)
        return True, f"clamav-unavailable: {type(exc).__name__}"

    if response.endswith("OK"):
        return True, None
    if " FOUND" in response:
        # "stream: <threat> FOUND" -> threat name before the FOUND marker
        threat = response.split(" FOUND", 1)[0].split(": ", 1)[-1].strip()
        return False, threat or "unknown-threat"
    logger.warning("Unexpected clamd response: %s", response[:200])
    return True, f"clamav-error: {response[:120]}"
