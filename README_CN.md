# FinEvoLab

<p>
  <a href="README.md"><kbd>English</kbd></a>
  <a href="README_CN.md"><kbd>简体中文</kbd></a>
</p>

**面向财报分析的可追溯金融 Agent：用 RAG 获取证据，用工具计算数值，用 harness 校验与恢复执行。**

Built on FinRobot Equity V1. This README has been modified for FinEvoLab.

FinEvoLab 是基于 FinRobot Equity V1 的独立衍生项目，将公开财报、结构化财务指标、模型规划和报告生成贯通为可运行的分析工作流，并通过组件对照、QLoRA 微调和推理量化实验评估效果。网页支持中文 / English，可选择 API 或 LoRA 规划后端。

## 核心能力

- **财报与证据**：SEC / 公司 IR 快照、财务指标抽取、期间与单位核对，以及可定位到原始来源的引用；支持指定上传的 HTML / 可读文本 PDF。
- **RAG**：固定语料使用 BM25、multilingual E5 和 RRF 混合检索；上传文件使用限定文档内检索，支持数值与正文联合分析。
- **Agent 与工具**：通过 Agents SDK 理解请求，调用指标查询、增长计算、跨公司比较和报告工具；金额与公式由程序计算，财务对象从真实工具结果组装。
- **Harness**：schema、来源与业务状态校验，最多一次定向修复，以及带版本签名的阶段检查点和断点恢复。
- **模型与界面**：DeepSeek API、Qwen3-4B-Instruct-2507 LoRA BF16 / NF4 规划后端，FastAPI 接口、双语网页及 HTML / PDF 报告。

默认 API 模式配合 CPU 检索运行。LoRA 服务部署于外部 GPU，通过 SSH 转发接入；LoRA 负责 `RequestPlan` 规划，API 模型生成解释性正文，各后端共用财务工具、RAG 和 harness。

主应用位于 `finevolab/`，复用的 Equity V1 模块与网页外壳位于 `finrobot_equity/`；`finrobot_autogen/` 和 `finrobot_desktop/` 保留上游的其他实现。

## 快速启动

以下为已验证的 Windows / PowerShell 入口。将 `$condaExe` 改为自己的 Conda 路径；已有 `finevolab` 环境可跳过环境创建和依赖安装两条命令。

```powershell
$condaExe = 'D:\ProgramSoftware\Miniconda3\Scripts\conda.exe'
& $condaExe env create -f environments/development.yml
& $condaExe run -n finevolab python -m pip install -r requirements/development.lock.txt
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
```

