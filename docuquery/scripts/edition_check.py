#!/usr/bin/env python3
"""Verify which product edition the RUNNING server is serving.

The three editions (Starter / Professional / Enterprise) are one codebase gated by the
EDITION setting. This script probes the feature surfaces from the outside — without
accounts or data — and reports which tier is active and whether the gating matrix is
correct:

    surface                 basic    professional    enterprise
    /api/admin/*            hidden   hidden          open (auth-gated)
    /api/messages/*/feedback hidden   open           open

Usage:
    python scripts/edition_check.py          # against http://127.0.0.1:8100
    python scripts/edition_check.py http://host:8100
"""

import sys
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8100"


async def probe(client: httpx.AsyncClient):
    admin = await client.get(f"{BASE}/api/admin/overview")           # unauthenticated
    fb = await client.post(
        f"{BASE}/api/messages/{uuid.uuid4()}/feedback",
        json={"rating": "up"},
    )
    core = await client.post(f"{BASE}/api/chat", json={"question": ""})  # validation probe
    return admin, fb, core


async def main() -> int:
    async with httpx.AsyncClient(timeout=30) as c:
        health = (await c.get(f"{BASE}/api/health")).json()
        configured = (health.get("edition") or "?").lower()
        admin, fb, core = await probe(c)

        admin_open = admin.status_code == 401  # surface exists, auth required
        fb_open = fb.status_code == 404 and "Message not found" in str(fb.json().get("detail", ""))
        core_alive = core.status_code == 422  # validation error = core product alive

        if admin_open:
            detected = "enterprise"
        elif fb_open:
            detected = "professional"
        else:
            detected = "basic"

        tier_names = {"basic": "Starter", "professional": "Professional", "enterprise": "Enterprise"}
        rows = [
            ("Core RAG (upload/chat/search)", "open" if core_alive else "DOWN", True),
            ("Professional: cache/quotas/feedback", "OPEN" if fb_open else "hidden", detected in ("professional", "enterprise")),
            ("Enterprise: admin console & audit", "OPEN (auth-gated)" if admin_open else "hidden", detected == "enterprise"),
        ]

        print("=" * 64)
        print(f"Server: {BASE}")
        print(f"Configured edition : {configured} ({tier_names.get(configured, configured)})")
        print(f"Detected edition   : {detected} ({tier_names.get(detected, detected)})")
        print("=" * 64)
        for name, state, expect_open in rows:
            ok = (state != "hidden") if expect_open else (state == "hidden")
            mark = "✓" if ok else "✗"
            print(f"  {mark} {name:<38} {state}")
        print("=" * 64)

        match = detected == configured
        print(f"{'✓ MATCH' if match else '✗ MISMATCH'}: server is serving the "
              f"{tier_names.get(detected, detected)} edition.")
        if not match:
            print("  → set EDITION in backend/.env (or env var) and RESTART the backend.")
        return 0 if match else 1


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
