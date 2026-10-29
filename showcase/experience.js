"use strict";
(() => {
  const data = window.FINEVOLAB_SHOWCASE?.experience;
  const live = document.documentElement.dataset.live === "true";
  const id = (name) => document.getElementById(name);
  const copy = {
    "zh-CN": {
      skip: "跳转到正文",
      title: "让问题，成为有据可循的分析。",
      intro:
        "登录后提出财报问题，查看执行状态、证据引用与分析结果，并下载报告。",
      home: "返回项目展示 ↗",
      replay: "执行回放",
      evidence: "证据浏览器",
      live: "真实分析",
      replayNote:
        "真实归档记录，按阶段查看。回放不发起新的模型调用；原始请求、财报片段与模型回答保留原文。",
      archive: "归档执行 · 非实时运行",
      record: "查看原始记录 ↗",
      evidenceNote:
        "按事实所属财年筛选。报告年份单独展示，保留原始单位、实际期间和原文定位。",
      company: "公司",
      year: "财年",
      metric: "指标",
      find: "搜索原文",
      all: "全部",
      revenue: "收入",
      operating_income: "营业利润",
      net_income: "净利润",
      observations: "条观测",
      empty: "没有匹配的证据。",
      reportYear: "来源报告年份",
      period: "实际期间",
      source: "打开原始来源 ↗",
      plan: "请求计划",
      tools: "工具与检索",
      answer: "分析结果",
      validation: "校验与修复",
      usage: "用量与身份",
      liveNote:
        "登录后，使用服务端 DeepSeek 配置执行请求规划、财报检索、金融工具、harness 校验与报告生成。",
      connectedMode: "真实分析服务已接入，请使用邀请口令登录。",
      signedInMode: "已登录，可提交分析问题或查看历史任务。",
      sourceYearNote:
        "指标财年由实际期间确定；来源报告可披露较早财年的对比数据。",
      invite: "邀请口令",
      signIn: "登录",
      signOut: "退出登录",
      question: "分析问题",
      example: "填入分析示例",
      run: "开始真实分析",
      taskNote:
        "使用固定公开财报。每日额度按 UTC 统计；每次任务执行都有模型、工具、Token 与时间预算。",
      quota: "今日任务",
      running: "正在执行，可在此查看阶段状态。",
      complete: "执行完成",
      businessStatus: "业务结果",
      businessComplete: "分析完成",
      insufficient_data: "证据不足",
      clarification_required: "需要澄清",
      periods_not_aligned: "期间不一致",
      invalid_parameters: "参数无效",
      failed: "执行失败",
      interrupted: "执行已中断",
      pending: "准备中",
      prepare: "输入准备",
      analyze: "规划、检索与计算",
      validate: "结果校验",
      report: "生成报告",
      download: "下载报告",
      unauthorized: "请先登录，或重新检查邀请口令。",
      busy: "额度或并发限制已触发，请稍后重试。",
      unavailable: "服务暂时不可用，请稍后重试。",
      invalid: "请求未通过校验，请检查输入。",
      notFound: "任务不存在或不属于当前账户。",
      network: "连接失败，请检查网络后重试。",
      signedOut: "已退出登录。",
      progress: "当前任务",
      error: "错误类型",
      budget: "单任务预算",
      history: "历史任务",
      loading: "正在读取任务。",
    },
    en: {
      skip: "Skip to content",
      title: "Your question. An evidence-backed answer.",
      intro:
        "Sign in, ask a financial question, follow execution, inspect cited evidence, and download your report.",
      home: "Back to the showcase ↗",
      replay: "Execution replay",
      evidence: "Evidence explorer",
      live: "Live analysis",
      replayNote:
        "Inspect a real archived execution by stage. Replay makes no new model calls. Original requests, source passages, and model answers retain their recorded language.",
      archive: "Archived execution · Recorded run",
      record: "View original record ↗",
      evidenceNote:
        "Filter by the fiscal year of the fact. Report year, original units, actual periods, and source locators remain explicit.",
      company: "Company",
      year: "Fiscal year",
      metric: "Metric",
      find: "Search source text",
      all: "All",
      revenue: "Revenue",
      operating_income: "Operating income",
      net_income: "Net income",
      observations: "observations",
      empty: "No matching evidence.",
      reportYear: "Source report year",
      period: "Actual period",
      source: "Open original source ↗",
      plan: "Request plan",
      tools: "Tools & retrieval",
      answer: "Analysis result",
      validation: "Validation & repair",
      usage: "Usage & identity",
      liveNote:
        "After signing in, use the server-side DeepSeek configuration for request planning, report retrieval, financial tools, harness validation, and report generation.",
      connectedMode:
        "Analysis service connected. Sign in with your invitation.",
      signedInMode: "Signed in. Ask a question or open a saved analysis.",
      sourceYearNote:
        "The metric fiscal year follows its actual period. A later source report can disclose earlier comparative figures.",
      invite: "Invitation code",
      signIn: "Sign in",
      signOut: "Sign out",
      question: "Analysis question",
      example: "Use example question",
      run: "Run live analysis",
      taskNote:
        "Uses pinned public filings. Daily quotas reset in UTC. Each task has model, tool, token, and time budgets.",
      quota: "Tasks today",
      running: "Analysis running. Execution stages update here.",
      complete: "Execution complete",
      businessStatus: "Analysis outcome",
      businessComplete: "Analysis complete",
      insufficient_data: "Insufficient evidence",
      clarification_required: "Clarification required",
      periods_not_aligned: "Periods differ",
      invalid_parameters: "Invalid parameters",
      failed: "Execution failed",
      interrupted: "Execution interrupted",
      pending: "Pending",
      prepare: "Prepare input",
      analyze: "Plan, retrieve & calculate",
      validate: "Validate result",
      report: "Build report",
      download: "Download report",
      unauthorized: "Sign in or check your invitation code.",
      busy: "Quota or concurrency limit reached. Please try again later.",
      unavailable: "Service unavailable. Please try again later.",
      invalid: "Request validation failed. Check your input.",
      notFound: "Task not found for this account.",
      network: "Connection failed. Check your network and retry.",
      signedOut: "Signed out.",
      progress: "Current task",
      error: "Error type",
      budget: "Per-task budget",
      history: "Task history",
      loading: "Loading task.",
    },
  };
  let language = "zh-CN";
  try {
    if (localStorage.getItem("finevolab.showcase.language") === "en")
      language = "en";
  } catch {}
  let stage = "plan",
    session = null,
    timer = null,
    generation = 0,
    lastState = null;
  let requestIdentity = null;
  const t = (key) => copy[language][key] || key;
  const node = (tag, text, cls) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (cls) element.className = cls;
    return element;
  };
  const pretty = (value) => node("pre", JSON.stringify(value, null, 2));
  function link(url, text) {
    const element = node("a", text, "text-link");
    if (new URL(url, location.href).protocol !== "https:")
      throw new Error("Evidence links require HTTPS");
    element.href = url;
    element.target = "_blank";
    element.rel = "noopener noreferrer";
    return element;
  }
  function evidenceCard(item, observation) {
    const details = node("details"),
      summary = node(
        "summary",
        `${item.company}${observation ? " · FY" + observation.fiscal_year : ""} · ${t("reportYear")} ${item.report_year} · ${item.section || item.kind || ""}`,
      );
    details.dataset.evidenceId = item.id;
    details.append(summary);
    if (observation) {
      details.append(
        node(
          "p",
          `${t("period")}: ${observation.period_start} → ${observation.period_end}`,
          "evidence-meta",
        ),
      );
      if (observation.fiscal_year !== item.report_year)
        details.append(node("p", t("sourceYearNote"), "technical"));
    }
    details.append(
      node("p", item.id, "technical"),
      node("pre", item.text),
      node("p", item.locator, "technical"),
      link(item.public_source_url || item.source_url, t("source")),
    );
    return details;
  }
  function renderReplay() {
    const replay = data.replays[0];
    id("replay-question").textContent = replay.question;
    id("replay-identity").textContent =
      `${replay.task_id} · SHA-256 ${replay.sha256}`;
    id("replay-source").href =
      window.FINEVOLAB_SHOWCASE.repository + "/blob/main/" + replay.source;
    id("replay-steps").replaceChildren();
    for (const key of ["plan", "tools", "answer", "validation", "usage"]) {
      const button = node("button", t(key));
      button.type = "button";
      button.dataset.stage = key;
      button.setAttribute("aria-pressed", String(stage === key));
      button.onclick = () => {
        stage = key;
        renderReplay();
      };
      id("replay-steps").append(button);
    }
    const detail = id("replay-detail");
    detail.replaceChildren(node("h3", t(stage)));
    if (stage === "plan") detail.append(pretty(replay.request));
    if (stage === "tools") {
      detail.append(pretty(replay.events));
      replay.evidence.forEach((item) => detail.append(evidenceCard(item)));
    }
    if (stage === "answer")
      detail.append(
        pretty({ answer: replay.answer, narrative: replay.narrative }),
      );
    if (stage === "validation")
      detail.append(
        pretty({ validation: replay.validation, repair: replay.repair }),
      );
    if (stage === "usage")
      detail.append(
        pretty({
          usage: replay.usage,
          elapsed_seconds: replay.elapsed_seconds,
          data_version: replay.data_version,
          runtime_backend: replay.runtime_backend,
        }),
      );
  }
  function filterOptions(name, values, translate = false) {
    const select = id(name),
      previous = select.value;
    select.replaceChildren(new Option(t("all"), ""));
    values.forEach((value) =>
      select.add(
        new Option(translate ? t(value) : String(value), String(value)),
      ),
    );
    select.value = previous;
    if (select.selectedIndex < 0) select.selectedIndex = 0;
  }
  function renderEvidence() {
    const query = id("filter-query").value.trim().toLowerCase();
    const rows = data.observations.filter(
      (item) =>
        (!id("filter-company").value ||
          item.company === id("filter-company").value) &&
        (!id("filter-year").value ||
          String(item.fiscal_year) === id("filter-year").value) &&
        (!id("filter-metric").value ||
          item.name === id("filter-metric").value) &&
        (!query ||
          `${item.evidence.text} ${item.evidence.id} ${item.company}`
            .toLowerCase()
            .includes(query)),
    );
    id("evidence-count").textContent = `${rows.length} ${t("observations")}`;
    id("evidence-rows").replaceChildren();
    if (!rows.length) id("evidence-rows").append(node("p", t("empty")));
    rows.forEach((item) => {
      const details = evidenceCard(item.evidence);
      details.querySelector("summary").textContent =
        `${item.company} · FY${item.fiscal_year} · ${t(item.name)} · ${Number(item.value).toLocaleString(language)} ${item.unit} · ${new URL(item.evidence.source_url).hostname}`;
      details.insertBefore(
        node(
          "p",
          `${t("reportYear")}: ${item.evidence.report_year} · ${t("period")}: ${item.period_start} → ${item.period_end} · ${item.currency} / ${item.unit}`,
          "evidence-meta",
        ),
        details.children[1],
      );
      id("evidence-rows").append(details);
    });
    id("data-version").textContent = `Data version · ${data.data_version}`;
  }
  function renderSession() {
    id("login-form").hidden = !live || Boolean(session);
    id("analysis-workspace").hidden = !session;
    id("live-mode").textContent = t(session ? "signedInMode" : "connectedMode");
    if (session)
      id("session-info").textContent =
        `${session.user} · ${t("quota")} ${session.used}/${session.daily_tasks} · UTC`;
  }
  function renderTask(state) {
    lastState = state;
    const target = id("task-output");
    target.replaceChildren(
      node("h3", `${t("progress")} · ${state.task_id}`),
      node("p", state.question),
    );
    const phases = node("div", undefined, "stage-status");
    Object.entries(state.phases || {}).forEach(([key, value]) =>
      phases.append(node("span", `${t(key)} · ${t(value.status)}`)),
    );
    target.append(phases, node("p", t(state.status)));
    if (state.business_status)
      target.append(
        node(
          "p",
          `${t("businessStatus")}: ${state.business_status === "complete" ? t("businessComplete") : t(state.business_status)}`,
        ),
      );
    if (state.error_type)
      target.append(node("p", `${t("error")}: ${state.error_type}`));
    if (state.answer) {
      target.append(node("h3", t("answer")), pretty(state.answer));
      if (state.narrative) target.append(pretty(state.narrative));
    }
    if (state.request) {
      const details = node("details");
      details.append(
        node("summary", t("plan")),
        pretty(state.request),
        pretty(state.events),
      );
      target.append(details);
    }
    const observations = [
      ...(state.answer?.metrics || []),
      ...(state.answer?.calculations || []).flatMap(
        (calculation) => calculation.inputs || [],
      ),
    ];
    (state.evidence || []).forEach((item) =>
      target.append(
        evidenceCard(
          item,
          observations.find(
            (observation) => observation.evidence_id === item.id,
          ),
        ),
      ),
    );
    if (state.validation)
      target.append(
        node("h3", t("validation")),
        pretty({ validation: state.validation, repair: state.repair }),
      );
    if (state.usage)
      target.append(
        node("h3", t("usage")),
        pretty({ usage: state.usage, elapsed_seconds: state.elapsed_seconds }),
      );
    const reports = node("div", undefined, "report-actions");
    (state.reports || []).forEach((kind) => {
      const a = node(
        "a",
        `${t("download")} · ${kind.toUpperCase()}`,
        "text-link",
      );
      a.href = `/experience/api/tasks/${state.task_id}/report/${kind}`;
      reports.append(a);
    });
    target.append(reports);
  }
  function setStatus(text) {
    id("live-status").textContent = text;
  }
  async function api(path, body) {
    const response = await fetch("/experience/api/" + path, {
      method: body ? "POST" : "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: body
        ? { "Content-Type": "application/json", "X-FinEvoLab": "1" }
        : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
      const key =
        {
          401: "unauthorized",
          403: "unauthorized",
          404: "notFound",
          409: "invalid",
          422: "invalid",
          429: "busy",
          503: "unavailable",
        }[response.status] || "unavailable";
      throw Object.assign(new Error(key), { status: response.status });
    }
    return response.json();
  }
  function clearSession() {
    generation++;
    clearTimeout(timer);
    session = null;
    lastState = null;
    requestIdentity = null;
    try {
      sessionStorage.removeItem("finevolab.experience.task");
    } catch {}
    id("task-output").replaceChildren();
    id("task-history").replaceChildren();
    id("question").value = "";
    id("submit-task").disabled = false;
    renderSession();
  }
  function fail(error) {
    setStatus(t(copy[language][error.message] ? error.message : "network"));
    if (error.status === 401) clearSession();
  }
  async function history() {
    const owner = session?.user;
    const rows = await api("tasks");
    if (session?.user !== owner) return;
    id("task-history").replaceChildren(node("strong", t("history")));
    rows.forEach((row) => {
      const button = node(
        "button",
        `${row.question.slice(0, 50)} · ${t(row.status)}`,
        "history-button",
      );
      button.type = "button";
      button.onclick = () => watch(row.task_id);
      id("task-history").append(button);
    });
  }
  async function watch(taskId) {
    clearTimeout(timer);
    const version = ++generation;
    try {
      sessionStorage.setItem("finevolab.experience.task", taskId);
    } catch {}
    const poll = async () => {
      try {
        const state = await api("tasks/" + taskId);
        if (version !== generation) return;
        renderTask(state);
        id("submit-task").disabled = state.status === "running";
        if (state.status === "running") timer = setTimeout(poll, 1800);
        else {
          const updated = await api("session");
          if (version !== generation) return;
          session = updated;
          renderSession();
          await history();
          setStatus(t(state.status));
        }
      } catch (error) {
        if (version === generation) {
          id("submit-task").disabled = false;
          fail(error);
        }
      }
    };
    await poll();
  }
  async function restore() {
    const version = generation;
    try {
      const updated = await api("session");
      if (version !== generation) return;
      session = updated;
      renderSession();
      await history();
      if (version !== generation) return;
      let task;
      try {
        task = sessionStorage.getItem("finevolab.experience.task");
      } catch {}
      if (task) await watch(task);
    } catch (error) {
      if (version === generation) {
        if (error.status === 401) clearSession();
        else fail(error);
      }
    }
  }
  function languageChanged() {
    if (live) {
      document.documentElement.lang = language;
      document.title = "FinEvoLab · " + t("title");
      id("language-toggle").textContent =
        language === "en" ? "中文 ↗" : "EN ↗";
      id("language-toggle").setAttribute(
        "aria-label",
        language === "en" ? "切换为简体中文" : "Switch to English",
      );
      id("live-mode").textContent = t("connectedMode");
    }
    document.querySelectorAll("[data-text]").forEach((element) => {
      element.textContent = t(element.dataset.text);
    });
    if (!live) {
      filterOptions(
        "filter-company",
        [...new Set(data.observations.map((item) => item.company))].sort(),
      );
      filterOptions(
        "filter-year",
        [...new Set(data.observations.map((item) => item.fiscal_year))].sort(
          (a, b) => b - a,
        ),
      );
      filterOptions(
        "filter-metric",
        [...new Set(data.observations.map((item) => item.name))].sort(),
        true,
      );
      renderReplay();
      renderEvidence();
    } else {
      renderSession();
      if (lastState) renderTask(lastState);
      if (session) history().catch(fail);
    }
  }
  if (!live) {
    ["filter-company", "filter-year", "filter-metric"].forEach(
      (name) => (id(name).onchange = renderEvidence),
    );
    id("filter-query").oninput = renderEvidence;
    document.addEventListener("finevolab:language", (event) => {
      language = event.detail.language;
      languageChanged();
    });
    language = document.documentElement.lang === "en" ? "en" : "zh-CN";
    languageChanged();
    return;
  }
  id("language-toggle").onclick = () => {
    language = language === "en" ? "zh-CN" : "en";
    try {
      localStorage.setItem("finevolab.showcase.language", language);
    } catch {}
    languageChanged();
  };
  id("login-form").onsubmit = async (event) => {
    event.preventDefault();
    const button = id("login-form").querySelector("button");
    button.disabled = true;
    try {
      await api("login", { invite: id("invite").value });
      id("invite").value = "";
      await restore();
      setStatus("");
    } catch (error) {
      fail(error);
    } finally {
      button.disabled = false;
    }
  };
  id("logout").onclick = async () => {
    try {
      await api("logout", {});
      clearSession();
      setStatus(t("signedOut"));
    } catch (error) {
      fail(error);
    }
  };
  id("example").onclick = () => {
    id("question").value =
      language === "en"
        ? "Calculate Microsoft FY2024 annual revenue growth and explain the disclosed business drivers using original annual report passages."
        : "计算 Microsoft 2024 财年的年度收入增长率，并引用财报原文解释披露的业务驱动因素。";
  };
  id("question-form").onsubmit = async (event) => {
    event.preventDefault();
    const question = id("question").value.trim();
    if (!question) return;
    if (!requestIdentity || requestIdentity.question !== question)
      requestIdentity = { question, request_id: crypto.randomUUID() };
    id("submit-task").disabled = true;
    setStatus(t("running"));
    try {
      const task = await api("tasks", requestIdentity);
      await watch(task.task_id);
      requestIdentity = null;
    } catch (error) {
      id("submit-task").disabled = false;
      fail(error);
    }
  };
  languageChanged();
  if (live) restore();
})();
