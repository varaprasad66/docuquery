import { expect, test } from "@playwright/test";

test("extraction explains fields, shows missing values, exports and clears stale results", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("docuquery_token", "test-session"));
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const json = path.endsWith("/auth/me") ? { id: "extract-user", display_name: "Tester" }
      : path.endsWith("/health/config") ? { integrations_enabled: true, registration_enabled: true, max_upload_mb: 10 }
      : path.endsWith("/health") ? { status: "ok", components: {}, model: "mock" }
      : path.endsWith("/documents") ? [{ id: "doc-one", original_name: "invoice.pdf", status: "ready", created_at: new Date().toISOString() }]
      : path.endsWith("/extract/templates") ? {
          invoice: { description: "Invoice fields", schema: { properties: { invoice_number: {}, due_date: {} } } },
          contract: { description: "Contract fields", schema: { properties: { parties: {} } } },
        }
      : path.endsWith("/extract") ? { template: "invoice", confidence: "high", data: { invoice_number: "INV-001", due_date: null }, citations: [{ field: "invoice_number", document_name: "invoice.pdf", location: "Page 1" }] }
      : [];
    await route.fulfill({ json });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "extract", exact: true }).click();
  await expect(page.getByText(/Fields to extract: invoice number, due date/)).toBeVisible();
  await page.getByLabel("Document to extract").selectOption("doc-one");
  await page.getByRole("button", { name: "Extract", exact: true }).last().click();
  await expect(page.getByRole("cell", { name: /^INV-001/ })).toBeVisible();
  await expect(page.getByText("Not found in document", { exact: true })).toBeVisible();
  await expect(page.getByText("model confidence: high (not verified)")).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export CSV" }).click();
  expect((await download).suggestedFilename()).toBe("extracted.csv");
  await page.getByLabel("Extraction template").selectOption("contract");
  await expect(page.getByRole("cell", { name: /^INV-001/ })).toHaveCount(0);
});
