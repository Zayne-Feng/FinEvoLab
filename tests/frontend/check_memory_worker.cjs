"use strict";
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { JSDOM } = require("jsdom");
const root = path.resolve(__dirname, "../..");
const dom = new JSDOM(
  fs.readFileSync(
    path.join(root, "finevolab/templates/experience.html"),
    "utf8",
  ),
  {
    url: "http://127.0.0.1:8085/",
    runScripts: "outside-only",
  },
);
const w = dom.window;
const calls = [];
let status = "queued";
let checks = 0;
function check(value) {
  assert.ok(value);
  checks++;
}
w.fetch = async (url, options = {}) => {
  calls.push({ url, ...options });
  let body = {};
  if (url.endsWith("/session"))
    body = { user: "alice", used: 0, daily_tasks: 5, execution_mode: "worker" };
  else if (url.endsWith("/tasks") && options.method === "GET") body = [];
  else if (url.endsWith("/tasks"))
    body = { task_id: "a".repeat(32), status: "queued" };
  else if (url.endsWith("/cancel")) {
    status = "cancelled";
    body = { status };
  } else if (url.includes("/tasks/"))
    body = {
      task_id: "a".repeat(32),
      status,
      phases: {},
      queue_seconds: 0.01,
      execution_attempts: 0,
    };
  return { ok: true, status: 200, json: async () => body };
};
const settle = async () => {
  for (let i = 0; i < 20; i++)
    await new Promise((resolve) => setTimeout(resolve, 0));
};
(async () => {
  w.eval(fs.readFileSync(path.join(root, "showcase/experience.js"), "utf8"));
  await settle();
  const id = (name) => w.document.getElementById(name);
  check(!id("use-memory").disabled);
  id("use-memory").checked = true;
  id("question").value = "上一年呢？";
  id("question-form").dispatchEvent(
    new w.Event("submit", { cancelable: true }),
  );
  await settle();
  const submitted = calls.find(
    (row) => row.url.endsWith("/tasks") && row.method === "POST",
  );
  check(JSON.parse(submitted.body).use_memory);
  check(JSON.parse(submitted.body).conversation_id.length > 15);
  check(id("submit-task").disabled);
  check(id("task-output").textContent.includes("排队"));
  const cancel = id("task-output").querySelector("button");
  check(Boolean(cancel));
  cancel.click();
  await settle();
  check(calls.some((row) => row.url.endsWith("/cancel")));
  check(!id("submit-task").disabled);
  id("language-toggle").click();
  check(w.document.documentElement.lang === "en");
  check(id("new-conversation").textContent === "New conversation");
  id("amount-scale").value = "millions";
  id("save-preferences").click();
  await settle();
  const saved = calls.find((row) => row.method === "PUT");
  check(JSON.parse(saved.body).amount_scale === "millions");
  check(saved.headers["X-FinEvoLab"] === "1");
  id("new-conversation").click();
  check(id("question").value === "");
  id("clear-memory").click();
  await settle();
  check(calls.some((row) => row.method === "DELETE"));
  check(id("live-status").textContent === "Business memory cleared.");
  dom.window.close();
  console.log(
    JSON.stringify({ status: "passed", checks, real_model_calls: 0 }),
  );
})().catch((error) => {
  dom.window.close();
  console.error(error);
  process.exitCode = 1;
});
