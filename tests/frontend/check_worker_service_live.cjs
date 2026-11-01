"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright-core");
const root = path.resolve(__dirname, "../..");
(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.FINEVOLAB_CHROMIUM,
    headless: true,
    args: ["--no-proxy-server"],
  });
  const checks = [];
  const errors = [];
  const verify = (name, condition) => {
    assert.ok(condition, name);
    checks.push(name);
  };
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    const url =
      process.env.FINEVOLAB_EXPERIENCE_URL || "http://127.0.0.1:8085/";
    await page.goto(url, { waitUntil: "domcontentloaded" });
    const invitation = (
      await fs.readFile(process.env.FINEVOLAB_INVITATION_FILE, "utf8")
    ).trim();
    await page.locator("#invite").fill(invitation);
    await page.locator('#login-form button[type="submit"]').click();
    await page.locator("#analysis-workspace").waitFor({ state: "visible" });
    verify(
      "invitation-cleared",
      (await page.locator("#invite").inputValue()) === "",
    );
    verify("memory-enabled", await page.locator("#use-memory").isEnabled());
    verify(
      "preferences-enabled",
      await page.locator("#amount-scale").isEnabled(),
    );
    await page.waitForFunction(
      () => document.querySelectorAll("#task-history button").length >= 5,
    );
    verify(
      "persisted-history",
      (await page.locator("#task-history button").count()) >= 5,
    );
    const metric = page
      .locator("#task-history button")
      .filter({ hasText: "Microsoft FY2024 revenue" })
      .first();
    await metric.click();
    await page.waitForFunction(() =>
      document.querySelector("#task-output").textContent.includes("2024"),
    );
    verify("memory-restored", await page.locator("#use-memory").isChecked());
    verify(
      "pdf-link",
      (await page.locator('#task-output a[href$="/report/pdf"]').count()) === 1,
    );
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() =>
      document.querySelector("#task-output").textContent.includes("2024"),
    );
    verify("reload-result", await page.locator("#use-memory").isChecked());
    for (const width of [1365, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const language of ["en", "zh-CN"]) {
        if ((await page.locator("html").getAttribute("lang")) !== language)
          await page.locator("#language-toggle").click();
        await page.waitForTimeout(80);
        verify(
          "language-" + width + "-" + language,
          (await page.locator("html").getAttribute("lang")) === language,
        );
        verify(
          "layout-" + width + "-" + language,
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth + 1,
          ),
        );
      }
    }
    await page.locator("#new-conversation").click();
    verify(
      "new-conversation",
      (await page.locator("#question").inputValue()) === "",
    );
    verify("no-script-errors", errors.length === 0);
    const result = { status: "passed", checks, errors, real_model_calls: 0 };
    const output = path.join(root, "output/worker_service_browser.json");
    await fs.writeFile(output, JSON.stringify(result, null, 2) + "\n");
    console.log(
      JSON.stringify({
        status: "passed",
        checks: checks.length,
        real_model_calls: 0,
      }),
    );
    await context.close();
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
