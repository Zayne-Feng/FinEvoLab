<div align="center">

# FinEvoLab

**融合 RAG、金融工具与 QLoRA 规划的可追溯财务分析。**

<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/English-64748B" alt="English"></a>
  <a href="README_CN.md"><img src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-7B6DAA" alt="简体中文"></a>
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?logo=python&amp;logoColor=white" alt="Python 3.11">
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&amp;logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/License-Apache--2.0-7B6DAA" alt="Apache-2.0">
</p>

[项目展示](https://finevolab.lunelu.com/) · [邀请制分析](https://app.lunelu.com/)

</div>

FinEvoLab 将财报转化为有据可查的回答、跨公司比较，以及增长率和利润率计算。混合 RAG 检索证据，确定性工具计算财务结果，可恢复的执行框架负责校验输出并生成带引用的 HTML/PDF 报告。系统提供 QLoRA 请求规划、双语网页和可复现评测。

[核心能力](#section-capabilities) · [快速启动](#section-quick-start) · [实验结果](#section-results) · [复现实验](#section-reproduce) · [运行服务](#section-services) · [网页部署](#section-web) · [许可证](#section-license)

---

<div id="section-capabilities"></div>

## 核心能力

| 能力 | 实现与输出 |
| --- | --- |
| **财报与证据** | SEC / 公司 IR 快照及指定上传的 HTML 或可提取文本的 PDF，支持指标抽取、期间与单位核对，以及原文定位。 |
| **RAG** | 固定语料采用 BM25、multilingual E5 与 RRF 混合检索；上传内容限定文档范围；可选 Cross-Encoder 重排、Redis 缓存和 Milvus 向量存储。 |
| **Agent 与工具** | 通过 Agents SDK 规划请求并执行指标、增长率、利润率、跨公司比较和报告工具；财务数值与公式基于有来源的观测计算。 |
| **Harness** | schema、来源与任务状态校验，最多一次定向修复，带版本签名的检查点与恢复；支持任务预算内的自检或隔离证据审查。 |
| **MCP 与可观测性** | 只读金融 MCP Server / Client、严格 schema 与访问范围、关联 Trace，以及可重复的延迟、负载和恢复检查。 |
| **记忆与任务** | 用户隔离的上下文和展示偏好、事务化任务接入、持久 worker、任务取消及检查点恢复。 |
| **模型与界面** | DeepSeek API 与 Qwen3-4B-Instruct-2507 LoRA BF16 / NF4 规划器、FastAPI 接口、双语界面及 HTML / PDF 报告。 |

```text
Question → RequestPlan → Evidence retrieval & financial tools → Validation → Report
```

默认 API 后端使用 CPU 检索。可选 LoRA 服务运行于外部 GPU，通过 SSH 端口转发接入：LoRA 生成 `RequestPlan`，API 模型生成解释性正文。两条路径共用财务工具、RAG 和 harness。

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

财务工具使用固定财报快照。自然语言分析使用本地 `finrobot_equity/core/config/config.ini` 中配置的模型服务，也可通过 `FINEVOLAB_MODEL_CONFIG` 指定私有配置。最小配置如下，在本机填写有效 key 和模型名称；DeepSeek 请求显式关闭 Thinking。

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

打开 **http://127.0.0.1:8003/finevolab**，可尝试以下问题：

- **跨公司比较：** 比较 Apple 与 Microsoft 2023 财年的收入，并说明财年期间差异。
- **带证据的解释：** 计算 Microsoft 2023 财年收入同比，并引用财报解释变化。

依赖锁定位于 [environments/](environments/) 和 [requirements/](requirements/)，检索模型与 revision 固定在 [retrieval.json](configs/retrieval.json)。

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
| 请求规划器正式测试 | Qwen base 109/240；LoRA seed 17 为 212/240、seed 29 为 213/240；DeepSeek 227/240 | [QLoRA 对照](project_notes/evaluations/planner_formal_results.json) |
| 同权重规划器 BF16 / NF4 推理 | 严格成功 212/240、211/240；NF4 峰值已分配显存降低 62.58%，平均推理耗时增加 47.18% | [量化结果](project_notes/evaluations/planner_quantization.json) |
| 检索缓存对照 | 135 组查询配置 × 5 次重复；返回结果一致；预热均值 68.52 → 6.16 ms，11.12× | [缓存对照](project_notes/experiments/retrieval-cache-v1/summary.json) |
| Redis 共享检索缓存 | 四组对照，77 题 × 3 次；693 次配对的片段完全一致；热查询均值 91.13 → 22.44 ms，4.06× | [服务对照](project_notes/evaluations/retrieval_service_comparison.json) |
| 同预算正文审查 | 原分析者 22/24；自检 24/24；隔离 Reviewer 23/24；72 次真实工作流，经两个隔离上下文盲评 | [角色对照](project_notes/experiments/agent-roles-v1/summary.json) |
| 无依据结论受控测试 | 接受／拒绝决策正确数：来源校验 10/20，自检 20/20，Reviewer 18/20；两种模型审查均拒绝全部 10 个无依据对照 | [支持关系对照](project_notes/experiments/claim-review-v3/summary.json) |
| 可选语义重排 | top 5 完整标准原文命中 15/36 → 23/36；业务配对严格成功均为 8/8 | [证据对照](project_notes/experiments/reranker-bilingual-v1/summary.json)、[业务验收](project_notes/experiments/runtime-refinement-v1/summary.json) |

业务严格分按题型核对数值、期间、来源与任务合同，并检查正文结论的证据支持；规划器严格分核对 `RequestPlan` schema 与相关请求语义。配对量化实验中，BF16 / NF4 的峰值已分配显存为 7.995 / 2.992 GiB，平均单次推理耗时为 3.759 / 5.532 s，计时包含输入编码、生成与解码。

项目已完成业务 Agent 评测、组件消融、双 seed QLoRA、量化对照和真实网页验收。各阶段的运行与来源索引见[实验清单](project_notes/evaluations/experiment_inventory.json)，真实 LoRA 服务及网页验收见[在线验收](project_notes/run_logs/planner_deployment_checks.json)。归档核心回归 **311 项通过**，见[运行验证](project_notes/evaluations/worker_service_acceptance.json)。归档的复现导出副本通过 178 项测试，见[复现副本验收](project_notes/run_logs/experience_acceptance.json)。

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

仓库按应用代码、复现输入与实验依据划分职责：

| 目录 | 内容 |
| --- | --- |
| `finevolab/` | 分析流程、财务工具、检索、运行服务及 API 模块。 |
| `finevolab/templates/` | 应用页面与 HTML 报告模板。 |
| `scripts/` | 数据准备、训练、评测、验证和服务管理命令。 |
| `configs/`、`prompts/` | 功能配置、模型身份与版本化提示词。 |
| `data/` | 来源快照、处理后的财务观测、索引元数据与冻结数据集。 |
| `tests/` | 财务、工作流、服务及前端回归检查。 |
| `finrobot_equity/` | 复用基础模块与固定版本对照基线。 |
| `project_notes/` | 版本化 JSON / JSONL 结果、环境快照与代表性图片；说明性 Markdown 和可选报告模板保存在本地。 |

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

## 运行服务与可选后端

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

<details>
<summary><strong>只读 MCP 工具与关联执行 Trace</strong></summary>

本地 MCP Server 提供指标查询、同比及利润率计算、公司比较和固定语料的财报检索。业务工作流通过 `tool_backend` 选择 `direct` 或 `mcp`，保留相同的规划与校验合同，并验证远程结果的快照身份。服务端执行 [mcp.json](configs/mcp.json) 中的工具与公司白名单、严格请求 schema 和只读工具标注，使用本地 stdio 传输。SDK Client 支持工具发现与结构化结果校验：

```powershell
conda run --no-capture-output -n finevolab python scripts/call_mcp.py
conda run --no-capture-output -n finevolab python scripts/call_mcp.py `
  --tool get_financial_metrics `
  --company MSFT --metric revenue --fiscal-year 2024
```

接入 MCP host 时，使用已安装环境的 Python 解释器启动 `scripts/serve_mcp.py`。检索范围限定为公开固定索引，使用 `report_year`；指标与计算工具使用 `fiscal_year`。复杂请求可以通过 `--request-file` 提供。返回的来源对象保留实际财务日期与披露年份。

HTTP 响应传递 `traceparent`；工作流阶段、模型调用、确定性工具和 MCP 调用共享可关联的 Trace 与父 span 标识。Trace 文件记录相对耗时、状态、尝试次数、缓存命中及已观测 Token。`FINEVOLAB_TRACE_MODE` 支持 `off`、`memory`、`file`、`batch`，托管的邀请服务默认使用 `batch`。后台导出提供有界队列、背压、失败计数及关闭时的显式刷新。本地导出位于 `output/traces/`、`output/mcp/` 和任务 Trace 目录。完整 HTTP 工作流测试隔离模型响应，实际执行 SDK 规划、金融工具、校验及报告生成。两种工具后端使用同一 HTTP 端点：

```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8003/api/questions' -ContentType 'application/json' -Body '{"question":"Calculate Microsoft revenue growth for fiscal year 2024.","tool_backend":"mcp"}'
```

```powershell
conda run --no-capture-output -n finevolab python -m unittest tests.test_mcp tests.test_tracing -v
conda run --no-capture-output -n finevolab python scripts/benchmark_services.py `
  --run-id mcp-trace-reproduction `
  --output output/reproduction/mcp_trace `
  --requests 40 --repeats 3 --concurrency 1 4 8
```

该实验比较等价的 direct 与 MCP 调用，并测量回环 TCP HTTP 请求、严格参数及访问范围拒绝、注入超时与连接故障后的恢复。结果记录逐请求输出、nearest-rank p50 / p95、吞吐、失败率、超时数、源码校验值和数据身份。真实模型烟测通过 `--real-model` 单独选择。

配对工作流使用 10 个固定数值计划或 12 个真实模型回归问题，两种工具后端保持相同问题、模型、提示词、预算及三轮重复。真实模型请求经过 TCP HTTP 并生成经过校验的报告。Trace 实验分别测量响应延迟和客户端、服务端导出排空所需时间：

```powershell
conda run --no-capture-output -n finevolab python scripts/benchmark_tool_backends.py `
  --mode fixed --repeats 3 --trace-mode batch `
  --output output/reproduction/mcp_fixed
conda run --no-capture-output -n finevolab python scripts/benchmark_tool_backends.py `
  --mode real --repeats 3 --trace-mode batch `
  --output output/reproduction/mcp_workflow
conda run --no-capture-output -n finevolab python scripts/benchmark_trace_modes.py `
  --modes off memory file batch --requests 40 --repeats 3 --concurrency 1 4 8 `
  --output output/reproduction/trace_export
```

正式本地验收通过 **1080/1080 次请求**、**15/15 项权限检查**、**1080 条关联 Trace / 2880 个 span**，以及 **10/10 次注入故障恢复**。正常负载的失败与超时数均为零。这些测量覆盖固定工具及服务，业务题准确率保持原评测口径。逐请求记录、p50 / p95、吞吐、源码身份和代表性 Trace 见 [MCP 与 Trace 验收](project_notes/evaluations/mcp_trace_acceptance.json)。

四种 Trace 模式的受控测量通过 **2880/2880 次请求**。并发 4 时，后台导出使 MCP 响应 p95 从 **2627.01 降至 80.79 ms**，降低 **96.92%**；包含导出排空的吞吐从 **2.50 提高到 4.68 次请求/s**。文件导出组保留 **1440 条完整 Trace / 4320 个 span**。详见 [Trace 导出对照](project_notes/evaluations/trace_export_comparison.json)。

关闭工具结果缓存的业务对照中，两种后端各通过 **36/36 次任务**，使用 **12 个开发回归问题**、各重复 **3 轮**。MCP 组实际执行 **42 次远程金融工具调用**；两组均完成来源校验及 HTML/PDF 报告。固定计划组在不请求模型的情况下通过 **60/60 次任务**。该组测量规划、数值与来源检查，以及正文完成度。工作流延时包含进程初始化；`direct` 继续作为默认后端。缓存开启组独立保存在同一份[配对工作流证据](project_notes/evaluations/mcp_workflow_comparison.json)中。

</details>

<details>
<summary><strong>业务记忆与持久任务 worker</strong></summary>

邀请工作台支持用户隔离的会话上下文，以及明确保存的语言、金额展示单位偏好。可勾选上下文继承、新建会话或清除业务记忆。相对财年在规划前解析；记忆保留已校验的请求字段和任务引用，财务金额仍通过工具获取。展示缩放保留标准财务对象。提交任务时，会话快照按 24 小时有效期及来源、配置签名检查。已提交任务保留冻结的上下文；清除记忆会阻止这些任务重新写回上下文。

默认执行模式为 `inline`。首次使用时创建邀请及私有访问配置；后续启动复用已有邀请：

```powershell
conda run --no-capture-output -n finevolab python scripts/manage_experience.py invite --user owner
```

选择 `worker` 执行模式并启动 Web 进程。该模式在提交任务时，在现有本地 SQLite 数据库中原子完成配额预留与任务入队：

```powershell
conda run --no-capture-output -n finevolab python scripts/manage_experience.py configure --execution-mode worker
conda run --no-capture-output -n finevolab python scripts/manage_experience.py serve
```

在另一个终端使用相同环境和私有访问配置启动独立 worker：

```powershell
conda run --no-capture-output -n finevolab python scripts/manage_experience.py worker
```

任务提供排队、执行、完成、失败和取消状态，以及排队耗时和执行次数。租约、心跳、执行代次及任务锁协调恢复与结果发布。已有阶段检查点和累计用量继续复用，未知模型调用用量保持明确状态。worker 在 CPU 上衔接现有 API 模型、direct/MCP 工具、来源校验和报告。

受控上下文解析检查中，关闭记忆为 30/90，开启后为 90/90：30 个场景，各重复 3 次。40 项故障检查全部通过。每组 10 次进程异常退出场景中，进程内调度自动完成 0/10，worker 为 10/10；两组使用已有检查点最终均完成 10/10，其中进程内组需要手动恢复。6 个真实模型 worker 烟测任务均通过校验并生成 PDF；上下文正确数为关闭记忆 2/3、开启记忆 3/3。该烟测验证集成链路。输入及逐例结果见[记忆与 worker 验收](project_notes/evaluations/memory_worker_acceptance.json)。

```powershell
conda run --no-capture-output -n finevolab python scripts/benchmark_memory_worker.py --output output/reproduction/memory_worker
conda run --no-capture-output -n finevolab python scripts/verify_memory_worker_live.py --output output/reproduction/memory_worker_live
```

日常服务使用独立 Web 与 worker 进程。HTTP 接入将同步配额和入队事务移出事件循环。明确的利润率请求使用 `AnalysisRequestPlan` 和已有审计算子，冻结的 `RequestPlan` 训练合同保持不变。

TCP HTTP 混合负载采用相同的 12 个任务，分别使用 1 和 2 个 worker，各重复 3 次。72 个任务的状态均符合合同：60 个完成，6 个返回注入错误，6 个触发注入超时。额外的 6 次排队取消和 6 次 worker 异常退出及 Web 重启恢复全部通过。两组均保留持久检查点，并使用日常的 30 秒租约、0.5 秒空闲轮询和 batch Trace。

| Worker 数 | 预期结果符合数 | 入队至发布 p50（秒） | p95（秒） |
| --- | --- | --- | --- |
| 1 | 36/36 | 10.591 | 44.713 |
| 2 | 36/36 | 8.396 | 48.500 |

本机测量包含 worker 冷启动与持久写盘。两个 worker 降低了中位延迟，但未稳定改善尾延迟，因此日常配置保留 1 个 worker。真实服务检查通过 5 个业务任务的来源校验及 HTML/PDF 生成，并通过 1 个利润率任务验证异步入队。真实浏览器通过 17 项检查，覆盖 worker 控件、历史、上下文恢复、报告和双语布局。逐例结果、重复测量与全部开发模型用量见[worker 服务验收](project_notes/evaluations/worker_service_acceptance.json)。

```powershell
conda run --no-capture-output -n finevolab python scripts/benchmark_worker_load.py --output output/reproduction/worker_load
$invitationFile = Read-Host 'Invitation file path'
conda run --no-capture-output -n finevolab python scripts/verify_worker_service.py --invitation-file $invitationFile --output output/reproduction/worker_service
```

负载命令使用 CPU fixture，不调用模型。真实服务验收需要 Web 与 worker 保持运行，并输入创建对应用户时输出的邀请文件路径；沿用现有额度，需要剩余 5 个任务。这些任务通过后，使用同一邀请进行浏览器检查。按网页章节安装前端依赖，将 `FINEVOLAB_CHROMIUM` 指向已安装的 Chromium 可执行文件、`FINEVOLAB_INVITATION_FILE` 指向该邀请文件，再运行 `npm run test:worker:live --prefix tests/frontend`。浏览器检查已完成的历史任务，不新增模型调用。

</details>

<div id="section-web"></div>

## 网页界面与部署

[在线展示页](https://finevolab.lunelu.com/)提供中英文切换、带来源引用的财务案例及可交互的实验对比，完全由静态文件运行。使用固定财务观测和已记录的实验结果构建：

```powershell
conda run --no-capture-output -n finevolab python scripts/build_showcase.py
```

产物位于 `output/showcase/`，Pages 工作流发布该目录；源文件位于 `showcase/`。前端检查使用 Node.js 24，安装依赖并运行交互检查：

```powershell
npm ci --prefix tests/frontend
npm run test:showcase --prefix tests/frontend
```

[项目展示首页](https://finevolab.lunelu.com/)提供真实归档混合分析回放，包括请求计划、检索片段、工具输出、校验、修复状态和实际用量。上传证据通过 SHA-256 匹配公开 SEC 快照，提供可访问的来源链接。证据浏览器支持按公司、事实所属财年、指标和原文筛选，保留报告年份、单位和实际期间。

展示首页集中呈现项目结果、归档执行回放和证据浏览，主按钮进入独立分析应用。分析应用提供邀请登录、问题输入、执行状态、带引用的结果、历史任务及 HTML/PDF 下载。旧 `experience.html` 地址跳转到展示首页的对应区域。页面路由与双语检查见[页面验收](project_notes/run_logs/site_layout_checks.json)。

<details>
<summary><strong>邀请制分析应用</strong></summary>

使用已有模型配置，在本地启动邀请制真实分析。仅首次使用时创建邀请，后续复用；采用 `worker` 模式时，还需按运行服务章节启动独立 worker：

```powershell
conda run --no-capture-output -n finevolab python scripts/manage_experience.py invite --user owner
conda run --no-capture-output -n finevolab python scripts/manage_experience.py serve
```

打开 `http://127.0.0.1:8085/`，使用保存在 `output/experience/invite-*.txt` 的邀请口令。服务读取已有 `FINEVOLAB_MODEL_CONFIG` 设置。邀请摘要与策略保存在 `output/experience/access.json`，会话与任务归属保存在 `output/experience/access.sqlite3`，均留在本地。独立应用按账户隔离任务与报告下载，执行原子化的个人及全站每日任务额度、并发限制、幂等提交和单任务 `RunBudget`。每日额度按 UTC 计算，失败与中断的执行也计入额度；重启后，`inline` 模式的在途任务标记为中断；`worker` 模式保留排队任务与可恢复执行。默认每人每天 5 个任务，全站每天 20 个任务，同时运行 1 个；单任务最多 6 次模型调用、10 次工具调用、20,000 个 Token 和 240 秒。启动服务前配置邀请与额度。浏览器会话有效期为 12 小时。服务默认仅监听本机。配置 `--public-url` 后，公开主机名要求 HTTPS，并使用安全会话 Cookie；转发头仅信任本机连接器。公开 Pages 工作台的回放与证据浏览不发起模型请求。

</details>

<details>
<summary><strong>Cloudflare Tunnel 部署</strong></summary>

通过 Cloudflare Tunnel 公开同一个邀请制应用时，配置 `configs/tunnel.json`，将连接器令牌本身保存到 `output/tunnel/token.txt`。安装器校验固定发行版的 SHA-256。复用上文创建的邀请配置。选择 `worker` 模式，并在一个终端中启动后端：

```powershell
conda run --no-capture-output -n finevolab python scripts/manage_tunnel.py install
conda run --no-capture-output -n finevolab python scripts/manage_experience.py configure --execution-mode worker
conda run --no-capture-output -n finevolab python scripts/manage_experience.py serve --public-url https://app.lunelu.com
```

在第二个终端中，使用同一私有访问配置启动独立 worker：

```powershell
conda run --no-capture-output -n finevolab python scripts/manage_experience.py worker
```

在第三个终端中启动隐藏运行的连接器并检查就绪状态：

```powershell
conda run --no-capture-output -n finevolab python scripts/manage_tunnel.py start
conda run --no-capture-output -n finevolab python scripts/manage_tunnel.py status
conda run --no-capture-output -n finevolab python scripts/manage_tunnel.py verify
```

在 Cloudflare 隧道配置中添加已发布应用路由：主机名 `app.lunelu.com`，服务 `http://127.0.0.1:8085`，路径过滤留空。[真实分析工作台](https://app.lunelu.com/)使用这条路由。采用 `worker` 模式时，本地后端、worker 与连接器均需保持运行。`verify` 检查 HTTPS 路由、真实工作台和匿名任务访问拦截，不调用模型。安装连接器或验证公网需要代理时使用 `--proxy`。`stop` 仅停止受管理的连接器。另行记录浏览器验收时，可通过 `FINEVOLAB_BROWSER_REPORT` 指定独立的报告文件名。

工作台验收见[工作台检查](project_notes/run_logs/experience_acceptance.json)。实际浏览器检查使用 `FINEVOLAB_CHROMIUM` 指定已安装的 Chromium 可执行文件，在本地服务运行时执行 `node tests/frontend/check_experience_live.cjs`。将 `FINEVOLAB_INVITATION_FILE` 指向本地邀请文件，并添加 `--real`，即可执行真实模型分析，验证工具、引用、校验、报告、刷新恢复及幂等提交。驱动读取邀请口令而不输出其内容。

</details>

<div id="section-license"></div>

## 许可证

FinEvoLab 自有代码、文档与修改部分采用 **Apache-2.0**，版权归 **Zehao Feng (Zayne-Feng) and FinEvoLab contributors**，见 [LICENSE](LICENSE)、[COPYRIGHT](COPYRIGHT) 和 [NOTICE](NOTICE)。部分组件衍生自 FinRobot Equity V1，原始声明集中保存在 [licenses/finrobot/](licenses/finrobot/)。
