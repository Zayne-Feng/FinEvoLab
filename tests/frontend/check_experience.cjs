"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const root = path.resolve(__dirname, "../..");
const site = path.join(root, "output/showcase");
let checks = 0;
function check(value, message) {
  assert.ok(value, message);
  checks++;
}
function open(live = false) {
  const html = fs.readFileSync(
    live
      ? path.join(root, "finevolab/templates/experience.html")
      : path.join(site, "index.html"),
    "utf8",
  );
  const dom = new JSDOM(html, {
    url: live ? "http://127.0.0.1:8085/" : "https://finevolab.lunelu.com/",
    runScripts: "outside-only",
  });
  if (!live) {
    dom.window.eval(fs.readFileSync(path.join(site, "data.js"), "utf8"));
    dom.window.eval(fs.readFileSync(path.join(site, "app.js"), "utf8"));
  }
  return dom;
}
(async () => {
  const dom = open(),
    w = dom.window,
    d = w.document,
    id = (name) => d.getElementById(name);
  const errors = [];
  w.addEventListener("error", (event) => errors.push(event.error));
  let calls = 0;
  w.fetch = () => {
    calls++;
    throw Error("Static workspaces must not call APIs");
  };
  w.eval(fs.readFileSync(path.join(site, "experience.js"), "utf8"));
  const rows = w.FINEVOLAB_SHOWCASE.experience.observations;
  for (const lang of ["zh-CN", "en"]) {
    if (d.documentElement.lang !== lang) id("language-toggle").click();
    check(d.documentElement.lang === lang, "Language must change");
    check(
      !id("login-form"),
      "The showcase must not contain an analysis login form",
    );
    check(
      d.querySelector(".hero-actions a").href === "https://app.lunelu.com/",
      "The primary action must open live analysis",
    );
    for (const stage of ["plan", "tools", "answer", "validation", "usage"]) {
      d.querySelector(`[data-stage="${stage}"]`).click();
      check(
        d
          .querySelector(`[data-stage="${stage}"]`)
          .getAttribute("aria-pressed") === "true",
        "Replay selection must be explicit",
      );
      check(
        id("replay-detail").textContent.length > 50,
        "Each stage must contain archived output",
      );
    }
    id("filter-company").value = "MSFT";
    id("filter-year").value = "2024";
    id("filter-metric").value = "revenue";
    id("filter-company").dispatchEvent(new w.Event("change"));
    const expected = rows.filter(
      (x) =>
        x.company === "MSFT" && x.fiscal_year === 2024 && x.name === "revenue",
    );
    check(
      id("evidence-rows").querySelectorAll("details").length ===
        expected.length,
      "Filters must match factual years",
    );
    check(
      id("evidence-rows").textContent.includes("245"),
      "Revenue must retain the source amount",
    );
    check(
      id("evidence-rows").textContent.includes("2023-07-01"),
      "Actual fiscal dates must remain visible",
    );
    id("filter-query").value = "<script>unmatched</script>";
    id("filter-query").dispatchEvent(new w.Event("input"));
    check(
      id("evidence-rows").querySelectorAll("details").length === 0,
      "Unmatched searches must be empty",
    );
    id("filter-query").value = "";
    ["filter-company", "filter-year", "filter-metric"].forEach(
      (name) => (id(name).value = ""),
    );
    id("filter-query").dispatchEvent(new w.Event("input"));
    check(
      id("evidence-rows").querySelectorAll("details").length === rows.length,
      "Clearing filters must restore the corpus",
    );
  }
  check(
    errors.length === 0,
    "Public interactions must not throw runtime errors",
  );
  check(calls === 0, "Public workspace must make zero backend requests");
  check(!d.querySelector("script:not([src])"), "Scripts must remain external");
  check(
    !JSON.stringify(w.FINEVOLAB_SHOWCASE.experience).includes("source_path"),
    "Public data must exclude local paths",
  );
  dom.window.close();
  const redirectSource = fs.readFileSync(
    path.join(site, "experience_redirect.js"),
    "utf8",
  );
  for (const [hash, expected] of [
    ["", "./index.html#replay"],
    ["#replay", "./index.html#replay"],
    ["#evidence", "./index.html#evidence"],
    ["#live", "https://app.lunelu.com/"],
    ["#unknown", "./index.html#replay"],
  ]) {
    let destination;
    require("node:vm").runInNewContext(redirectSource, {
      location: { hash, replace: (value) => (destination = value) },
    });
    check(
      destination === expected,
      "Legacy workspace links must reach their canonical destination",
    );
  }
  const legacy = new JSDOM(
    fs.readFileSync(path.join(site, "experience.html"), "utf8"),
  );
  check(
    legacy.window.document
      .querySelector('meta[http-equiv="refresh"]')
      .content.includes("./index.html#replay"),
    "Legacy navigation must work without JavaScript",
  );
  legacy.window.close();
  const live = open(true),
    lw = live.window,
    ld = lw.document;
  check(
    !ld.getElementById("replay") && !ld.getElementById("evidence"),
    "Analysis must not duplicate public archive sections",
  );
  check(
    !ld.querySelector('script[src$="data.js"]'),
    "Analysis must not load the public archive payload",
  );
  let authenticated = false,
    posts = 0;
  const saved = {
    task_id: "a".repeat(32),
    status: "complete",
    question: "test",
    phases: { validate: { status: "complete" } },
    answer: {
      status: "complete",
      calculations: [
        {
          inputs: [
            {
              evidence_id: "metric-one",
              fiscal_year: 2024,
              period_start: "2023-07-01",
              period_end: "2024-06-30",
            },
          ],
        },
      ],
    },
    validation: { status: "passed" },
    evidence: [
      {
        id: "metric-one",
        company: "MSFT",
        report_year: 2026,
        source_url: "https://example.com/filing",
        text: "Revenue source",
        locator: "income-statements",
      },
    ],
    reports: ["pdf"],
  };
  lw.fetch = async (url, options = {}) => {
    const route = url.replace("/experience/api/", "");
    if (route === "login") {
      authenticated = true;
      return { ok: true, json: async () => ({ user: "alice" }) };
    }
    if (route === "logout") {
      authenticated = false;
      return { ok: true, json: async () => ({}) };
    }
    if (!authenticated) return { ok: false, status: 401 };
    if (route === "session")
      return {
        ok: true,
        json: async () => ({ user: "alice", used: posts, daily_tasks: 5 }),
      };
    if (route === "tasks" && options.method === "POST") {
      posts++;
      return {
        ok: true,
        json: async () => ({ task_id: saved.task_id, status: "running" }),
      };
    }
    if (route === "tasks") return { ok: true, json: async () => [] };
    return { ok: true, json: async () => saved };
  };
  lw.eval(fs.readFileSync(path.join(site, "experience.js"), "utf8"));
  const tick = () => new Promise((resolve) => setTimeout(resolve, 20));
  await tick();
  check(
    !ld.getElementById("login-form").hidden,
    "Analysis application must show login",
  );
  ld.getElementById("invite").value = "a".repeat(43);
  ld.getElementById("login-form").dispatchEvent(
    new lw.Event("submit", { cancelable: true }),
  );
  await tick();
  check(
    !ld.getElementById("analysis-workspace").hidden,
    "Authenticated workspace must open",
  );
  check(
    ld.getElementById("invite").value === "",
    "Invitation must clear after authentication",
  );
  check(
    ld.getElementById("live-mode").textContent.includes("已登录"),
    "Authenticated sessions must not display a login instruction",
  );
  ld.getElementById("example").click();
  check(
    ld.getElementById("question").value.includes("Microsoft"),
    "Example must request hybrid analysis",
  );
  ld.getElementById("question-form").dispatchEvent(
    new lw.Event("submit", { cancelable: true }),
  );
  await tick();
  check(posts === 1, "Submit must create exactly one request");
  check(
    ld.getElementById("task-output").textContent.includes("passed"),
    "Validation result must be rendered",
  );
  check(
    ld
      .querySelector('#task-output a[href$="/report/pdf"]')
      .href.endsWith("/report/pdf"),
    "Download must target the owned report route",
  );
  const evidence = ld.querySelector('details[data-evidence-id="metric-one"]');
  check(
    evidence.querySelector("summary").textContent.includes("FY2024") &&
      evidence.querySelector("summary").textContent.includes("2026"),
    "Metric fiscal year must remain distinct from source report year",
  );
  check(
    evidence.textContent.includes("2023-07-01") &&
      evidence.textContent.includes("2024-06-30"),
    "Actual financial periods must be visible",
  );
  ld.getElementById("language-toggle").click();
  check(
    ld.getElementById("live-mode").textContent.startsWith("Signed in."),
    "Authenticated status must follow the selected language",
  );
  check(
    ld
      .querySelector('details[data-evidence-id="metric-one"] summary')
      .textContent.includes("Source report year 2026"),
    "Source-year labels must follow the selected language",
  );
  ld.getElementById("language-toggle").click();
  await tick();
  authenticated = false;
  ld.getElementById("question-form").dispatchEvent(
    new lw.Event("submit", { cancelable: true }),
  );
  await tick();
  check(
    ld.getElementById("analysis-workspace").hidden,
    "Expired sessions must close the workspace",
  );
  check(
    ld.getElementById("task-output").textContent === "",
    "Expired sessions must clear old answers",
  );
  check(
    lw.sessionStorage.getItem("finevolab.experience.task") === null,
    "Expired sessions must clear the remembered task",
  );
  ld.getElementById("invite").value = "a".repeat(43);
  ld.getElementById("login-form").dispatchEvent(
    new lw.Event("submit", { cancelable: true }),
  );
  await tick();
  check(
    ld.getElementById("task-output").textContent === "",
    "New sessions must not reveal old task contents",
  );
  ld.getElementById("logout").click();
  await tick();
  check(
    ld.getElementById("analysis-workspace").hidden,
    "Logout must close the workspace",
  );
  check(
    ld.getElementById("task-output").textContent === "",
    "Logout must clear task contents",
  );
  check(
    ld.getElementById("live-mode").textContent.includes("请使用邀请口令登录"),
    "Logout must restore the login instruction",
  );
  lw.close();
  console.log(
    JSON.stringify({
      status: "passed",
      checks,
      static_api_calls: calls,
      mocked_analysis_requests: posts,
    }),
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