首次使用时，在仓库根目录构建检索资源；已有匹配资源可直接复用。编码脚本下载固定 revision 的 Embedding 模型，并在本地生成向量与缓存。

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/build_index.py
& $condaExe run --no-capture-output -n finevolab python scripts/build_vectors.py
& $condaExe run --no-capture-output -n finevolab python scripts/build_contextual_index.py
```

离线模式使用固定财报快照。自然语言 Agent 读取本地 `finrobot_equity/core/config/config.ini`，也可通过 `FINEVOLAB_MODEL_CONFIG` 指定私有配置。最小配置如下，在本机填写有效 key 和模型名称；DeepSeek 请求显式关闭 Thinking。

```ini
[API_KEYS]
openai_api_key = YOUR_API_KEY
openai_model = YOUR_MODEL_NAME
openai_base_url = https://api.deepseek.com/
```

```powershell
& $condaExe run --no-capture-output -n finevolab python -m uvicorn finevolab.integrated_web:app --host 127.0.0.1 --port 8003
```

打开 **http://127.0.0.1:8003/finevolab**。可尝试“比较 Apple 与 Microsoft 2023 财年的收入，并说明财年期间差异”，或“计算 Microsoft 2023 财年收入同比，并引用财报解释变化”。依赖锁定位于 [environments/](environments/) 和 [requirements/](requirements/)，检索模型与 revision 固定在 [retrieval.json](configs/retrieval.json)。

## 实验结果

| 实验范围 | 严格成功次数与资源指标 | 记录 |
| --- | --- | --- |
| 业务 Agent 共同支持任务 | FinEvoLab 旧引擎 179/195 → 改进引擎 195/195；65 题各重复 3 次 | [业务评测](project_notes/experiments/scope-test-v1/assessment.json) |
| 扩展业务 Agent 主测试 | 291/300，97.00%；100 题各重复 3 次 | [完整结果](project_notes/experiments/scope-test-v1/assessment.json) |
| 请求规划器正式 test | Qwen base 109/240；LoRA 两个 seed 为 212/240、213/240；DeepSeek 227/240 | [QLoRA 对照](project_notes/evaluations/planner_formal_results.json) |
| 同权重 BF16 / NF4 推理 | 严格成功 212/240、211/240；NF4 峰值已分配显存降低 62.58%，平均延时增加 47.18% | [量化结果](project_notes/evaluations/planner_quantization.json) |

业务严格分按题型核对数值、期间、来源与任务合同，并检查正文结论的证据支持；规划器严格分核对 `RequestPlan` schema 与相关请求语义。配对量化实验中，BF16 / NF4 的峰值已分配显存为 7.995 / 2.992 GiB，平均生成延时为 3.759 / 5.532 s。

项目已完成业务 Agent 评测、组件消融、双 seed QLoRA、量化对照和真实网页验收。各阶段的运行与来源索引见[实验清单](project_notes/evaluations/experiment_inventory.json)，真实 LoRA 服务及网页验收见[在线验收](project_notes/run_logs/planner_deployment_checks.json)。完整核心回归 **189 项通过**，见[页面验收](project_notes/run_logs/site_layout_checks.json)。按提交范围导出的独立文件副本通过 178 项测试，见[复现副本验收](project_notes/run_logs/experience_acceptance.json)。

## 复现实验

代码与输入按以下目录组织：`finevolab/` 为业务模块，`scripts/` 为数据与实验入口，`configs/`、`prompts/` 为功能配置，`finevolab/templates/` 为网页与业务报告的 HTML 模板，`data/` 为来源快照与冻结数据，`tests/` 为回归与前端检查。`project_notes/` 中的 JSON / JSONL、环境快照与代表性图片提供版本化实验依据；开发说明及可选实验报告 Markdown 模板在本地集中保存，正式汇总以 JSON 结果记录。

按 development / validation / test 划分，主业务任务为 50/50/100，内部编写的双语规划任务为 600/120/240；规划集的 development 部分用于 SFT 训练。直接使用仓库内冻结数据及 manifest；以下命令从现有结果重新生成数值回放，或以新的 run ID 开展 3 轮业务对照：

配对实验从 Git 历史读取原版引擎，使用保留完整提交历史的 clone。`scripts/verify_repository_distribution.py` 可核对提交范围、来源快照、冻结输入和法律文件。

```powershell
& $condaExe run --no-capture-output -n finevolab python -m unittest discover -s tests -q
& $condaExe run --no-capture-output -n finevolab python scripts/replay_planner_predictions.py --bundle data/training/planner_formal/frozen_v1 --split test --runs project_notes/experiments/planner-formal-deepseek-test project_notes/experiments/planner-formal-base-test project_notes/experiments/planner-formal-lora17-test --case-ids data/training/planner_formal/replay_case_ids.json --output output/reproduction/replay17
& $condaExe run --no-capture-output -n finevolab python scripts/run_scope_experiments.py --split test --paired --repeats 3 --run-id reproduction_scope_test
```

业务对照使用配置的真实 API。训练与量化已在 24 GB 显存的 RTX 3090 上验证，模型 revision 由 `configs/planner_model.json` 固定；正式运行的完整环境快照见 [planner_serving_environment.txt](project_notes/run_logs/planner_serving_environment.txt)。在独立 CUDA GPU Python 环境安装依赖后执行：

```bash
python -m pip install -r requirements/planner_training.in -r requirements/planner_serving.in
python scripts/train_planner.py --bundle data/training/planner_formal/frozen_v1 --config configs/planner_formal_seed17.json --output output/training/reproduction-seed17
python scripts/train_planner.py --bundle data/training/planner_formal/frozen_v1 --config configs/planner_formal_seed29.json --output output/training/reproduction-seed29
python scripts/run_quantization_comparison.py --bundle data/training/planner_formal/frozen_v1 --adapter output/training/reproduction-seed17/checkpoint-75 --mode none --output output/reproduction/quant-bf16
python scripts/run_quantization_comparison.py --bundle data/training/planner_formal/frozen_v1 --adapter output/training/reproduction-seed17/checkpoint-75 --mode nf4 --output output/reproduction/quant-nf4
```

规划器同题评测入口为 `scripts/planner_benchmark.py predict`，用 `--arm deepseek --backend api`、`--arm qwen_base --backend transformers` 或 `--arm qwen_lora --backend transformers --adapter <checkpoint>` 选择实验组。正式配置为 `--repeats 1 --repairs 0 --max-new-tokens 512 --max-input-tokens 8192`，各运行使用相同 `--bundle` 和独立 `--output`。报告中的对照采用 validation 选定的共同 epoch 1，对应 checkpoint 75。

先对 DeepSeek、Qwen base 和两个 LoRA seed 执行 `--split validation`，将输出分别保存到下方目录。以 seed 17 为主 LoRA 组、seed 29 为复验组，冻结 validation 选择：

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/planner_benchmark.py seal --bundle data/training/planner_formal/frozen_v1 --runs output/reproduction/deepseek-validation output/reproduction/base-validation output/reproduction/lora17-validation --replicate-runs output/reproduction/lora29-validation --output output/reproduction/selection.json
```

