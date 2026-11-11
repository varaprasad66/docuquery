import { expect, test } from "@playwright/test";

test("delete and sign-out require confirmation before requests", async ({ page }) => {
  let deletions = 0;
  let logouts = 0;
  await page.addInitScript(() => localStorage.setItem("docuquery_token", "test-session"));
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "DELETE") {
      deletions++;
      return route.fulfill({ status: 204 });
    }
    if (path.endsWith("/auth/logout")) {
      logouts++;
      return route.fulfill({ status: 204 });
    }
    const data = path.endsWith("/auth/me") ? { id: "account-a", display_name: "Test User", email: "test@example.com" }
      : path.endsWith("/health/config") ? { registration_enabled: true, integrations_enabled: false, max_upload_mb: 10 }
      : path.endsWith("/health") ? { status: "ok", components: {}, model: "test" }
      : path.endsWith("/documents") ? [{ id: "doc-a", original_name: "handbook.pdf", status: "ready", size_bytes: 100, page_count: 1, chunk_count: 1, created_at: new Date().toISOString() }]
      : [];
    return route.fulfill({ json: data });
  });
  await page.goto("/");
  await expect(page.getByText("handbook.pdf", { exact: true })).toBeVisible();
  page.once("dialog", async dialog => {
    expect(dialog.message()).toContain("handbook.pdf");
    await dialog.dismiss();
  });
  await page.getByTitle("Delete document").click();
  expect(deletions).toBe(0);
  await expect(page.getByText("handbook.pdf", { exact: true })).toBeVisible();
  page.once("dialog", dialog => dialog.accept());
  await page.getByTitle("Delete document").click();
  await expect(page.getByText("handbook.pdf", { exact: true })).toHaveCount(0);
  expect(deletions).toBe(1);
  page.once("dialog", dialog => dialog.dismiss());
  await page.getByRole("button", { name: "Sign Out" }).click();
  expect(logouts).toBe(0);
  await expect(page.getByText("Test User", { exact: false })).toBeVisible();
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Sign Out" }).click();
  await expect(page.getByRole("button", { name: "Sign In", exact: true })).toBeVisible();
  expect(logouts).toBe(1);
});
