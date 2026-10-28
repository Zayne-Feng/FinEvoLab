"use strict";
(() => {
  const data = window.FINEVOLAB_SHOWCASE;
  const translations = {
    "zh-CN": {
      title: "FinEvoLab · 有据可循的金融分析",
      description:
        "以公开财报为依据的金融 Agent，融合 RAG、工具调用、执行校验与 QLoRA。探索真实实验结果和财务分析案例。",
      skip: "跳转到正文",
      navigation: "主导航",
      navWorkflow: "工作流",
      navCases: "分析案例",
      navResults: "实验结果",
      eyebrow: "FINANCIAL AGENT · RESEARCH & ENGINEERING",
      heroLine1: "让每一个财务判断",
      heroLine2: "都有据可循。",
      heroDescription:
        "从公开财报到可追溯的分析报告。融合 RAG、金融工具与执行校验，让模型理解问题，让程序保障计算。",
      exploreCases: "探索分析案例",
      exploreResults: "查看实验结果",
      researchDesk: "财报研究台",
      annualRevenue: "年度收入",
      usdBillions: "十亿美元 · USD",
      yearOverYear: "同比增长",
      heroChart: "Microsoft 年度收入比较",
      sourceBound: "数值计算与财报来源绑定",
      evidenceFirst: "Evidence first.",
      evidenceFirstSub: "每一步，都可追溯。",
      keyResults: "主要实测结果",
      businessMetric: "扩展业务主测严格成功率",
      businessScope:
        "{businessQuestions} 道任务 · 每题重复 {businessRepetitions} 次",
      plannerMetric: "微调后规划严格成功率提升",
      plannerScope: "seed 17 对比 Qwen base · {plannerTest} 条规划请求",
      memoryMetric: "NF4 峰值分配显存降低",
      memoryScope: "同权重 BF16 / NF4 配对对照",
      testsMetric: "核心回归测试通过",
      testsScope: "覆盖工具、工作流、服务与交付",
      workflowEyebrow: "01 / THE WORKFLOW",
      workflowTitle: "从来源，到结论。",
      workflowDescription:
        "把检索、推理、计算和校验连接起来，保留完整的证据路径。",
      sourceTitle: "公开财报",
      sourceText: "SEC 与公司 IR 快照，保留财年、单位及来源身份。",
      ragTitle: "混合检索",
      ragText: "结合关键词与语义召回，保留正文和表格上下文。",
      agentTitle: "请求规划",
      agentText: "识别公司、指标、期间与意图，调度财务工具。",
      harnessTitle: "计算与校验",
      harnessText: "程序计算金额与同比；校验来源，最多一次定向修复并恢复执行。",
      reportTitle: "证据化报告",
      reportText: "呈现数值、期间、公式与引用，输出 HTML 和 PDF。",
      casesEyebrow: "02 / FINANCIAL CASE STUDIES",
      casesTitle: "打开一份分析，沿着证据阅读。",
      casesDescription:
        "基于仓库固定财报观测的交互案例。选择问题，查看计算、财务期间与原始来源。",
      caseTabs: "财务分析案例",
      growthTitle: "收入增长",
      comparisonTitle: "跨公司比较",
      marginTitle: "经营利润率",
      staticLabel: "预设案例 · 本地交互",
      researchQuestion: "研究问题",
      financialObservations: "财务观测",
      sourceTrail: "原始证据",
      sourceTrailNote: "公司财报 · 可定位引用",
      periodsAligned: "财务期间一致",
      periodsDiffer: "财年期间存在差异",
      annualPeriods: "相邻完整财年",
      revenue: "收入",
      operating_income: "经营利润",
      growthQuestion: "Microsoft 2024 财年收入同比增长多少？",
      comparisonQuestion:
        "比较 Apple 与 Microsoft 2023 财年的收入，并说明期间差异。",
      marginQuestion: "Microsoft 2024 财年的经营利润率是多少？",
      resultsEyebrow: "03 / MEASURED RESULTS",
      resultsTitle: "用实验，回答性能问题。",
      resultsDescription:
        "从组件对照到双 seed 微调，再到同权重量化。每组结果都链接到仓库中的实验依据。",
      resultTabs: "实验组",
      businessTab: "业务 Agent",
      plannerTab: "QLoRA 微调",
      quantTab: "推理量化",
      viewEvidence: "查看实验依据",
      businessLabel: "严格业务成功 · 扩展主测",
      businessTitle: "{businessSuccesses} / {businessTotal} 次严格成功",
      businessDescription:
        "主测包含 {businessQuestions} 道题，每题重复 {businessRepetitions} 次；严格评分按题型检查数值、期间、来源、任务合同及正文证据支持。右侧在 {commonQuestions} 道共同题、{commonTotal} 次运行的相同范围内比较两代引擎。",
      earlierEngine: "早期引擎 · 共同题",
      currentEngine: "当前引擎 · 共同题",
      plannerLabel: "Qwen3-4B-Instruct-2507 · RequestPlan",
      plannerTitle: "{plannerTrain} 条训练样本，{plannerTest} 条请求检验。",
      plannerDescription:
        "QLoRA 微调 RequestPlan 规划器，严格评分检查 schema 与请求语义。各组共用 {plannerTest} 条 test 请求（{plannerClusters} 个模板簇）、生成预算与评分规则，修复次数为 0。两个 seed 采用 validation 选定的共同 epoch {selectedEpoch}；DeepSeek 为共用 API 对照。",
      quantLabel: "同一 LoRA 权重 · 配对推理",
      quantTitle: "以更少显存，完成同题推理。",
      quantDescription:
        "同权重、同题、同预算对照。NF4 峰值分配显存降低 {memory}%；平均生成延时增加 {latency}%。严格成功次数为 BF16 {bf16Successes}/{quantTotal}、NF4 {nf4Successes}/{quantTotal}。",
      quantPrecision: "推理精度",
      quantMemory: "峰值分配显存",
      quantLatency: "平均延时",
      datasetTitle: "冻结数据，让实验可复现。",
      datasetText:
        "业务任务 {businessDevelopment} / {businessValidation} / {businessTest}；自建双语合成规划任务 {plannerTrain} / {plannerValidation} / {plannerTest}，分别用于 development / validation / test。",
      viewData: "查看数据 ↗",
      reproduceEyebrow: "04 / BUILD ON THE WORK",
      reproduceTitle: "从阅读结果，到运行实验。",
      reproduceText:
        "源码、冻结数据、预测记录与评测入口均已公开。按照 README 准备环境，即可在本地运行完整分析工作流。",
      openRepository: "浏览 GitHub 仓库 ↗",
      readGuide: "阅读复现指南 ↗",
      copy: "复制命令",
      copied: "命令已复制",
      copyFallback: "请选中上方命令进行复制。",
      footerText: "以公开数据为基础，以可复现实验为依据。",
      upstream: "基于 FinRobot Equity V1",
    },
    en: {
      title: "FinEvoLab · Evidence-backed financial analysis",
      description:
        "A financial Agent grounded in public filings, combining RAG, financial tools, execution validation, and QLoRA. Explore measured results and financial case studies.",
      skip: "Skip to content",
      navigation: "Main navigation",
      navWorkflow: "Workflow",
      navCases: "Case studies",
      navResults: "Results",
      eyebrow: "FINANCIAL AGENT · RESEARCH & ENGINEERING",
      heroLine1: "Financial insight.",
      heroLine2: "Evidence, attached.",
      heroDescription:
        "From public filings to traceable financial reports. RAG finds evidence, models interpret requests, and financial tools keep calculations precise.",
      exploreCases: "Explore the cases",
      exploreResults: "View the results",
      researchDesk: "RESEARCH DESK",
      annualRevenue: "Annual revenue",
      usdBillions: "USD billions",
      yearOverYear: "year over year",
      heroChart: "Microsoft annual revenue comparison",
      sourceBound: "Computed values, linked to original filings",
      evidenceFirst: "Evidence first.",
      evidenceFirstSub: "Trace every step.",
      keyResults: "Key measured results",
      businessMetric: "Strict success on the expanded business test",
      businessScope:
        "{businessQuestions} tasks · {businessRepetitions} repetitions per task",
      plannerMetric: "Planner strict success gain after fine-tuning",
      plannerScope: "seed 17 vs. Qwen base · {plannerTest} planning requests",
      memoryMetric: "Lower NF4 peak allocated GPU memory",
      memoryScope: "Same-weight BF16 / NF4 comparison",
      testsMetric: "Core regression tests passed",
      testsScope: "Tools, workflows, serving, and delivery",
      workflowEyebrow: "01 / THE WORKFLOW",
      workflowTitle: "From sources to conclusions.",
      workflowDescription:
        "Connect retrieval, reasoning, computation, and validation while preserving the evidence trail.",
      sourceTitle: "Public filings",
      sourceText:
        "SEC and company IR snapshots with fiscal periods, units, and source identities.",
      ragTitle: "Hybrid retrieval",
      ragText:
        "Keyword and semantic retrieval preserve narrative and table context.",
      agentTitle: "Request planning",
      agentText:
        "Identify companies, metrics, periods, and intent, then select financial tools.",
      harnessTitle: "Compute & validate",
      harnessText:
        "Compute values, validate evidence, repair at most once, and resume execution.",
      reportTitle: "Cited reports",
      reportText:
        "Present values, periods, formulas, and citations in HTML and PDF reports.",
      casesEyebrow: "02 / FINANCIAL CASE STUDIES",
      casesTitle: "Open an analysis. Follow the evidence.",
      casesDescription:
        "Interactive cases computed from frozen financial observations. Choose a question to explore values, fiscal periods, and original sources.",
      caseTabs: "Financial case studies",
      growthTitle: "Revenue growth",
      comparisonTitle: "Company comparison",
      marginTitle: "Operating margin",
      staticLabel: "Preset cases · Local interaction",
      researchQuestion: "RESEARCH QUESTION",
      financialObservations: "FINANCIAL OBSERVATIONS",
      sourceTrail: "ORIGINAL EVIDENCE",
      sourceTrailNote: "Company filings · Located citations",
      periodsAligned: "Matching fiscal periods",
      periodsDiffer: "Fiscal periods differ",
      annualPeriods: "Consecutive full fiscal years",
      revenue: "Revenue",
      operating_income: "Operating income",
      growthQuestion:
        "How much did Microsoft revenue grow in fiscal year 2024?",
      comparisonQuestion:
        "Compare Apple and Microsoft FY2023 revenue and explain their fiscal-period differences.",
      marginQuestion:
        "What was Microsoft operating margin in fiscal year 2024?",
      resultsEyebrow: "03 / MEASURED RESULTS",
      resultsTitle: "Performance, answered by experiments.",
      resultsDescription:
        "Component comparisons, two-seed fine-tuning, and same-weight quantization. Every result links to its recorded experimental evidence.",
      resultTabs: "Experiment groups",
      businessTab: "Business Agent",
      plannerTab: "QLoRA tuning",
      quantTab: "Quantization",
      viewEvidence: "Read the evidence",
      businessLabel: "STRICT BUSINESS SUCCESS · EXPANDED TEST",
      businessTitle: "{businessSuccesses} / {businessTotal} strict successes",
      businessDescription:
        "The main test contains {businessQuestions} tasks with {businessRepetitions} repetitions each. Strict scoring applies task-specific checks for values, periods, sources, task contracts, and narrative evidence support. The chart compares both engines on {commonQuestions} common tasks, totaling {commonTotal} runs per engine.",
      earlierEngine: "Earlier engine · Common tasks",
      currentEngine: "Current engine · Common tasks",
      plannerLabel: "Qwen3-4B-Instruct-2507 · RequestPlan",
      plannerTitle: "Train on {plannerTrain}. Evaluate {plannerTest} requests.",
      plannerDescription:
        "QLoRA tunes the RequestPlan planner; strict scoring checks schema and request semantics. All arms share {plannerTest} test requests ({plannerClusters} template clusters), the generation budget, and scoring rules, with 0 repairs. Both seeds use validation-selected epoch {selectedEpoch}; DeepSeek is the shared API control.",
      quantLabel: "SAME LORA WEIGHTS · PAIRED INFERENCE",
      quantTitle: "Same tasks. A smaller memory footprint.",
      quantDescription:
        "Same weights, tasks, and budget. NF4 reduces peak allocated memory by {memory}% and increases mean generation latency by {latency}%. Strict successes: BF16 {bf16Successes}/{quantTotal}, NF4 {nf4Successes}/{quantTotal}.",
      quantPrecision: "Inference precision",
      quantMemory: "Peak allocated memory",
      quantLatency: "Mean latency",
      datasetTitle: "Frozen inputs. Reproducible experiments.",
      datasetText:
        "Business tasks: {businessDevelopment} / {businessValidation} / {businessTest}. Internally authored synthetic bilingual planner tasks: {plannerTrain} / {plannerValidation} / {plannerTest} for development / validation / test.",
      viewData: "Explore the data ↗",
      reproduceEyebrow: "04 / BUILD ON THE WORK",
      reproduceTitle: "From reading results to running experiments.",
      reproduceText:
        "Source code, frozen data, predictions, and scoring tools are public. Follow the README to set up the environment and run the analysis workflow locally.",
      openRepository: "Explore GitHub ↗",
      readGuide: "Read the setup guide ↗",
      copy: "Copy commands",
      copied: "Commands copied",
      copyFallback: "Select and copy the commands above.",
      footerText:
        "Grounded in public data. Supported by reproducible experiments.",
      upstream: "Built on FinRobot Equity V1",
    },
  };
  const keys = Object.keys(translations.en).sort();
  if (
    JSON.stringify(keys) !==
    JSON.stringify(Object.keys(translations["zh-CN"]).sort())
  )
    throw new Error("Translation keys differ");
  let language = "zh-CN";
  try {
    language =
      localStorage.getItem("finevolab.showcase.language") === "en"
        ? "en"
        : "zh-CN";
  } catch {}
  let selectedCase = "growth";
  let selectedResult = "business";
  const byId = (id) => document.getElementById(id);
  const values = {
    businessQuestions: data.business.questions,
    businessRepetitions: data.business.repetitions,
    businessSuccesses: data.business.successes,
    businessTotal: data.business.total,
    commonQuestions: data.business.common_questions,
    commonTotal: data.business.common_total,
    businessDevelopment: data.splits.business.development,
    businessValidation: data.splits.business.validation,
    businessTest: data.splits.business.test,
    plannerTrain: data.splits.planner.development,
    plannerValidation: data.splits.planner.validation,
    plannerTest: data.splits.planner.test,
    plannerClusters: data.planner_template_clusters,
    selectedEpoch: data.selected_epoch,
    bf16Successes: data.quantization[0].successes,
    nf4Successes: data.quantization[1].successes,
    quantTotal: data.quantization[0].total,
    memory: data.memory_reduction,
    latency: data.latency_increase,
  };
  const t = (key) =>
    translations[language][key]?.replace(/\{(\w+)\}/g, (_, name) => {
      if (!(name in values)) throw new Error("A display value is missing");
      return String(values[name]);
    });
  const create = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const percent = (successes, total) => ((100 * successes) / total).toFixed(2);
  function metric(id, value, suffix) {
    const node = byId(id);
    node.replaceChildren(document.createTextNode(value));
    if (suffix) node.append(create("span", "", suffix));
  }
  function renderCase() {
    const value = data.cases[selectedCase];
    byId("case-question").textContent = t(selectedCase + "Question");
    byId("case-panel").setAttribute("aria-labelledby", "case-" + selectedCase);
    const status = byId("period-status");
    status.textContent = t(
      selectedCase === "growth"
        ? "annualPeriods"
        : selectedCase === "comparison" && !value.periods_aligned
          ? "periodsDiffer"
          : "periodsAligned",
    );
    status.classList.toggle(
      "unaligned",
      selectedCase === "comparison" && !value.periods_aligned,
    );
    byId("case-observations").replaceChildren(
      ...value.observations.map((row) => {
        const card = create("div", "observation");
        card.append(
          create(
            "span",
            "obs-name",
            `${row.company} · FY${row.year} · ${t(row.metric)}`,
          ),
          create("strong", "", row.billions),
          create("small", "", t("usdBillions")),
          create("small", "", `${row.period_start} → ${row.period_end}`),
        );
        return card;
      }),
    );
    let answer;
    if (selectedCase === "growth") {
      const [before, after] = value.observations;
      answer =
        language === "en"
          ? `Microsoft reported revenue of USD ${after.billions} billion in FY2024, compared with USD ${before.billions} billion in FY2023: growth of ${value.result}%. Both observations cover full fiscal years.`
          : `Microsoft 2024 财年收入为 ${after.billions} 十亿美元，2023 财年为 ${before.billions} 十亿美元，同比增长 ${value.result}%。两项观测均对应完整财年。`;
    } else if (selectedCase === "comparison") {
      const [apple, microsoft] = value.observations;
      answer =
        language === "en"
          ? `Apple FY2023 revenue was USD ${apple.billions} billion and Microsoft revenue was USD ${microsoft.billions} billion, a difference of USD ${value.result} billion. Their fiscal periods differ; both date ranges are shown above.`
          : `Apple 2023 财年收入为 ${apple.billions} 十亿美元，Microsoft 为 ${microsoft.billions} 十亿美元，相差 ${value.result} 十亿美元。两家公司财年期间不同，具体日期见上方观测。`;
    } else {
      answer =
        language === "en"
          ? `Microsoft FY2024 operating margin was ${value.result}%, computed as operating income divided by revenue. Both inputs use USD and the same fiscal period.`
          : `Microsoft 2024 财年经营利润率为 ${value.result}%，由经营利润除以收入计算。两项输入的币种和财务期间一致。`;
    }
    byId("case-answer").textContent = answer;
    byId("case-formula").textContent =
      value.formula ||
      `${value.observations[0].billions} − ${value.observations[1].billions} = ${value.result} USD billion`;
    byId("case-sources").replaceChildren(
      ...value.observations.map((row) => {
        const link = create("a", "source-link");
        link.href = row.source_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        const details = create(
          "span",
          "",
          `${row.company} · FY${row.year} · ${t(row.metric)}`,
        );
        details.append(
          create("small", "", `${row.evidence_id} · ${row.locator}`),
        );
        link.append(details, create("span", "", "↗"));
        return link;
      }),
    );
  }
  function bar(label, value, total, display) {
    const row = create("div", "bar-row");
    const header = create("div", "bar-label");
    header.append(create("span", "", label), create("strong", "", display));
    const track = create("div", "bar-track");
    const fill = create("div", "bar-fill");
    fill.style.width = `${Math.min(100, Math.max(0, (100 * value) / total))}%`;
    track.append(fill);
    row.append(header, track);
    return row;
  }
  function renderResult() {
    const kind = selectedResult;
    byId("result-panel").setAttribute("aria-labelledby", "result-" + kind);
    const prefix = kind === "quantization" ? "quant" : kind;
    byId("result-label").textContent = t(prefix + "Label");
    byId("result-title").textContent = t(prefix + "Title");
    byId("result-description").textContent = t(prefix + "Description");
    byId("result-evidence").href =
      data.repository + "/blob/main/" + data.evidence[kind];
    const chart = byId("result-chart");
    chart.replaceChildren();
    if (kind === "business") {
      const b = data.business;
      chart.append(
        bar(
          t("earlierEngine"),
          b.common_before,
          b.common_total,
          `${b.common_before}/${b.common_total} · ${percent(b.common_before, b.common_total)}%`,
        ),
        bar(
          t("currentEngine"),
          b.common_after,
          b.common_total,
          `${b.common_after}/${b.common_total} · ${percent(b.common_after, b.common_total)}%`,
        ),
      );
    } else if (kind === "planner") {
      chart.append(
        ...data.planner.map((row) =>
          bar(
            row.label,
            row.successes,
            row.total,
            `${row.successes}/${row.total} · ${percent(row.successes, row.total)}%`,
          ),
        ),
      );
    } else {
      chart.append(
        ...data.quantization.map((row) =>
          bar(row.label, Number(row.memory), 8.5, `${row.memory} GiB`),
        ),
      );
      const table = create("table", "quant-table");
      const head = create("thead");
      const heading = create("tr");
      [t("quantPrecision"), t("quantMemory"), t("quantLatency")].forEach(
        (text) => heading.append(create("th", "", text)),
      );
      head.append(heading);
      const body = create("tbody");
      data.quantization.forEach((row) => {
        const line = create("tr");
        [row.label, `${row.memory} GiB`, `${row.latency} s`].forEach((text) =>
          line.append(create("td", "", text)),
        );
        body.append(line);
      });
      table.append(head, body);
      chart.append(table);
    }
  }
  function selectTab(button, attribute) {
    const kind = button.dataset[attribute];
    const selector = attribute === "case" ? "[data-case]" : "[data-result]";
    document.querySelectorAll(selector).forEach((node) => {
      const active = node === button;
      node.classList.toggle("active", active);
      node.setAttribute("aria-selected", String(active));
      node.tabIndex = active ? 0 : -1;
    });
    if (attribute === "case") {
      selectedCase = kind;
      renderCase();
    } else {
      selectedResult = kind;
      renderResult();
    }
  }
  function applyLanguage() {
    document.documentElement.lang = language;
    document.title = t("title");
    document.querySelector('meta[name="description"]').content =
      t("description");
    document.querySelectorAll("[data-i18n]").forEach((node) => {
      const text = t(node.dataset.i18n);
      if (text === undefined) throw new Error("A translation is missing");
      node.textContent = text;
    });
    document
      .querySelectorAll("[data-i18n-aria]")
      .forEach((node) =>
        node.setAttribute("aria-label", t(node.dataset.i18nAria)),
      );
    const button = byId("language-toggle");
    button.textContent = language === "en" ? "中文 ↗" : "EN ↗";
    button.setAttribute(
      "aria-label",
      language === "en" ? "切换为简体中文" : "Switch to English",
    );
    byId("readme-link").href =
      data.repository +
      "/blob/main/" +
      (language === "en" ? "README.md" : "README_CN.md");
    byId("copy-status").textContent = "";
    metric(
      "business-rate",
      percent(data.business.successes, data.business.total),
      "%",
    );
    metric("planner-gain", "+" + data.planner_gain, "pp");
    metric("memory-saving", data.memory_reduction, "%");
    byId("regression-tests").textContent = data.regression_tests;
    renderCase();
    renderResult();
  }
  document.querySelectorAll('[role="tablist"]').forEach((list) => {
    const tabs = [...list.querySelectorAll('[role="tab"]')];
    tabs.forEach((button, index) => {
      const attribute = button.hasAttribute("data-case") ? "case" : "result";
      button.addEventListener("click", () => selectTab(button, attribute));
      button.addEventListener("keydown", (event) => {
        let next;
        if (["ArrowRight", "ArrowDown"].includes(event.key))
          next = (index + 1) % tabs.length;
        else if (["ArrowLeft", "ArrowUp"].includes(event.key))
          next = (index + tabs.length - 1) % tabs.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = tabs.length - 1;
        if (next === undefined) return;
        event.preventDefault();
        selectTab(tabs[next], attribute);
        tabs[next].focus();
      });
    });
  });
  byId("language-toggle").addEventListener("click", () => {
    language = language === "en" ? "zh-CN" : "en";
    try {
      localStorage.setItem("finevolab.showcase.language", language);
    } catch {}
    applyLanguage();
  });
  byId("copy-command").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(
        byId("reproduce-command").textContent,
      );
      byId("copy-status").textContent = t("copied");
    } catch {
      byId("copy-status").textContent = t("copyFallback");
    }
  });
  const hero = data.cases.growth;
  byId("hero-revenue").textContent = hero.observations[1].billions;
  byId("hero-before").textContent = hero.observations[0].billions;
  byId("hero-after").textContent = hero.observations[1].billions;
  byId("hero-growth").textContent = "+" + hero.result + "%";
  document.querySelector(".hero-column.previous i").style.height =
    (
      (83 * Number(hero.observations[0].value)) /
      Number(hero.observations[1].value)
    ).toFixed(3) + "px";
  applyLanguage();
})();