随后各组使用 `--split test --selection output/reproduction/selection.json`，保持与各自 validation 相同的模型 / adapter、环境和生成配置。将 test 输出保存到下方对应目录，分别比较两个 LoRA seed 与共用的 DeepSeek、Qwen base 对照组：

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/planner_benchmark.py compare --bundle data/training/planner_formal/frozen_v1 --split test --runs output/reproduction/deepseek-test output/reproduction/base-test output/reproduction/lora17-test --output output/reproduction/planner_seed17.json
& $condaExe run --no-capture-output -n finevolab python scripts/planner_benchmark.py compare --bundle data/training/planner_formal/frozen_v1 --split test --runs output/reproduction/deepseek-test output/reproduction/base-test output/reproduction/lora29-test --output output/reproduction/planner_seed29.json
```

各 CLI 的 `--help` 列出完整参数；原始预测与各组 manifest 保存在 `project_notes/experiments/planner-formal-*`。

LoRA 在线服务使用 `python scripts/serve_planner.py --bundle data/training/planner_formal/frozen_v1 --adapter output/training/reproduction-seed17/checkpoint-75 --port 8082`；通过 SSH 将远端 `127.0.0.1:8082` 转发至本地 `127.0.0.1:8787`，与 [planner_service.json](configs/planner_service.json) 的 loopback 地址对应。网页可选择 BF16 / NF4。原 FinNLP 子模块按固定 commit 保留，可用 `git submodule update --init --recursive` 获取。

使用重新训练的 adapter 时，将服务 `GET /health` 返回的 `adapter_sha256` 填入对应的规划器配置，并通过 `FINEVOLAB_PLANNER_CONFIG` 指定该配置文件，保持客户端与实际权重身份一致。

## 静态展示

[在线展示页](https://finevolab.lunelu.com/)提供中英文切换、带来源引用的财务案例及可交互的实验对比，完全由静态文件运行。使用固定财务观测和已记录的实验结果构建：

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/build_showcase.py
```

产物位于 `output/showcase/`，Pages 工作流发布该目录。源文件位于 `showcase/`；安装前端依赖后，可通过 `npm run test:showcase --prefix tests/frontend` 运行前端检查。

