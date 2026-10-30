# FinEvoLab

<p align="center">
  <a href="README.md"><kbd>English</kbd></a>
  <a href="README_CN.md"><kbd>简体中文</kbd></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?logo=python&amp;logoColor=white" alt="Python 3.11">
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&amp;logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/License-Apache--2.0-7B6DAA" alt="Apache-2.0">
</p>

**财报有依据，计算可复核，分析可追溯。**

**在线体验：**[项目展示](https://finevolab.lunelu.com/) · [邀请制分析](https://app.lunelu.com/)。

FinEvoLab 支持财报问答、跨公司比较、增长率与利润率计算，以及带来源引用的报告生成。项目融合混合 RAG、确定性财务工具、可恢复执行框架与 QLoRA 请求规划，提供中英文 Web 界面及可复现评测。

[核心能力](#section-capabilities) · [快速启动](#section-quick-start) · [实验结果](#section-results) · [复现实验](#section-reproduce) · [可选服务](#section-services) · [网页部署](#section-web) · [许可证](#section-license)

---

<div id="section-capabilities"></div>

## 核心能力

| 能力 | 实现与输出 |
| --- | --- |
| **财报与证据** | SEC / 公司 IR 快照、财务指标抽取、期间与单位核对，以及可定位到原始来源的引用；支持指定上传的 HTML / 可读文本 PDF。 |
| **RAG** | 固定语料使用 BM25、multilingual E5 和 RRF 混合检索；上传文件使用限定文档内检索，支持数值与正文联合分析；可选 Redis 共享检索缓存及版本化 Milvus 向量后端。 |
| **Agent 与工具** | 通过 Agents SDK 理解请求，调用指标查询、增长计算、跨公司比较和报告工具；金额与公式由程序计算，财务对象从真实工具结果组装。 |
| **Harness** | schema、来源与业务状态校验，最多一次定向修复，以及带版本签名的阶段检查点和断点恢复；支持同一任务预算内的自检与隔离证据审查角色。 |
| **模型与界面** | DeepSeek API、Qwen3-4B-Instruct-2507 LoRA BF16 / NF4 规划后端，FastAPI 接口、双语网页及 HTML / PDF 报告。 |

```text
Question → RequestPlan → Evidence retrieval & financial tools → Validation → Report
```

默认 API 模式配合 CPU 检索运行。LoRA 服务部署于外部 GPU，通过 SSH 转发接入；LoRA 负责 `RequestPlan` 规划，API 模型生成解释性正文，各后端共用财务工具、RAG 和 harness。

FinEvoLab 的核心流程位于 `finevolab/`，配套目录提供数据准备、训练与实验入口。复用的基础模块及固定版本对照基线保存在 `finrobot_equity/`。

<div id="section-quick-start"></div>

## 快速启动

请使用已能运行 `conda` 的终端。以下命令采用 PowerShell，从完整克隆仓库开始；已有的 `finevolab` 环境可直接复用。

### 1. 创建环境

```powershell
git clone https://github.com/Zayne-Feng/FinEvoLab.git
cd FinEvoLab

conda env create -f environments/development.yml
conda run -n finevolab python -m pip install -r requirements/development.lock.txt
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
```

### 2. 构建检索资源

首次使用时，在仓库根目录构建检索资源；已有匹配资源可直接复用。编码脚本下载固定 revision 的 Embedding 模型，并在本地生成向量与缓存。

```powershell
conda run --no-capture-output -n finevolab python scripts/build_index.py
conda run --no-capture-output -n finevolab python scripts/build_vectors.py
conda run --no-capture-output -n finevolab python scripts/build_contextual_index.py
```

### 3. 配置模型服务

离线模式使用固定财报快照。自然语言 Agent 读取本地 `finrobot_equity/core/config/config.ini`，也可通过 `FINEVOLAB_MODEL_CONFIG` 指定私有配置。最小配置如下，在本机填写有效 key 和模型名称；DeepSeek 请求显式关闭 Thinking。

```ini
[API_KEYS]
openai_api_key = YOUR_API_KEY
openai_model = YOUR_MODEL_NAME
openai_base_url = https://api.deepseek.com/
```

### 4. 启动应用

```powershell
conda run --no-capture-output -n finevolab python -m uvicorn finevolab.integrated_web:app --host 127.0.0.1 --port 8003
```

打开 **http://127.0.0.1:8003/finevolab**。可尝试“比较 Apple 与 Microsoft 2023 财年的收入，并说明财年期间差异”，或“计算 Microsoft 2023 财年收入同比，并引用财报解释变化”。依赖锁定位于 [environments/](environments/) 和 [requirements/](requirements/)，检索模型与 revision 固定在 [retrieval.json](configs/retrieval.json)。

<details>
<summary><strong>财务 API、利润率审计与显式情景计算</strong></summary>

确定性计算工具还支持 `operating_margin`、`net_margin`、`operating_margin_change` 和 `net_margin_change`。利润率使用百分比，利润率变化使用百分点。每项结果保留来源输入、实际财务期间、披露年份、公式、引用、数据签名与字段级审计检查；每项输入采用其最新可用披露。本地 `POST /api/derived-metrics` 接口和以下命令可直接完成计算，无需模型调用：

```powershell
conda run --no-capture-output -n finevolab python scripts/calculate_derived.py `
  --company MSFT `
  --fiscal-year 2024 `
  --metric operating_margin_change `
  --report-dir output/derived_report
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8003/api/derived-metrics' -ContentType 'application/json' -Body '{"company":"MSFT","fiscal_year":2024,"metric":"net_margin"}'
```

分析控制包括带累计用量与修复次数核算的公司级模型步骤检查点、按事实期间与披露血缘进行的来源核对，以及用于合并重叠确定性缓存请求的进程内 singleflight。版本化任务方法和无损财务上下文编码通过 `configs/analysis_extensions.json`、`configs/analysis_methods.json` 配置。本地 `POST /api/scenarios` 接口根据明确的收入和利润率假设生成敏感性表格，保留历史基准及其审计。

```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8003/api/source-reconciliation' -ContentType 'application/json' -Body '{"company":"MSFT","fiscal_year":2024,"metric":"revenue"}'
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8003/api/scenarios' -ContentType 'application/json' -Body '{"company":"MSFT","fiscal_year":2024,"margin":"operating_margin","revenue_changes_percent":[-10,0,10],"margin_changes_percentage_points":[-2,0,2]}'
```

</details>

<div id="section-results"></div>

## 实验结果

| 实验范围 | 严格成功次数与资源指标 | 记录 |
| --- | --- | --- |
| 业务 Agent 共同支持任务 | FinEvoLab 旧引擎 179/195 → 改进引擎 195/195；65 题各重复 3 次 | [业务评测](project_notes/experiments/scope-test-v1/assessment.json) |
| 扩展业务 Agent 主测试 | 291/300，97.00%；100 题各重复 3 次 | [完整结果](project_notes/experiments/scope-test-v1/assessment.json) |
| 请求规划器正式 test | Qwen base 109/240；LoRA 两个 seed 为 212/240、213/240；DeepSeek 227/240 | [QLoRA 对照](project_notes/evaluations/planner_formal_results.json) |
| 同权重 BF16 / NF4 推理 | 严格成功 212/240、211/240；NF4 峰值已分配显存降低 62.58%，平均延时增加 47.18% | [量化结果](project_notes/evaluations/planner_quantization.json) |
| 检索缓存对照 | 135 组查询配置 × 5 次重复；返回结果一致；预热均值 68.52 → 6.16 ms，11.12× | [缓存对照](project_notes/experiments/retrieval-cache-v1/summary.json) |
| Redis 共享检索缓存 | 四组对照，77 题 × 3 次；693 次配对的片段完全一致；热查询均值 91.13 → 22.44 ms，4.06× | [服务对照](project_notes/evaluations/retrieval_service_comparison.json) |
| 同预算正文审查 | 原分析者 22/24；自检 24/24；隔离 Reviewer 23/24；72 次真实工作流，经两个隔离上下文盲评 | [角色对照](project_notes/experiments/agent-roles-v1/summary.json) |
| 无依据结论受控测试 | 接受决策：来源校验 10/20，自检 20/20，Reviewer 18/20；两种模型审查均拒绝全部 10 个无依据对照 | [支持关系对照](project_notes/experiments/claim-review-v3/summary.json) |
| 可选语义重排 | top 5 完整标准原文命中 15/36 → 23/36；业务配对严格成功均为 8/8 | [证据对照](project_notes/experiments/reranker-bilingual-v1/summary.json)、[业务验收](project_notes/experiments/runtime-refinement-v1/summary.json) |

业务严格分按题型核对数值、期间、来源与任务合同，并检查正文结论的证据支持；规划器严格分核对 `RequestPlan` schema 与相关请求语义。配对量化实验中，BF16 / NF4 的峰值已分配显存为 7.995 / 2.992 GiB，平均生成延时为 3.759 / 5.532 s。

项目已完成业务 Agent 评测、组件消融、双 seed QLoRA、量化对照和真实网页验收。各阶段的运行与来源索引见[实验清单](project_notes/evaluations/experiment_inventory.json)，真实 LoRA 服务及网页验收见[在线验收](project_notes/run_logs/planner_deployment_checks.json)。完整核心回归 **272 项通过**，见[运行验证](project_notes/run_logs/analysis_extensions_checks.json)。归档的复现导出副本通过 178 项测试，见[复现副本验收](project_notes/run_logs/experience_acceptance.json)。

<details>
<summary><strong>分析扩展：配对结果与复现命令</strong></summary>

[固定规划 DeepSeek 对照](project_notes/experiments/analysis-extensions-release/assessment.json)对四个问题按每组各重复两次。baseline / encoding / methods / combined 的来源与结构校验分别通过 **7/8、6/8、8/8、6/8**；加入两个隔离评审上下文对证据支持和内容完整性的共同认可后，分别为 **4/8、4/8、5/8、4/8**。上下文编码减少输入 Token **6.90%**；方法配置增加输入 Token **1.29%**。这些数值描述本批固定工作负载，编码与方法选择保留为可选项。两个评审上下文使用同一 `deepseek-flash` 模型。

[工程对照结果](project_notes/experiments/analysis-extensions-release/summary.json) 记录了配对恢复与并发负载、来源状态控制样本、上下文可逆编码和独立核算的情景计算。可通过以下命令使用已配置的模型服务运行对照，并调用结构化接口：

```powershell
conda run --no-capture-output -n finevolab python scripts/benchmark_analysis_extensions.py `
  --output output/reproduction/analysis_extensions `
  --live
conda run --no-capture-output -n finevolab python scripts/review_analysis_extensions.py `
  --run output/reproduction/analysis_extensions `
  --execute
conda run --no-capture-output -n finevolab python scripts/summarize_analysis_extensions.py `
  --run output/reproduction/analysis_extensions
```

</details>

<div id="section-reproduce"></div>

## 复现实验

代码与输入按以下目录组织：`finevolab/` 为业务模块，`scripts/` 为数据与实验入口，`configs/`、`prompts/` 为功能配置，`finevolab/templates/` 为网页与业务报告的 HTML 模板，`data/` 为来源快照与冻结数据，`tests/` 为回归与前端检查。`project_notes/` 中的 JSON / JSONL、环境快照与代表性图片提供版本化实验依据；开发说明及可选实验报告 Markdown 模板在本地集中保存，正式汇总以 JSON 结果记录。

按 development / validation / test 划分，主业务任务为 50/50/100，内部编写的双语规划任务为 600/120/240；规划集的 development 部分用于 SFT 训练。直接使用仓库内冻结数据及 manifest；以下命令从现有结果重新生成数值回放，或以新的 run ID 开展 3 轮业务对照、复核正文证据并汇总严格评分：

配对实验从 Git 历史读取原版引擎，使用保留完整提交历史的 clone。`scripts/verify_repository_distribution.py` 可核对提交范围、来源快照、冻结输入和法律文件。

<details>
<summary><strong>回归检查、预测回放与业务评测</strong></summary>

```powershell
conda run --no-capture-output -n finevolab python -m unittest discover -s tests -q
conda run --no-capture-output -n finevolab python scripts/replay_planner_predictions.py `
  --bundle data/training/planner_formal/frozen_v1 `
  --split test `
  --runs project_notes/experiments/planner-formal-deepseek-test project_notes/experiments/planner-formal-base-test project_notes/experiments/planner-formal-lora17-test `
  --case-ids data/training/planner_formal/replay_case_ids.json `
  --output output/reproduction/replay17
conda run --no-capture-output -n finevolab python scripts/run_scope_experiments.py `
  --split test `
  --paired `
  --repeats 3 `
  --run-id reproduction_scope_test
conda run --no-capture-output -n finevolab python scripts/review_scope_answers.py `
  --experiment reproduction_scope_test `
  --split test `
  --run-id reproduction_scope_review
conda run --no-capture-output -n finevolab python scripts/summarize_scope_results.py `
  --experiment reproduction_scope_test `
  --review reproduction_scope_review
```

</details>

<details>
<summary><strong>QLoRA 训练、规划器评测与推理量化</strong></summary>

业务对照使用配置的真实 API。训练与量化已在 24 GB 显存的 RTX 3090 上验证，模型 revision 由 `configs/planner_model.json` 固定；正式运行的完整环境快照见 [planner_serving_environment.txt](project_notes/run_logs/planner_serving_environment.txt)。在独立 CUDA GPU Python 环境安装依赖后执行：

```bash
python -m pip install -r requirements/planner_training.in -r requirements/planner_serving.in
python scripts/train_planner.py \
  --bundle data/training/planner_formal/frozen_v1 \
  --config configs/planner_formal_seed17.json \
  --output output/training/reproduction-seed17
python scripts/train_planner.py \
  --bundle data/training/planner_formal/frozen_v1 \
  --config configs/planner_formal_seed29.json \
  --output output/training/reproduction-seed29
python scripts/run_quantization_comparison.py \
  --bundle data/training/planner_formal/frozen_v1 \
  --adapter output/training/reproduction-seed17/checkpoint-75 \
  --mode none \
  --output output/reproduction/quant-bf16
python scripts/run_quantization_comparison.py \
  --bundle data/training/planner_formal/frozen_v1 \
  --adapter output/training/reproduction-seed17/checkpoint-75 \
  --mode nf4 \
  --output output/reproduction/quant-nf4
```

规划器同题评测入口为 `scripts/planner_benchmark.py predict`，用 `--arm deepseek --backend api`、`--arm qwen_base --backend transformers` 或 `--arm qwen_lora --backend transformers --adapter <checkpoint>` 选择实验组。正式配置为 `--repeats 1 --repairs 0 --max-new-tokens 512 --max-input-tokens 8192`，各运行使用相同 `--bundle` 和独立 `--output`。报告中的对照采用 validation 选定的共同 epoch 1，对应 checkpoint 75。

先对 DeepSeek、Qwen base 和两个 LoRA seed 执行 `--split validation`，将输出分别保存到下方目录。以 seed 17 为主 LoRA 组、seed 29 为复验组，冻结 validation 选择：

```powershell
conda run --no-capture-output -n finevolab python scripts/planner_benchmark.py `
  seal `
  --bundle data/training/planner_formal/frozen_v1 `
  --runs output/reproduction/deepseek-validation output/reproduction/base-validation output/reproduction/lora17-validation `
  --replicate-runs output/reproduction/lora29-validation `
  --output output/reproduction/selection.json
```

随后各组使用 `--split test --selection output/reproduction/selection.json`，保持与各自 validation 相同的模型 / adapter、环境和生成配置。将 test 输出保存到下方对应目录，分别比较两个 LoRA seed 与共用的 DeepSeek、Qwen base 对照组：

```powershell
conda run --no-capture-output -n finevolab python scripts/planner_benchmark.py `
  compare `
  --bundle data/training/planner_formal/frozen_v1 `
  --split test `
  --runs output/reproduction/deepseek-test output/reproduction/base-test output/reproduction/lora17-test `
  --output output/reproduction/planner_seed17.json
conda run --no-capture-output -n finevolab python scripts/planner_benchmark.py `
  compare `
  --bundle data/training/planner_formal/frozen_v1 `
  --split test `
  --runs output/reproduction/deepseek-test output/reproduction/base-test output/reproduction/lora29-test `
  --output output/reproduction/planner_seed29.json
```

各 CLI 的 `--help` 列出完整参数；原始预测与各组 manifest 保存在 `project_notes/experiments/planner-formal-*`。

LoRA 在线服务使用 `python scripts/serve_planner.py --bundle data/training/planner_formal/frozen_v1 --adapter output/training/reproduction-seed17/checkpoint-75 --port 8082`；通过 SSH 将远端 `127.0.0.1:8082` 转发至本地 `127.0.0.1:8787`，与 [planner_service.json](configs/planner_service.json) 的 loopback 地址对应。网页可选择 BF16 / NF4。

使用重新训练的 adapter 时，将服务 `GET /health` 返回的 `adapter_sha256` 填入对应的规划器配置，并通过 `FINEVOLAB_PLANNER_CONFIG` 指定该配置文件，保持客户端与实际权重身份一致。

</details>

<details>
<summary><strong>检索缓存与语义重排</strong></summary>

过滤后的 BM25 索引与查询向量使用有容量限制的内存缓存。邀请制分析工作台展示阶段、模型与工具耗时，以及 Token 用量和缓存命中情况；完成任务恢复时保留原有总耗时。共享 API 熔断器阻止连续临时故障，并在恢复窗口后进行探测。

可选 CPU Cross-Encoder 固定在 [reranker.json](configs/reranker.json)。默认 `enabled=false` 保留快速检索，并作为下方实验的对照组；实验命令显式评估语义重排，比较完成后可将 `enabled` 设为 `true`，用于常规分析。双语证据对照包含 18 对中英文查询、9 个来源主题组；业务配对验收包含 8 个请求、4 对双语任务，每组各执行一次，并由 2 个隔离上下文的同模型 AI 评审复核。下方业务验收命令使用已配置的 API。各组对照记录均附带已验证的 `source.patch`，可基于 manifest 中的提交还原运行源码。

先从已版本化的来源快照准备本地选定文档缓存，复用冻结的文档标识与财务数据：

```powershell
@'
import json
from pathlib import Path
from finevolab.scope_evaluation import task_store

root = Path.cwd()
documents = json.loads((root / "data/evaluation/scope_v3/documents.json").read_text(encoding="utf-8"))
for document in documents:
    task_store(root, {"upload_ids": [document["id"]]}, documents)
'@ | conda run --no-capture-output -n finevolab python -
```

```powershell
conda run --no-capture-output -n finevolab python scripts/benchmark_retrieval.py `
  --mode cache `
  --baseline 8a10053a `
  --output output/reproduction/retrieval_cache
conda run --no-capture-output -n finevolab python scripts/prepare_reranker.py
conda run --no-capture-output -n finevolab python scripts/benchmark_retrieval.py `
  --mode rerank `
  --split validation `
  --queries data/evaluation/retrieval_refinement/bilingual_queries.json `
  --output output/reproduction/reranker_bilingual
conda run --no-capture-output -n finevolab python scripts/benchmark_runtime_refinement.py `
  --output output/reproduction/runtime_refinement
```

</details>

<div id="section-services"></div>

## 可选检索服务与证据审查

<details>
<summary><strong>Redis 与 Milvus 配置</strong></summary>

安装可选 CPU 依赖，并将 Redis 服务端可执行文件加入 PATH。已验证服务为 Redis 8.4.0、通过 gRPC 提供服务的 Milvus Lite 3.2.2，客户端为 pymilvus 3.0.2。管理器启动本机回环服务，复用已健康运行的服务，只停止自身管理的进程。Milvus 导入已有的 13,426 × 384 contextual 向量，采用版本化 ID 和 FLAT/IP 打分。

```powershell
conda run --no-capture-output -n finevolab python -m pip install -r requirements/retrieval_services.in
conda run --no-capture-output -n finevolab python scripts/manage_retrieval_services.py start
conda run --no-capture-output -n finevolab python scripts/manage_retrieval_services.py import
conda run --no-capture-output -n finevolab python scripts/manage_retrieval_services.py status
```

在 `configs/retrieval_services.json` 中将 `redis.enabled` 设为 `true`，即可跨进程共享公开语料的片段排名。缓存 ID 从当前来源索引还原并继续校验；上传数据保持文档内 BM25 检索。将 `vector_backend` 设为 `milvus`，即可使用已导入的 collection。通过 `FINEVOLAB_REDIS_URL` 和 `FINEVOLAB_MILVUS_TOKEN` 私下提供连接凭证，通过 `FINEVOLAB_RETRIEVAL_SERVICES_CONFIG` 指定私有服务配置。默认后端为 `numpy`，Redis 默认关闭。在当前语料上，未缓存的 Milvus Lite 热查询均值为 932.85 ms，Redis 加 Milvus 为 23.59 ms。已测得的延时收益来自 Redis；Milvus 提供保持最终检索结果一致的可选存储后端。

</details>

<details>
<summary><strong>证据审查与服务实验</strong></summary>

将 `configs/agent_review.json` 的 `mode`，或 `FINEVOLAB_REVIEW_MODE`，设为 `off`、`self_check` 或 `reviewer`。审查模式检查解释题与混合题的结论，共用既有调用、Token、时间预算和一次修复额度，保留确定性数值答案。页面和报告展示审查决定。两种审查角色使用已配置模型的独立调用；Reviewer 采用独立角色和上下文。

角色对照使用 `deepseek-flash`，覆盖来自 12 道 validation 原题的 24 个双语请求，每组运行一次。三组采用相同检索配置及总预算。原分析者、自检、Reviewer 的平均工作流耗时分别为 9.02 / 11.56 / 11.76 s，模型调用为 52 / 76 / 75 次。8 道混合题的数值答案在三组间一致。本轮自检严格得分最高，作为需要审查时的优先选项；默认仍为 `off`。20 个受控预测构成来自 3 道底层任务的 10 对正负样本，接受决策同时包含结论支持与正文覆盖。Reviewer 因覆盖不完整拒绝了 2 个来源支持的正样本。逐题决定、源码补丁、配置与配对结果见[服务对照](project_notes/evaluations/retrieval_service_comparison.json)。

重建检索资源后，使用新的 run ID 复现实验。以下命令中，角色对照、结论审查对照及认证 HTTP 验收调用真实模型，服务验收及汇总不调用模型。

```powershell
conda run --no-capture-output -n finevolab python scripts/verify_retrieval_services.py `
  --output output/reproduction/service_checks.json
conda run --no-capture-output -n finevolab python scripts/build_showcase.py --output output/reproduction/service_site
conda run --no-capture-output -n finevolab python scripts/verify_service_analysis.py `
  --site output/reproduction/service_site `
  --output output/reproduction/service_analysis_checks.json
conda run --no-capture-output -n finevolab python scripts/benchmark_retrieval_services.py `
  --repeats 3 `
  --output output/reproduction/retrieval_services
conda run --no-capture-output -n finevolab python scripts/benchmark_agent_roles.py --output output/reproduction/agent_roles
conda run --no-capture-output -n finevolab python scripts/benchmark_claim_review.py --output output/reproduction/claim_review
conda run --no-capture-output -n finevolab python scripts/summarize_service_experiments.py `
  --retrieval-run output/reproduction/retrieval_services `
  --roles-run output/reproduction/agent_roles `
  --controls-run output/reproduction/claim_review `
  --output output/reproduction/service_summary.json
conda run --no-capture-output -n finevolab python scripts/manage_retrieval_services.py stop
```

不传运行目录选项时，汇总命令聚合仓库内版本化的归档运行。[服务验收](project_notes/run_logs/retrieval_services_checks.json)覆盖 135 组查询配置、向量幂等导入、空闲连接、服务故障、显式回退、缓存过期与损坏，以及服务重启恢复。

</details>

<div id="section-web"></div>

## 静态展示

[在线展示页](https://finevolab.lunelu.com/)提供中英文切换、带来源引用的财务案例及可交互的实验对比，完全由静态文件运行。使用固定财务观测和已记录的实验结果构建：

```powershell
conda run --no-capture-output -n finevolab python scripts/build_showcase.py
```

产物位于 `output/showcase/`，Pages 工作流发布该目录。源文件位于 `showcase/`；安装前端依赖后，可通过 `npm run test:showcase --prefix tests/frontend` 运行前端检查。

[项目展示首页](https://finevolab.lunelu.com/)提供真实归档混合分析回放，包括请求计划、检索片段、工具输出、校验、修复状态和实际用量。上传证据通过 SHA-256 匹配公开 SEC 快照，提供可访问的来源链接。证据浏览器支持按公司、事实所属财年、指标和原文筛选，保留报告年份、单位和实际期间。

展示首页集中呈现项目结果、归档执行回放和证据浏览，主按钮进入独立分析应用。分析应用提供邀请登录、问题输入、执行状态、带引用的结果、历史任务及 HTML/PDF 下载。旧 `experience.html` 地址跳转到展示首页的对应区域。页面路由与双语检查见[页面验收](project_notes/run_logs/site_layout_checks.json)。

<details>
<summary><strong>邀请制分析应用</strong></summary>

使用已有模型配置，在本地启动邀请制真实分析：

```powershell
conda run --no-capture-output -n finevolab python scripts/manage_experience.py invite --user owner
conda run --no-capture-output -n finevolab python scripts/manage_experience.py serve
```

打开 `http://127.0.0.1:8085/`，使用保存在 `output/experience/invite-*.txt` 的邀请口令。服务读取已有 `FINEVOLAB_MODEL_CONFIG` 设置。邀请摘要与策略保存在 `output/experience/access.json`，会话与任务归属保存在 `output/experience/access.sqlite3`，均留在本地。独立应用按账户隔离任务与报告下载，执行原子化的个人及全站每日任务额度、并发限制、幂等提交和单任务 `RunBudget`。每日额度按 UTC 计算，失败与中断的执行也计入额度；重启后任务标记为中断。默认每人每天 5 个任务，全站每天 20 个任务，同时运行 1 个；单任务最多 6 次模型调用、10 次工具调用、20,000 个 Token 和 240 秒。启动服务前配置邀请与额度。浏览器会话有效期为 12 小时。服务默认仅监听本机。配置 `--public-url` 后，公开主机名要求 HTTPS，并使用安全会话 Cookie；转发头仅信任本机连接器。公开 Pages 工作台的回放与证据浏览不发起模型请求。

</details>

<details>
<summary><strong>Cloudflare Tunnel 部署</strong></summary>

通过 Cloudflare Tunnel 公开同一个邀请制应用时，配置 `configs/tunnel.json`，将连接器令牌本身保存到 `output/tunnel/token.txt`。安装器校验固定发行版的 SHA-256。在一个终端中启动后端：

```powershell
conda run --no-capture-output -n finevolab python scripts/manage_tunnel.py install
conda run --no-capture-output -n finevolab python scripts/manage_experience.py serve --public-url https://app.lunelu.com
```

在另一个终端中启动隐藏运行的连接器并检查就绪状态：

```powershell
conda run --no-capture-output -n finevolab python scripts/manage_tunnel.py start
conda run --no-capture-output -n finevolab python scripts/manage_tunnel.py status
conda run --no-capture-output -n finevolab python scripts/manage_tunnel.py verify
```

在 Cloudflare 隧道配置中添加已发布应用路由：主机名 `app.lunelu.com`，服务 `http://127.0.0.1:8085`，路径过滤留空。[真实分析工作台](https://app.lunelu.com/)使用这条路由。本地后端与连接器均需保持运行。`verify` 检查 HTTPS 路由、真实工作台和匿名任务访问拦截，不调用模型。安装连接器或验证公网需要代理时使用 `--proxy`。`stop` 仅停止受管理的连接器。另行记录浏览器验收时，可通过 `FINEVOLAB_BROWSER_REPORT` 指定独立的报告文件名。

工作台验收见[工作台检查](project_notes/run_logs/experience_acceptance.json)。实际浏览器检查使用 `FINEVOLAB_CHROMIUM` 指定已安装的 Chromium 可执行文件，在本地服务运行时执行 `node tests/frontend/check_experience_live.cjs`。将 `FINEVOLAB_INVITATION_FILE` 指向本地邀请文件，并添加 `--real`，即可执行真实模型分析，验证工具、引用、校验、报告、刷新恢复及幂等提交。驱动读取邀请口令而不输出其内容。

</details>

<div id="section-license"></div>

## 许可证

FinEvoLab 自有代码、文档与修改部分采用 **Apache-2.0**，版权归 **Zehao Feng (Zayne-Feng) and FinEvoLab contributors**，见 [LICENSE](LICENSE)、[COPYRIGHT](COPYRIGHT) 和 [NOTICE](NOTICE)。部分组件衍生自 FinRobot Equity V1，原始声明集中保存在 [licenses/finrobot/](licenses/finrobot/)。
