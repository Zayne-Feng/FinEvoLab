"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright-core");
const root = path.resolve(__dirname, "../..");
const site = path.join(root, "output/refinement/site");
(async () => {
  const records = JSON.parse(
    await fs.readFile(
      path.join(
        root,
        "project_notes/experiments/runtime-refinement-v1/records.json",
      ),
      "utf8",
    ),
  );
  const row = records.find(
    (record) => record.execution_status === "completed" && record.score === 1,
  );
  assert(row, "A successful recorded workflow is required");
  const dataset = JSON.parse(
    await fs.readFile(
      path.join(
        root,
        "data/evaluation/retrieval_refinement/runtime_cases.json",
      ),
      "utf8",
    ),
  );
  const question = dataset.tasks.find(
    (item) => item.id === row.case_id,
  ).question;
  const saved = {
    ...row.output,
    task_id: row.task_id,
    status: "complete",
    question,
    phases: row.phases,
    operations: row.operations,
    reports: ["html", "pdf"],
  };
  const browser = await chromium.launch({
    executablePath: process.env.FINEVOLAB_CHROMIUM,
    headless: true,
    args: ["--no-proxy-server"],
  });
  const checks = [],
    errors = [];
  let submissions = 0;
  const check = (name, result) => {
    assert(result, name);
    checks.push(name);
  };
  try {
    for (const width of [1365, 768, 390, 360]) {
      for (const language of ["zh-CN", "en"]) {
        const context = await browser.newContext({
          viewport: { width, height: 1000 },
        });
        const page = await context.newPage();
        let authenticated = true;
        page.on("pageerror", (error) => errors.push(error.message));
        await page.addInitScript(
          ({ task, language }) => {
            localStorage.setItem("finevolab.showcase.language", language);
            sessionStorage.setItem("finevolab.experience.task", task);
          },
          { task: row.task_id, language },
        );
        await page.route("**/*", async (route) => {
          const url = new URL(route.request().url());
          const pathname = url.pathname;
          const json = (body, status = 200) =>
            route.fulfill({
              status,
              contentType: "application/json",
              body: JSON.stringify(body),
            });
          if (pathname === "/")
            return route.fulfill({
              contentType: "text/html",
              body: await fs.readFile(
                path.join(root, "finevolab/templates/experience.html"),
                "utf8",
              ),
            });
          if (
            [
              "/styles.css",
              "/experience.css",
              "/experience.js",
              "/favicon.svg",
            ].includes(pathname)
          )
            return route.fulfill({
              contentType: pathname.endsWith(".js")
                ? "application/javascript"
                : pathname.endsWith(".css")
                  ? "text/css"
                  : "image/svg+xml",
              body: await fs.readFile(path.join(site, pathname.slice(1))),
            });
          if (pathname === "/experience/api/logout") {
            authenticated = false;
            return json({});
          }
          if (!authenticated) return json({}, 401);
          if (pathname === "/experience/api/session")
            return json({ user: "local-review", used: 1, daily_tasks: 5 });
          if (pathname === "/experience/api/tasks") {
            if (route.request().method() === "POST") {
              submissions++;
              return json({}, 405);
            }
            return json([
              {
                task_id: row.task_id,
                question: saved.question,
                status: "complete",
              },
            ]);
          }
          if (pathname === `/experience/api/tasks/${row.task_id}`)
            return json(saved);
          return json({}, 404);
        });
        await page.goto("http://127.0.0.1:8095/");
        await page.locator(".execution-timing").waitFor();
        await page.locator(".execution-timing summary").click();
        const text = await page.locator(".execution-timing").innerText();
        check(
          `${width}:${language}:timing-label`,
          text.includes(language === "en" ? "Execution timing" : "执行耗时"),
        );
        check(
          `${width}:${language}:model`,
          text.includes("RequestPlan") && text.includes("Tokens"),
        );
        check(
          `${width}:${language}:tool`,
          text.includes("search_reports") && text.includes("build_report"),
        );
        check(
          `${width}:${language}:phases`,
          (await page.locator(".execution-timing ol > li").count()) === 4,
        );
        check(
          `${width}:${language}:layout`,
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth + 1,
          ),
        );
        check(
          `${width}:${language}:report-link`,
          (await page
            .locator(`a[href$="/${row.task_id}/report/pdf"]`)
            .count()) === 1,
        );
        await page.locator("#language-toggle").click();
        const switched = await page.locator(".execution-timing").textContent();
        check(
          `${width}:${language}:switch`,
          switched.includes(
            language === "en" ? "执行耗时" : "Execution timing",
          ),
        );
        if (width === 390 && language === "en") {
          await page.locator(".execution-timing summary").click();
          await page.locator(".execution-timing").scrollIntoViewIfNeeded();
          await fs.mkdir(path.join(root, "output/refinement/screenshots"), {
            recursive: true,
          });
          await page.screenshot({
            path: path.join(
              root,
              "output/refinement/screenshots/timing-mobile.png",
            ),
          });
        }
        await page.locator("#logout").click();
        await page.locator("#analysis-workspace").waitFor({ state: "hidden" });
        check(
          `${width}:${language}:logout`,
          await page.locator("#analysis-workspace").isHidden(),
        );
        await context.close();
      }
    }
    check("no-model-submissions", submissions === 0);
    check("no-page-errors", errors.length === 0);
    const receipt = {
      status: "passed",
      checks: checks.length,
      assertions: checks,
      transport: "intercepted local routes with recorded real workflow results",
      new_model_requests: 0,
    };
    await fs.writeFile(
      path.join(root, "output/refinement/browser_checks.json"),
      JSON.stringify(receipt, null, 2) + "\n",
    );
    console.log(
      JSON.stringify({
        status: receipt.status,
        checks: receipt.checks,
        new_model_requests: 0,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
