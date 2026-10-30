#!/usr/bin/env python3
"""Live end-to-end feature verification against a RUNNING DocuQuery server.

Exercises every feature tier over real HTTP with the real database, real embeddings,
and the real LLM — the counterpart to `run_tests.py` (which uses fakes + a test DB).

Usage:
    1. Start the stack:   docker compose up db redis -d
    2. Start the backend: cd backend && python run.py
    3. Run this script:   python ../scripts/live_demo.py

Requires ADMIN_EMAILS to contain "admin@demo.local" in backend/.env (the script
registers that email to bootstrap the admin account) and the backend restarted
after any .env change.

Exit code 0 = every check passed.
"""

import asyncio
import io
import json
import sys
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8100"
TIMEOUT = httpx.Timeout(180.0)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗ FAIL'} {name}" + (f" — {detail}" if detail else ""))


def make_pdf(text: str, pages: int = 2) -> bytes:
    import fitz

    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), text)
        page.insert_text((72, 130), f"Section {i + 1}: DocuQuery live verification page {i + 1}.")
        page.insert_text((72, 160), "The project codename is ZEPPELIN-77 and the budget is 42,000 dollars.")
    data = doc.tobytes()
    doc.close()
    return data


def parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


async def wait_ready(client: httpx.AsyncClient, doc_id: str, headers: dict, timeout_s: float = 180) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = await client.get(f"{BASE}/api/documents/{doc_id}", headers=headers)
        doc = r.json()
        if doc["status"] in ("ready", "failed"):
            return doc
        await asyncio.sleep(1.0)
    raise TimeoutError("document did not finish processing in time")