[项目展示首页](https://finevolab.lunelu.com/)提供真实归档混合分析回放，包括请求计划、检索片段、工具输出、校验、修复状态和实际用量。上传证据通过 SHA-256 匹配公开 SEC 快照，提供可访问的来源链接。证据浏览器支持按公司、事实所属财年、指标和原文筛选，保留报告年份、单位和实际期间。

展示首页集中呈现项目结果、归档执行回放和证据浏览，主按钮进入独立分析应用。分析应用提供邀请登录、问题输入、执行状态、带引用的结果、历史任务及 HTML/PDF 下载。旧 `experience.html` 地址跳转到展示首页的对应区域。页面路由与双语检查见[页面验收](project_notes/run_logs/site_layout_checks.json)。

使用已有模型配置，在本地启动邀请制真实分析：

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/manage_experience.py invite --user owner
& $condaExe run --no-capture-output -n finevolab python scripts/manage_experience.py serve
```

打开 `http://127.0.0.1:8085/`，使用保存在 `output/experience/invite-*.txt` 的邀请口令。服务读取已有 `FINEVOLAB_MODEL_CONFIG` 设置。邀请摘要与策略保存在 `output/experience/access.json`，会话与任务归属保存在 `output/experience/access.sqlite3`，均留在本地。独立应用按账户隔离任务与报告下载，执行原子化的个人及全站每日任务额度、并发限制、幂等提交和单任务 `RunBudget`。每日额度按 UTC 计算，失败与中断的执行也计入额度；重启后任务标记为中断。默认每人每天 5 个任务，全站每天 20 个任务，同时运行 1 个；单任务最多 6 次模型调用、10 次工具调用、20,000 个 Token 和 240 秒。启动服务前配置邀请与额度。浏览器会话有效期为 12 小时。服务默认仅监听本机。配置 `--public-url` 后，公开主机名要求 HTTPS，并使用安全会话 Cookie；转发头仅信任本机连接器。公开 Pages 工作台的回放与证据浏览不发起模型请求。

通过 Cloudflare Tunnel 公开同一个邀请制应用时，配置 `configs/tunnel.json`，将连接器令牌本身保存到 `output/tunnel/token.txt`。安装器校验固定发行版的 SHA-256。在一个终端中启动后端：

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/manage_tunnel.py install
& $condaExe run --no-capture-output -n finevolab python scripts/manage_experience.py serve --public-url https://app.lunelu.com
```

在另一个终端中启动隐藏运行的连接器并检查就绪状态：

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/manage_tunnel.py start
& $condaExe run --no-capture-output -n finevolab python scripts/manage_tunnel.py status
& $condaExe run --no-capture-output -n finevolab python scripts/manage_tunnel.py verify
```

在 Cloudflare 隧道配置中添加已发布应用路由：主机名 `app.lunelu.com`，服务 `http://127.0.0.1:8085`，路径过滤留空。[真实分析工作台](https://app.lunelu.com/)使用这条路由。本地后端与连接器均需保持运行。`verify` 检查 HTTPS 路由、真实工作台和匿名任务访问拦截，不调用模型。安装连接器或验证公网需要代理时使用 `--proxy`。`stop` 仅停止受管理的连接器。另行记录浏览器验收时，可通过 `FINEVOLAB_BROWSER_REPORT` 指定独立的报告文件名。

工作台验收见[工作台检查](project_notes/run_logs/experience_acceptance.json)。实际浏览器检查使用 `FINEVOLAB_CHROMIUM` 指定已安装的 Chromium 可执行文件，在本地服务运行时执行 `node tests/frontend/check_experience_live.cjs`。将 `FINEVOLAB_INVITATION_FILE` 指向本地邀请文件，并添加 `--real`，即可执行真实模型分析，验证工具、引用、校验、报告、刷新恢复及幂等提交。驱动读取邀请口令而不输出其内容。

## 许可证

FinEvoLab 自有代码与文档采用 **Apache-2.0**，版权与许可声明见 [COPYRIGHT](COPYRIGHT)。保留上游 [LICENSE](LICENSE)、[NOTICE](NOTICE) 和 [Trademark policy](TRADEMARK_POLICY.md)。第三方数据、模型与其他材料遵循各自条款。
