"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const root = path.resolve(__dirname, "../..");
const site = path.resolve(
  process.argv[2] || path.join(root, "output/showcase"),
);
const html = fs.readFileSync(path.join(site, "index.html"), "utf8");
const payload = fs.readFileSync(path.join(site, "data.js"), "utf8");
const script = fs.readFileSync(path.join(site, "app.js"), "utf8");
let checks = 0;
function check(value, message) {
  assert.ok(value, message);
  checks += 1;
}
function open(options = {}) {
  const dom = new JSDOM(html, {
    url: "https://finevolab.lunelu.com/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  if (options.language)
    dom.window.localStorage.setItem(
      "finevolab.showcase.language",
      options.language,
    );
  if (options.storageBlocked)
    Object.defineProperty(dom.window, "localStorage", {
      get() {
        throw new Error("Storage unavailable");
      },
    });
  dom.window.fetch = () => {
    throw new Error("A static showcase cannot call a backend");
  };
  dom.window.eval(payload);
  dom.window.eval(script);
  return dom;
}
(async () => {
  const dom = open();
  const { document } = dom.window;
  const id = (name) => document.getElementById(name);
  check(
    document.documentElement.lang === "zh-CN",
    "The default language must be Chinese",
  );
  check(
    document
      .querySelector('meta[http-equiv="Content-Security-Policy"]')
      .content.includes("connect-src 'none'"),
    "Backend connections must be disabled",
  );
  check(
    [...document.scripts].every((node) =>
      node.src.startsWith("https://finevolab.lunelu.com/"),
    ),
    "Scripts must be local",
  );
  check(
    id("business-rate").textContent === "97.00%",
    "The business rate must match the formal assessment",
  );
  check(
    id("planner-gain").textContent === "+42.92pp",
    "Planner gain must use seed 17 and the base control",
  );
  check(
    id("memory-saving").textContent === "62.58%",
    "Memory reduction must match the paired comparison",
  );
  for (const language of ["zh-CN", "en"]) {
    if (document.documentElement.lang !== language)
      id("language-toggle").click();
    check(
      document.documentElement.lang === language,
      "Language switching must update the document",
    );
    for (const kind of ["growth", "comparison", "margin"]) {
      id("case-" + kind).click();
      check(
        id("case-" + kind).getAttribute("aria-selected") === "true",
        "The selected case must be announced",
      );
      check(
        id("case-panel").getAttribute("aria-labelledby") === "case-" + kind,
        "Case panel labels must follow selection",
      );
      check(
        document.querySelectorAll(".source-link").length === 2,
        "Each case must retain its input citations",
      );
      check(
        [...document.querySelectorAll(".source-link")].every((node) =>
          node.href.startsWith("https://"),
        ),
        "Citations must use HTTPS",
      );
      if (kind === "growth")
        check(
          id("case-answer").textContent.includes("15.67%"),
          "Revenue growth must match Decimal computation",
        );
      if (kind === "comparison") {
        check(
          id("period-status").classList.contains("unaligned"),
          "Different fiscal periods must be disclosed",
        );
        check(
          id("case-answer").textContent.includes("171.370"),
          "Revenue comparison must preserve the monetary scale",
        );
        check(
          id("case-observations").textContent.includes("2023-09-30") &&
            id("case-observations").textContent.includes("2023-06-30"),
          "Original financial dates must be retained",
        );
      }
      if (kind === "margin")
        check(
          id("case-answer").textContent.includes("44.64%"),
          "Operating margin must use matching periods",
        );
    }
    for (const kind of ["business", "planner", "quantization"]) {
      id("result-" + kind).click();
      check(
        id("result-" + kind).getAttribute("aria-selected") === "true",
        "Experiment tab state must match the panel",
      );
      check(
        id("result-evidence").href.includes("/blob/main/project_notes/"),
        "Every result must link to versioned evidence",
      );
      if (kind === "business")
        check(
          id("result-chart").textContent.includes("179/195") &&
            id("result-chart").textContent.includes("195/195"),
          "The business comparison must retain a shared denominator",
        );
      if (kind === "planner")
        check(
          ["109/240", "212/240", "213/240", "227/240"].every((text) =>
            id("result-chart").textContent.includes(text),
          ),
          "All planner arms must share 240 test tasks",
        );
      if (kind === "quantization")
        check(
          ["7.995", "2.992", "3.759", "5.532"].every((text) =>
            id("result-chart").textContent.includes(text),
          ),
          "Quantization must retain both memory and latency",
        );
    }
    check(
      [...document.querySelectorAll("[data-i18n]")].every(
        (node) =>
          node.textContent.trim() && !node.textContent.includes("undefined"),
      ),
      "All localized labels must resolve",
    );
    check(
      id("readme-link").href.endsWith(
        language === "en" ? "README.md" : "README_CN.md",
      ),
      "The guide must follow the interface language",
    );
  }
  id("case-growth").click();
  id("case-growth").dispatchEvent(
    new dom.window.KeyboardEvent("keydown", {
      key: "ArrowDown",
      bubbles: true,
    }),
  );
  check(
    document.activeElement.id === "case-comparison",
    "Keyboard selection must move focus",
  );
  check(
    id("case-comparison").getAttribute("aria-selected") === "true",
    "Keyboard selection must update case content",
  );
  let copied = "";
  Object.defineProperty(dom.window.navigator, "clipboard", {
    value: {
      async writeText(value) {
        copied = value;
      },
    },
    configurable: true,
  });
  id("copy-command").click();
  await new Promise((resolve) => setImmediate(resolve));
  check(
    copied.includes("git clone https://github.com/Zayne-Feng/FinEvoLab.git"),
    "Copy must contain a working repository command",
  );
  check(
    id("copy-status").textContent === "Commands copied",
    "Copy must provide completion feedback",
  );
  Object.defineProperty(dom.window.navigator, "clipboard", {
    value: {
      async writeText() {
        throw new Error("Clipboard unavailable");
      },
    },
  });
  id("copy-command").click();
  await new Promise((resolve) => setImmediate(resolve));
  check(
    id("copy-status").textContent.includes("Select and copy"),
    "Clipboard rejection must provide a usable fallback",
  );
  const english = open({ language: "en" });
  check(
    english.window.document.documentElement.lang === "en",
    "Saved language must survive a page reload",
  );
  english.window.close();
  const blocked = open({ storageBlocked: true });
  check(
    blocked.window.document.documentElement.lang === "zh-CN",
    "Blocked storage must not prevent rendering",
  );
  blocked.window.document.getElementById("language-toggle").click();
  check(
    blocked.window.document.documentElement.lang === "en",
    "Language switching must work without storage",
  );
  blocked.window.close();
  const ids = [...document.querySelectorAll("[id]")].map((node) => node.id);
  check(ids.length === new Set(ids).size, "Document IDs must be unique");
  for (const link of document.querySelectorAll('a[href^="#"]'))
    if (link.hash)
      check(
        document.getElementById(link.hash.slice(1)),
        "Local navigation targets must exist",
      );
  check(
    !document.querySelector('a[href^="http://"],form'),
    "The static page must not submit forms or use insecure external links",
  );
  dom.window.close();
  console.log(JSON.stringify({ status: "passed", checks, backendRequests: 0 }));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
