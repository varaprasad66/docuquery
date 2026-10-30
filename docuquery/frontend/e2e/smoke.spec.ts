/**
 * Playwright smoke test: register -> upload -> process -> chat with citation.
 *
 * Requires the full local stack (backend on :8100 with Ollama reachable, and
 * the Vite dev server or built frontend on :5173):
 *   docker compose up db redis -d
 *   cd backend && python run.py
 *   cd frontend && npm run dev
 *   npx playwright test
 */
import { expect, test } from "@playwright/test";
import { join } from "node:path";

const UNIQUE = Date.now();

test("login, upload, and chat with a citation", async ({ page }) => {
  test.setTimeout(180_000);

  await page.goto("/");

  // --- Register / sign in ---
  await page.getByRole("button", { name: /Sign In/i }).click();
  await page
    .getByRole("button", { name: /Register/i })
    .first()
    .click();
  await page.getByPlaceholder("Your name").fill("Smoke Tester");
  await page
    .getByPlaceholder("user@example.com")
    .fill(`smoke-${UNIQUE}@example.com`);
  await page.getByPlaceholder("••••••••").fill("SmokePassword123");
  await page.getByRole("button", { name: /Create Account/i }).click();
  await expect(page.getByText("Smoke Tester")).toBeVisible({ timeout: 15_000 });

  // --- Upload the fixture document ---
  await page.getByRole("button", { name: /documents/i }).click();
  await page.setInputFiles(
    'input[type="file"]',
    join(__dirname, "fixtures", "handbook-sample.pdf"),
  );
  await expect(page.getByText(/^Ready/).first()).toBeVisible({
    timeout: 120_000,
  });

  // --- Chat and expect a cited answer ---
  await page.getByRole("button", { name: /^chat/i }).click();
  await page
    .getByPlaceholder(/Ask anything about your uploaded documents/i)
    .fill("How many annual leave days do I get per year?");
  await page.keyboard.press("Enter");

  await expect(page.getByText(/25/).first()).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("handbook-sample.pdf").first()).toBeVisible({
    timeout: 30_000,
  });
});
