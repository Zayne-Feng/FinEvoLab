"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright-core");
const root = path.resolve(__dirname, "../..");
const url =
  process.env.FINEVOLAB_EXPERIENCE_URL ||
  "http://127.0.0.1:8085/experience.html";
const real = process.argv.includes("--real");
let invitation = "";
(async () => {
  const executablePath = process.env.FINEVOLAB_CHROMIUM;
  assert(
    executablePath,
    "FINEVOLAB_CHROMIUM must name an installed Chromium executable",
  );
  const browser = await chromium.launch({
    executablePath,
    headless: true,
    args: process.env.FINEVOLAB_BROWSER_PROXY ? [] : ["--no-proxy-server"],
    ...(process.env.FINEVOLAB_BROWSER_PROXY
      ? { proxy: { server: process.env.FINEVOLAB_BROWSER_PROXY } }
      : {}),
  });
  const errors = [],
    calls = [],
    checks = [];
  const verify = (name, value) => {
    assert(value, name);
    checks.push(name);
  };
  try {
    const context = await browser.newContext({
      viewport: { width: 1365, height: 900 },
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
      if (request.url().includes("/experience/api/"))
        calls.push(new URL(request.url()).pathname);
    });
    await page.goto(url, { waitUntil: "domcontentloaded" });
    for (const width of [1365, 768, 390, 360]) {
      await page.setViewportSize({ width, height: 900 });
      for (const language of ["zh-CN", "en"]) {
        if ((await page.locator("html").getAttribute("lang")) !== language)
          await page.locator("#language-toggle").click();
        await page.evaluate(
          () =>
            new Promise((resolve) =>
              requestAnimationFrame(() => requestAnimationFrame(resolve)),
            ),
        );
        const layout = await page.evaluate(() => ({
          viewport: innerWidth,
          scroll: document.documentElement.scrollWidth,
          x: scrollX,
          overflow: [...document.querySelectorAll("body *")]
            .filter(
              (e) => e.getBoundingClientRect().right + scrollX > innerWidth + 1,
            )
            .map((e) => ({
              tag: e.tagName,
              id: e.id,
              width: e.getBoundingClientRect().width,
              right: e.getBoundingClientRect().right,
            }))
            .slice(0, 8),
        }));
        verify(
          `layout-${width}-${language}: ${JSON.stringify(layout)}`,
          layout.scroll <= layout.viewport + 1,
        );
        for (const stage of [
          "plan",
          "tools",
          "answer",
          "validation",
          "usage",
        ]) {
          await page.locator(`[data-stage="${stage}"]`).click();
          verify(
            `replay-${stage}-${width}-${language}`,
            (await page
              .locator(`[data-stage="${stage}"]`)
              .getAttribute("aria-pressed")) === "true",
          );
        }
        await page.locator("#filter-company").selectOption("MSFT");
        await page.locator("#filter-year").selectOption("2024");
        await page.locator("#filter-metric").selectOption("revenue");
        verify(
          `evidence-${width}-${language}`,
          (await page.locator("#evidence-rows details").count()) > 0,
        );
        verify(
          `period-${width}-${language}`,
          (await page.locator("#evidence-rows").textContent()).includes(
            "2023-07-01",
          ),
        );
        await page.locator("#filter-query").fill("impossible-matching-text");
        verify(
          `empty-${width}-${language}`,
          (await page.locator("#evidence-rows details").count()) === 0,
        );
        await page.locator("#filter-query").fill("");
      }
    }
    await fs.mkdir(path.join(root, "output/experience/screenshots"), {
      recursive: true,
    });
    await page.locator("#evidence").screenshot({
      path: path.join(
        root,
        "output/experience/screenshots/evidence-mobile.png",
      ),
    });
    await page.setViewportSize({ width: 1365, height: 900 });
    await page.locator('[data-stage="tools"]').click();
    await page.locator("#replay").screenshot({
      path: path.join(root, "output/experience/screenshots/replay-desktop.png"),
    });
    let execution = null;
    if (real) {
      invitation = (
        await fs.readFile(process.env.FINEVOLAB_INVITATION_FILE, "utf8")
      ).trim();
      await page.locator("#invite").fill(invitation);
      await page.locator('#login-form button[type="submit"]').click();
      await page.locator("#analysis-workspace").waitFor({ state: "visible" });
      verify(
        "invitation-cleared",
        (await page.locator("#invite").inputValue()) === "",
      );
      await page.locator("#example").click();
      const submission = page.waitForResponse(
        (response) =>
          response.url().endsWith("/experience/api/tasks") &&
          response.request().method() === "POST",
      );
      await page.locator("#submit-task").click();
      const submitted = await submission;
      verify("task-accepted", submitted.status() === 202);
      const task = await submitted.json();
      await page.waitForFunction(
        () =>
          Boolean(
            document.querySelector('#task-output a[href$="/report/pdf"]'),
          ),
        {},
        { timeout: 300000 },
      );
      const response = await context.request.get(
        new URL(`/experience/api/tasks/${task.task_id}`, url).href,
      );
      const state = await response.json();
      verify("real-task-complete", state.status === "complete");
      verify("real-validation-passed", state.validation.status === "passed");
      verify("real-business-complete", state.business_status === "complete");
      verify("real-narrative-complete", state.narrative.status === "complete");
      verify("real-hybrid", state.answer_type === "hybrid");
      verify(
        "real-retrieval",
        state.events.some((event) => event.tool === "search_reports"),
      );
      verify(
        "real-calculation",
        state.events.some((event) => event.tool === "calculate_metrics"),
      );
      verify("real-citations", state.evidence.length > 0);
      const pdf = await context.request.get(
        new URL(`/experience/api/tasks/${task.task_id}/report/pdf`, url).href,
      );
      verify(
        "real-pdf",
        (await pdf.body()).subarray(0, 5).toString() === "%PDF-",
      );
      const html = await context.request.get(
        new URL(`/experience/api/tasks/${task.task_id}/report/html`, url).href,
      );
      verify(
        "real-html",
        html.status() === 200 &&
          (await html.text()).toLowerCase().includes("<!doctype html>"),
      );
      const body = submitted.request().postDataJSON();
      const duplicate = await context.request.post(
        new URL("/experience/api/tasks", url).href,
        { headers: { "X-FinEvoLab": "1" }, data: body },
      );
      verify(
        "idempotent-task",
        (await duplicate.json()).task_id === task.task_id,
      );
      const session = await (
        await context.request.get(new URL("/experience/api/session", url).href)
      ).json();
      await page.reload({ waitUntil: "domcontentloaded" });
      await page
        .locator('#task-output a[href$="/report/pdf"]')
        .waitFor({ timeout: 15000 });
      verify("refresh-restores-task", true);
      await page.locator("#task-output").screenshot({
        path: path.join(
          root,
          "output/experience/screenshots/real-analysis.png",
        ),
      });
      execution = {
        task_id: task.task_id,
        status: state.status,
        answer_type: state.answer_type,
        validation: state.validation,
        usage: state.usage,
        data_version: state.data_version,
        elapsed_seconds: state.elapsed_seconds,
        tools: [...new Set(state.events.map((e) => e.tool))],
        reports: state.reports,
        quota_used: session.used,
      };
      await page.locator("#logout").click();
      await page.locator("#login-form").waitFor({ state: "visible" });
      verify(
        "logout-denies-report",
        (
          await context.request.get(
            new URL(`/experience/api/tasks/${task.task_id}/report/pdf`, url)
              .href,
          )
        ).status() === 401,
      );
      verify(
        "logout-clears-output",
        (await page.locator("#task-output").textContent()) === "",
      );
    } else if (
      (await page.locator("html").getAttribute("data-live")) === "false"
    )
      verify("static-zero-api-calls", calls.length === 0);
    verify("no-script-errors", errors.length === 0);
    const report = {
      status: "passed",
      url,
      browser: browser.version(),
      checks: checks.length,
      check_names: checks,
      script_errors: errors,
      real_execution: execution,
      static_model_calls: 0,
    };
    const name = real
      ? "experience_live_checks.json"
      : url.startsWith("https:")
        ? "experience_public_checks.json"
        : "experience_browser_checks.json";
    await fs.writeFile(
      path.join(root, "project_notes/run_logs", name),
      JSON.stringify(report, null, 2) + "\n",
    );
    console.log(
      JSON.stringify({
        status: report.status,
        checks: report.checks,
        real_execution: execution,
        script_errors: errors,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  const message = invitation
    ? String(error).split(invitation).join("[redacted]")
    : String(error);
  console.error(message);
  process.exitCode = 1;
});
