import { expect, test } from "@playwright/test";

test("shows the ready development foundation without horizontal overflow", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Control plane" })).toBeVisible();
  await expect(page.getByText("API online")).toBeVisible();
  await expect(page.getByText("Ready", { exact: true })).toHaveCount(2);
  await expect(page.getByText("Scaffolded", { exact: true })).toBeVisible();

  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(hasHorizontalOverflow).toBe(false);
});