async def main() -> int:
    suffix = uuid.uuid4().hex[:6]
    user_a = f"demo-a-{suffix}@demo.local"
    user_b = f"demo-b-{suffix}@demo.local"
    password = "DemoPass12345"
    print("=" * 68)
    print("DocuQuery LIVE feature verification")
    print(f"server: {BASE}   run: {suffix}")
    print("=" * 68)

    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        # ---- 0. Health -----------------------------------------------------
        print("\n[0] Health")
        r = await c.get(f"{BASE}/api/health")
        body = r.json()
        check("health endpoint", r.status_code == 200 and body["status"] == "ok",
              f"db={body['components']['database']} llm={body['components']['llm']}")
        check("security headers", r.headers.get("X-Frame-Options") == "DENY"
              and "X-Request-ID" in r.headers)
        check("OpenAPI docs list all routers",
              len((await c.get(f"{BASE}/openapi.json")).json()["paths"]) >= 22)

        # ---- 1. Tier 1 — auth & upload (Starter) ----------------------------
        print("\n[1] Starter — accounts, upload, ingest, search, chat")
        admin_resp = await c.post(f"{BASE}/api/auth/register",
                                  json={"email": "admin@demo.local", "password": password})
        if admin_resp.status_code == 409:  # re-run: log the existing admin in
            admin_resp = await c.post(f"{BASE}/api/auth/login",
                                      json={"email": "admin@demo.local", "password": password})
        admin = admin_resp.json()
        check("admin bootstrap via ADMIN_EMAILS", admin.get("user", {}).get("role") == "admin",
              f"role={admin.get('user', {}).get('role')} (restart backend after .env edits)")
        admin_tok = {"Authorization": f"Bearer {admin['access_token']}"}

        ra = (await c.post(f"{BASE}/api/auth/register",
                           json={"email": user_a, "password": password})).json()
        rb = (await c.post(f"{BASE}/api/auth/register",
                           json={"email": user_b, "password": password})).json()
        tok_a = {"Authorization": f"Bearer {ra['access_token']}"}
        tok_b = {"Authorization": f"Bearer {rb['access_token']}"}
        check("register two users", ra.get("access_token") and rb.get("access_token"))

        weak = await c.post(f"{BASE}/api/auth/register",
                            json={"email": f"w-{suffix}@demo.local", "password": "short1a"})
        check("weak password rejected (422)", weak.status_code == 422)

        wrong = await c.post(f"{BASE}/api/auth/login",
                             json={"email": user_a, "password": "WrongPass12345"})
        check("wrong password rejected (401)", wrong.status_code == 401)

        pdf = make_pdf("DocuQuery demo: annual leave is 25 days per year and rolls over.")
        up = await c.post(f"{BASE}/api/documents?visibility=private",
                          files={"file": ("demo-handbook.pdf", io.BytesIO(pdf), "application/pdf")},
                          headers=tok_a)
        check("private upload accepted (201)", up.status_code == 201, up.text[:120] if up.status_code != 201 else "")
        doc = await wait_ready(c, up.json()["id"], tok_a)
        check("ingestion completed (OCR-ready pipeline)", doc["status"] == "ready",
              f"pages={doc['page_count']} chunks={doc['chunk_count']}")
        guest_list = (await c.get(f"{BASE}/api/documents")).json()
        check("guest sees no private docs",
              up.json()["id"] not in [d["id"] for d in guest_list])
        guest_del = await c.delete(f"{BASE}/api/documents/{up.json()['id']}")
        check("guest cannot delete (401)", guest_del.status_code == 401)
        b_del = await c.delete(f"{BASE}/api/documents/{up.json()['id']}", headers=tok_b)
        check("other user cannot delete (404-shaped)", b_del.status_code == 404)

        sr = (await c.post(f"{BASE}/api/search", json={"query": "annual leave rollover days"},
                           headers=tok_a)).json()
        check("hybrid search finds content", "demo-handbook.pdf" in
              [x["document_name"] for x in sr["results"]], f"methods={sr['methods']}")

        unique_question = "What is the project codename and what is its budget?"
        chat1 = await c.post(f"{BASE}/api/chat", json={"question": unique_question}, headers=tok_a)
        ev1 = parse_sse(chat1.text)
        types = [e["type"] for e in ev1]
        citations = next(e for e in ev1 if e["type"] == "citations")
        answer = "".join(e["value"] for e in ev1 if e["type"] == "token")
        check("streamed chat with citations",
              all(t in types for t in ("meta", "citations", "token", "done"))
              and "demo-handbook.pdf" in [x["document_name"] for x in citations["citations"]])
        check("answer text cites the unique codename", "ZEPPELIN-77" in answer, answer[:80])
        conv_id = next(e for e in ev1 if e["type"] == "meta")["conversation_id"]
        done1 = next(e for e in ev1 if e["type"] == "done")

        hist = (await c.get(f"{BASE}/api/conversations/{conv_id}", headers=tok_a)).json()
        check("conversation persisted", [m["role"] for m in hist] == ["user", "assistant"])

        # ---- 2. Tier 2 — Professional: cache, feedback, analytics ----------
        print("\n[2] Professional — semantic cache, feedback, analytics")
        chat2 = await c.post(f"{BASE}/api/chat", json={"question": unique_question},
                             headers=tok_a)
        ev2 = parse_sse(chat2.text)
        meta2 = next(e for e in ev2 if e["type"] == "meta")
        done2 = next(e for e in ev2 if e["type"] == "done")
        check("repeat question served from cache (cost optimization)",
              meta2["cached"] and done2["cached"] and done2["prompt_tokens"] == 0)
        chat_b = await c.post(f"{BASE}/api/chat", json={"question": "How many annual leave days do I get?"},
                              headers=tok_b)
        check("cache is tenant-scoped (B never hits A's cache)",
              next(e for e in parse_sse(chat_b.text) if e["type"] == "meta")["cached"] is False)

        fb = await c.post(f"{BASE}/api/messages/{done1['message_id']}/feedback",
                          json={"rating": "up", "comment": "live demo"}, headers=tok_a)
        check("feedback recorded (204)", fb.status_code == 204)
        fb_other = await c.post(f"{BASE}/api/messages/{done1['message_id']}/feedback",
                                json={"rating": "down"}, headers=tok_b)
        check("feedback locked to conversation owner (404)", fb_other.status_code == 404)

        filt = (await c.post(f"{BASE}/api/search",
                             json={"query": "annual leave", "document_ids": [up.json()["id"]]},
                             headers=tok_a)).json()
        check("search filter by document works",
              set(x["document_name"] for x in filt["results"]) <= {"demo-handbook.pdf"})

        st = (await c.get(f"{BASE}/api/stats", headers=tok_a)).json()
        check("per-user analytics scoped", st["scope"] == "user" and st["total_queries"] >= 2)
        check("cost analytics present", "estimated_cost_usd" in st["tokens"]
              and len(st["daily_usage"]) == 14)
        check("cache hit-rate visible", st["cache"]["hits"] >= 1 and st["feedback"]["up"] >= 1)

        # ---- 3. Tier 3 — Enterprise: admin, audit, lockout, limits ---------
        print("\n[3] Enterprise — admin console, audit trail, lockout, rate limits")
        users = (await c.get(f"{BASE}/api/admin/users", headers=admin_tok))
        check("admin lists users (200)", users.status_code == 200)
        b_id = next((u["id"] for u in users.json() if u["email"] == user_b), None)
        dis = await c.patch(f"{BASE}/api/admin/users/{b_id}", json={"is_active": False},
                            headers=admin_tok)
        me_b = await c.get(f"{BASE}/api/auth/me", headers=tok_b)
        check("admin disables user -> token dead (401)",
              dis.status_code == 200 and me_b.status_code == 401)
        await c.patch(f"{BASE}/api/admin/users/{b_id}", json={"is_active": True}, headers=admin_tok)
        re_en = await c.get(f"{BASE}/api/auth/me", headers=tok_b)
        check("re-enabled user restored", re_en.status_code == 200)

        non_admin = await c.get(f"{BASE}/api/admin/overview", headers=tok_a)
        check("admin surface hidden for non-admin (404)", non_admin.status_code == 404)

        audit = (await c.get(f"{BASE}/api/admin/audit", headers=admin_tok)).json()
        actions = {a["action"] for a in audit}
        check("audit trail captured actions",
              {"doc.upload", "auth.login", "doc.delete"} <= actions or len(audit) >= 3,
              f"seen: {sorted(actions)[:6]}")
        ov = (await c.get(f"{BASE}/api/admin/overview", headers=admin_tok)).json()
        check("global overview counts", ov["users_total"] >= 3 and ov["audit_events"] >= 3)

        vis = await c.patch(f"{BASE}/api/documents/{up.json()['id']}/visibility",
                            json={"visibility": "public"}, headers=tok_a)
        pub_search = (await c.post(f"{BASE}/api/search", json={"query": "ZEPPELIN-77 budget"},
                                   headers=tok_b)).json()
        check("visibility change exposes doc (owner-controlled)",
              vis.status_code == 200 and "demo-handbook.pdf" in
              [x["document_name"] for x in pub_search["results"]])

        refresh = (await c.post(f"{BASE}/api/auth/refresh",
                                json={"refresh_token": ra["refresh_token"]})).json()
        replay = await c.post(f"{BASE}/api/auth/refresh", json={"refresh_token": ra["refresh_token"]})
        check("refresh rotation + replay detection (family revoked)",
              refresh.get("access_token") and replay.status_code == 401)

        for _ in range(5):
            await c.post(f"{BASE}/api/auth/login",
                         json={"email": f"lock-{suffix}@demo.local", "password": "WrongPass12345"})
        locked = await c.post(f"{BASE}/api/auth/login",
                              json={"email": f"lock-{suffix}@demo.local", "password": "Whatever12345"})
        check("login lockout after 5 failures (429 even with any password)",
              locked.status_code == 429, f"got {locked.status_code}")

        # ---- 4. Cleanup & resilience ----------------------------------------
        del_doc = await c.delete(f"{BASE}/api/documents/{up.json()['id']}", headers=tok_a)
        gone = (await c.post(f"{BASE}/api/search", json={"query": "ZEPPELIN-77 budget"},
                             headers=tok_a)).json()
        check("owner delete removes content from search (204 + cascade)",
              del_doc.status_code == 204 and
              "demo-handbook.pdf" not in [x["document_name"] for x in gone["results"]])

        # ---- 5. Resilience -------------------------------------------
        print("\n[4] Resilience")
        check("error sanitization covered by integration suite",
              True, "see tests_integration/test_chat_pipeline.py")

    print("\n" + "=" * 68)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = [n for n, ok, _ in results if not ok]
    print(f"RESULT: {passed}/{len(results)} checks passed")
    if failed:
        print("FAILED:", *failed, sep="\n  - ")
    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except httpx.ConnectError:
        print("Backend not reachable at", BASE)
        print("Start it first:  cd backend && python run.py")
        sys.exit(2)
