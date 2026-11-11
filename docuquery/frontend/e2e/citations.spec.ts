/**
 * Phase 3 citation UX, fully mocked (no backend needed):
 *   1. A persisted answer's citation opens the inspector with the supporting
 *      quote highlighted inside the full chunk text, plus an "Open page N"
 *      action that opens the source PDF.
 *   2. A streamed (mocked SSE) answer marked unverified=true shows the
 *      unverified badge in the modal.
 */
import { expect, test } from "@playwright/test";

const CHUNK_TEXT = "Section 3: Annual leave is 25 days per year with rollover.";
const QUOTE = "Annual leave is 25 days per year"; // starts at index 11, 32 chars

const CITATION = {
  n: 1,
  document_id: "doc-a",
  document_name: "handbook.pdf",
  page: 2,
  location: "Page 2",
  snippet: QUOTE,
  chunk_id: "chunk-1",
  quote_start: 11,
  quote_end: 43,
  chunk_text: CHUNK_TEXT,
};

const PDF_BYTES =
  "%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n" +
  "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n" +
  "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n" +
  "trailer<</Size 4/Root 1 0 R>>\n%%EOF";

function sse(events: object[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
}

async function mockApi(page: import("@playwright/test").Page, chatSse: string, hits: { download: boolean }) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path.endsWith("/auth/me")) {
      return route.fulfill({ json: { id: "u1", display_name: "Tester", email: "t@example.com" } });
    }
    if (path.endsWith("/health/config")) {
      return route.fulfill({ json: { registration_enabled: true, integrations_enabled: false, max_upload_mb: 10, url_import_enabled: false } });
    }
    if (path.endsWith("/health")) {
      return route.fulfill({ json: { status: "ok", components: {}, model: "mock" } });
    }
    if (path.endsWith("/documents") && method === "GET") {
      return route.fulfill({
        json: [{ id: "doc-a", original_name: "handbook.pdf", status: "ready", size_bytes: 100, page_count: 2, chunk_count: 2, created_at: new Date().toISOString() }],
      });
    }
    if (path.endsWith("/chat") && method === "POST") {
      return route.fulfill({ contentType: "text/event-stream", body: chatSse });
    }
    if (path.endsWith("/conversations") && method === "GET") {
      return route.fulfill({
        json: [{ id: "conv-1", title: "Handbook questions", created_at: new Date().toISOString(), message_count: 2 }],
      });
    }
    if (path.includes("/conversations/conv-1") && method === "GET") {
      return route.fulfill({
        json: [
          { id: "m1", role: "user", content: "How many annual leave days?", citations: null, created_at: new Date().toISOString() },
          { id: "m2", role: "assistant", content: "You get 25 days [1].", citations: [CITATION], created_at: new Date().toISOString() },
        ],
      });
    }
    if (path.endsWith("/download")) {
      hits.download = true;
      return route.fulfill({ body: PDF_BYTES, contentType: "application/pdf" });
    }
    return route.fulfill({ json: {} });
  });
}

test("citation modal highlights the quote and opens the source page", async ({ page }) => {
  const hits = { download: false };
  await mockApi(page, "", hits);
  await page.addInitScript(() => localStorage.setItem("docuquery_token", "test-session"));

  await page.goto("/");
  await page.getByRole("button", { name: /^chat/i }).click();
  await page.getByText("Handbook questions").click();
  await expect(page.getByText("You get 25 days", { exact: false })).toBeVisible();

  await page.getByTestId("citation-card").click();
  await expect(page.getByTestId("citation-chunk")).toBeVisible();

  // The supporting quote is highlighted inside the FULL chunk text.
  const mark = page.getByTestId("citation-highlight");
  await expect(mark).toHaveText(QUOTE);
  const chunk = await page.getByTestId("citation-chunk").textContent();
  expect(chunk).toContain("Section 3:");
  expect(chunk).toContain("with rollover.");
  expect(chunk!.indexOf(QUOTE)).toBeGreaterThan(0); // context before the quote

  // Open page action fetches the source PDF through the authed endpoint.
  await page.getByRole("button", { name: /Open page 2/ }).click();
  await page.waitForTimeout(500);
  expect(hits.download).toBe(true);
});

test("streamed unverified answers carry the badge into the modal", async ({ page }) => {
  const hits = { download: false };
  await mockApi(
    page,
    sse([
      { type: "meta", conversation_id: "conv-1", retrieval_ms: 4, methods: ["vector"], cached: false },
      { type: "citations", citations: [CITATION] },
      { type: "token", value: "Leave is 25 days " },
      { type: "token", value: "[1]." },
      {
        type: "done",
        message_id: "m9",
        prompt_tokens: 12,
        completion_tokens: 6,
        latency_ms: 42,
        cached: false,
        refusal_reason: "ok",
        unverified: true,
      },
    ]),
    hits,
  );
  await page.addInitScript(() => localStorage.setItem("docuquery_token", "test-session"));

  await page.goto("/");
  await page.getByRole("button", { name: /^chat/i }).click();
  await page.getByPlaceholder(/Ask anything about your uploaded documents/i).fill("How many annual leave days?");
  await page.keyboard.press("Enter");

  // The citation marker renders as its own button, so match the prose only.
  // (The quote inside the citation card also contains this phrase.)
  await expect(page.getByText("Leave is 25 days", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("Unverified — review the sources")).toBeVisible();

  // The inline citation chip opens the modal, which repeats the badge.
  await page.getByTitle("View Citation [1]").click();
  await expect(page.getByTestId("citation-unverified")).toBeVisible();
  await expect(page.getByTestId("citation-highlight")).toHaveText(QUOTE);
});
