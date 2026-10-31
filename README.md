<div align="center">

# FinEvoLab

**Evidence-backed financial analysis with RAG, tools, and QLoRA planning.**

<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/English-64748B" alt="English"></a>
  <a href="README_CN.md"><img src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-7B6DAA" alt="简体中文"></a>
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?logo=python&amp;logoColor=white" alt="Python 3.11">
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&amp;logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/License-Apache--2.0-7B6DAA" alt="Apache-2.0">
</p>

[Project showcase](https://finevolab.lunelu.com/) · [Invited analysis](https://app.lunelu.com/)

</div>

FinEvoLab answers questions about financial reports, compares companies, calculates growth and profit margins, and generates reports with traceable citations. It combines hybrid RAG, deterministic financial tools, a resumable execution harness, and QLoRA request planning, with a bilingual web interface and reproducible evaluations.

[Capabilities](#section-capabilities) · [Quick start](#section-quick-start) · [Results](#section-results) · [Reproduce](#section-reproduce) · [Optional services](#section-services) · [Web deployment](#section-web) · [License](#section-license)

---

<div id="section-capabilities"></div>

## Features

| Capability | Implementation and output |
| --- | --- |
| **Reports and evidence** | SEC / company IR snapshots, financial metric extraction, period and unit checks, and citations that locate the original sources; supports selected HTML files and PDFs with extractable text. |
| **RAG** | BM25, multilingual E5, and RRF hybrid retrieval over the fixed corpus; retrieval within selected uploaded documents; combined numerical and narrative analysis; optional shared Redis caching and a versioned Milvus vector backend. |
| **Agent and tools** | Agents SDK interprets requests and invokes metric lookup, growth calculation, company comparison, and report tools. Programs compute amounts and formulas and assemble financial objects from actual tool results. |
| **Harness** | Schema, source, and task-status validation, at most one targeted repair, version-bound stage checkpoints, execution recovery, and optional self-check or isolated evidence-review roles within the same task budget. |
| **MCP and observability** | Read-only financial MCP Server / Client, strict schemas and server-side access scopes, correlated execution traces, and repeatable load / recovery measurements. |
| **Models and interface** | DeepSeek API, Qwen3-4B-Instruct-2507 LoRA BF16 / NF4 planning backends, FastAPI endpoints, a bilingual web interface, and HTML / PDF reports. |

```text
Question → RequestPlan → Evidence retrieval & financial tools → Validation → Report
```

The default API mode runs with CPU retrieval. The LoRA service runs on an external GPU and connects through SSH port forwarding. LoRA handles `RequestPlan` planning, while the API model generates explanatory narratives. All backends share the financial tools, RAG, and harness.

FinEvoLab's core workflows are implemented in `finevolab/`, with data preparation, training, and experiments in the supporting directories. Reused infrastructure and the pinned comparison baseline are kept in `finrobot_equity/`.

<div id="section-quick-start"></div>

## Quick start

Use a terminal where `conda` is available. The commands below use PowerShell and start from a full clone of the repository. An existing `finevolab` environment can be reused; skip its environment creation and dependency installation commands.

### 1. Create the environment

```powershell
git clone https://github.com/Zayne-Feng/FinEvoLab.git
cd FinEvoLab

conda env create -f environments/development.yml
conda run -n finevolab python -m pip install -r requirements/development.lock.txt
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
```

### 2. Build retrieval resources

Build the retrieval resources from the repository root on first use; reuse existing matching resources. The encoding scripts download the pinned Embedding model revision and generate vectors and caches locally.

```powershell
conda run --no-capture-output -n finevolab python scripts/build_index.py
conda run --no-capture-output -n finevolab python scripts/build_vectors.py
conda run --no-capture-output -n finevolab python scripts/build_contextual_index.py
```

### 3. Configure the model provider

The financial tools use pinned report snapshots. Natural-language analysis uses the provider configured in the local `finrobot_equity/core/config/config.ini`, or a private configuration specified through `FINEVOLAB_MODEL_CONFIG`. Fill in a valid key and model name in the minimal configuration below. DeepSeek requests explicitly disable Thinking.

```ini
[API_KEYS]
openai_api_key = YOUR_API_KEY
openai_model = YOUR_MODEL_NAME
openai_base_url = https://api.deepseek.com/
```

### 4. Start the application

```powershell
conda run --no-capture-output -n finevolab python -m uvicorn finevolab.integrated_web:app --host 127.0.0.1 --port 8003
```

Open **http://127.0.0.1:8003/finevolab**. Try “Compare Apple and Microsoft revenue for fiscal year 2023 and explain the fiscal-period differences,” or “Calculate Microsoft year-over-year revenue growth for fiscal year 2023 and cite the report to explain the change.” Dependency locks are in [environments/](environments/) and [requirements/](requirements/). The retrieval model and revision are pinned in [retrieval.json](configs/retrieval.json).

<details>
<summary><strong>Financial APIs, audited margins, and explicit scenarios</strong></summary>

The deterministic calculation tool also supports `operating_margin`, `net_margin`, `operating_margin_change`, and `net_margin_change`. Margins use percent; changes use percentage points. Each result retains the source inputs, actual fiscal periods, disclosure years, formula, citations, data signature, and field-level audit checks. Each input uses its latest available disclosure. The local `POST /api/derived-metrics` endpoint and the following command execute these calculations without model calls:

```powershell
conda run --no-capture-output -n finevolab python scripts/calculate_derived.py `
  --company MSFT `
  --fiscal-year 2024 `
  --metric operating_margin_change `
  --report-dir output/derived_report
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8003/api/derived-metrics' -ContentType 'application/json' -Body '{"company":"MSFT","fiscal_year":2024,"metric":"net_margin"}'
```

Analysis controls include validated company-level model-step checkpoints with cumulative usage and repair accounting, source reconciliation by fiscal scope and filing lineage, and in-process singleflight for overlapping deterministic cache misses. Versioned task methods and lossless financial-context encoding are configurable in `configs/analysis_extensions.json` and `configs/analysis_methods.json`. The local `POST /api/scenarios` endpoint produces sensitivity grids from explicit revenue and margin assumptions, retaining the observed baseline and its audit.

```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8003/api/source-reconciliation' -ContentType 'application/json' -Body '{"company":"MSFT","fiscal_year":2024,"metric":"revenue"}'
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8003/api/scenarios' -ContentType 'application/json' -Body '{"company":"MSFT","fiscal_year":2024,"margin":"operating_margin","revenue_changes_percent":[-10,0,10],"margin_changes_percentage_points":[-2,0,2]}'
```

</details>

<div id="section-results"></div>

## Results

| Evaluation scope | Strict successes and resource metrics | Evidence |
| --- | --- | --- |
| Common supported business-Agent tasks | Earlier FinEvoLab engine 179/195 → improved engine 195/195; 65 questions, 3 repetitions each | [Business evaluation](project_notes/experiments/scope-test-v1/assessment.json) |
| Expanded business-Agent main test | 291/300, 97.00%; 100 questions, 3 repetitions each | [Full results](project_notes/experiments/scope-test-v1/assessment.json) |
| Request-planner formal test | Qwen base 109/240; LoRA seed 17: 212/240, seed 29: 213/240; DeepSeek 227/240 | [QLoRA comparison](project_notes/evaluations/planner_formal_results.json) |
| Same-weight planner BF16 / NF4 inference | Strict successes: 212/240 and 211/240; NF4 reduced peak allocated memory by 62.58% and increased mean inference time by 47.18% | [Quantization results](project_notes/evaluations/planner_quantization.json) |
| Retrieval cache comparison | 135 query configurations × 5 repetitions; identical results; warm mean 68.52 → 6.16 ms, 11.12× | [Cache comparison](project_notes/experiments/retrieval-cache-v1/summary.json) |
| Shared Redis retrieval cache | Four arms, 77 questions × 3 repetitions; 693 paired comparisons with identical passages; warm mean 91.13 → 22.44 ms, 4.06× | [Service comparison](project_notes/evaluations/retrieval_service_comparison.json) |
| Same-budget narrative review | Analyst 22/24; self-check 24/24; isolated Reviewer 23/24; 72 real workflows with two blind scoring contexts | [Role comparison](project_notes/experiments/agent-roles-v1/summary.json) |
| Controlled unsupported claims | Correct accept/reject decisions: source validation 10/20, self-check 20/20, Reviewer 18/20; both model review modes rejected all 10 unsupported controls | [Support controls](project_notes/experiments/claim-review-v3/summary.json) |
| Optional semantic reranking | Complete gold-quote hits in top 5: 15/36 → 23/36; paired workflow strict successes 8/8 for both arms | [Evidence comparison](project_notes/experiments/reranker-bilingual-v1/summary.json), [Workflow verification](project_notes/experiments/runtime-refinement-v1/summary.json) |

Business strict scores combine the numerical, period, source, and task-contract checks applicable to each question with evidence support for narrative claims. Planner strict scores check the `RequestPlan` schema and relevant request semantics. In the paired quantization run, BF16 / NF4 peak allocated memory was 7.995 / 2.992 GiB and mean per-request inference time was 3.759 / 5.532 s, including input encoding, generation, and decoding.

The project has completed business-Agent evaluation, component ablations, QLoRA with two seeds, quantization comparisons, and live web acceptance. Run and source references are listed in the [experiment inventory](project_notes/evaluations/experiment_inventory.json); live LoRA service and web checks are recorded in [service acceptance](project_notes/run_logs/planner_deployment_checks.json). The complete core regression passed **296 tests**; see [runtime verification](project_notes/evaluations/trace_export_comparison.json). The archived reproduction export passed 178 tests; see [reproduction-copy acceptance](project_notes/run_logs/experience_acceptance.json).

<details>
<summary><strong>Analysis extensions: paired results and reproduction</strong></summary>

The [fixed-plan DeepSeek comparison](project_notes/experiments/analysis-extensions-release/assessment.json) evaluates four questions twice per arm. Baseline / encoding / methods / combined passed source-and-schema checks on **7/8, 6/8, 8/8, 6/8** tasks; adding support and completeness agreement from two isolated review contexts gave **4/8, 4/8, 5/8, 4/8**. Encoding reduced input tokens by **6.90%**; methods increased them by **1.29%**. These are descriptive results for this fixed workload. Encoding and method selection remain opt-in. The two review contexts use the same `deepseek-flash` model.

The [engineering comparison](project_notes/experiments/analysis-extensions-release/summary.json) records paired recovery and concurrency workloads, source-status controls, reversible context encoding, and independently checked scenario arithmetic. Run the model comparison with the configured provider and call the structured endpoints with:

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

## Reproducing experiments

Code and inputs are organized as follows: `finevolab/` contains business modules, `scripts/` contains data and experiment entry points, `configs/` and `prompts/` contain functional configuration, `finevolab/templates/` contains HTML templates for the web interface and business reports, `data/` contains source snapshots and frozen datasets, and `tests/` contains regression and frontend checks. JSON / JSONL files, environment snapshots, and representative images under `project_notes/` provide versioned experimental evidence. Development notes and optional Markdown report templates remain local; formal summaries are recorded as JSON results.

The development / validation / test splits contain 50/50/100 business tasks and 600/120/240 internally authored bilingual planner tasks. The planner development split supplies the SFT training examples. Use the frozen datasets and manifests in the repository. The following commands replay numerical results from archived predictions or run a business comparison with 3 repetitions under a new run ID, review narrative evidence, and aggregate strict scores:

Paired experiments load the original engine from Git history, so use a clone with the complete commit history. `scripts/verify_repository_distribution.py` checks the submission scope, source snapshots, frozen inputs, and legal files.

<details>
<summary><strong>Regression checks, prediction replay, and business evaluation</strong></summary>

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
<summary><strong>QLoRA training, planner evaluation, and inference quantization</strong></summary>

Business comparisons use the configured live API. Training and quantization were verified on an RTX 3090 with 24 GB VRAM, with the model revision pinned in `configs/planner_model.json`. The complete environment snapshot from the formal run is recorded in [planner_serving_environment.txt](project_notes/run_logs/planner_serving_environment.txt). Install dependencies in a separate CUDA GPU Python environment and run:

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

For planner evaluation on the same questions, use `scripts/planner_benchmark.py predict`. Select the arm with `--arm deepseek --backend api`, `--arm qwen_base --backend transformers`, or `--arm qwen_lora --backend transformers --adapter <checkpoint>`. The formal settings are `--repeats 1 --repairs 0 --max-new-tokens 512 --max-input-tokens 8192`, with the same `--bundle` and a separate `--output` for each run. The reported comparison uses the validation-selected common epoch 1, corresponding to checkpoint 75.

First run `--split validation` for DeepSeek, Qwen base, and both LoRA seeds, saving their outputs in the directories below. Freeze the validation selection with seed 17 as the primary LoRA arm and seed 29 as its replicate:

```powershell
conda run --no-capture-output -n finevolab python scripts/planner_benchmark.py `
  seal `
  --bundle data/training/planner_formal/frozen_v1 `
  --runs output/reproduction/deepseek-validation output/reproduction/base-validation output/reproduction/lora17-validation `
  --replicate-runs output/reproduction/lora29-validation `
  --output output/reproduction/selection.json
```

Then run each arm with `--split test --selection output/reproduction/selection.json`, retaining its validation model / adapter, environment, and generation settings. Save the test outputs under the matching directories below and compare each LoRA seed against the shared DeepSeek and Qwen base controls:

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

Each CLI lists its complete arguments through `--help`. Original predictions and per-arm manifests are stored under `project_notes/experiments/planner-formal-*`.

Start the LoRA service with `python scripts/serve_planner.py --bundle data/training/planner_formal/frozen_v1 --adapter output/training/reproduction-seed17/checkpoint-75 --port 8082`. Use SSH to forward remote `127.0.0.1:8082` to local `127.0.0.1:8787`, matching the loopback address in [planner_service.json](configs/planner_service.json). The web interface supports BF16 / NF4 selection.

When using a retrained adapter, copy the `adapter_sha256` returned by the service's `GET /health` into the corresponding planner configuration and select that file through `FINEVOLAB_PLANNER_CONFIG`, keeping the client identity consistent with the actual weights.

</details>

<details>
<summary><strong>Retrieval caching and semantic reranking</strong></summary>

Filtered BM25 indexes and query vectors use bounded in-memory caches. The invited analysis workspace shows phase, model, and tool durations with token usage and cache hits. Completed-task recovery preserves the recorded total duration. Shared provider circuits stop repeated transient failures and probe for recovery.

The optional CPU Cross-Encoder is pinned in [reranker.json](configs/reranker.json). The default `enabled=false` retains fast retrieval and supplies the control arm for the comparisons below. These commands evaluate semantic reranking explicitly; set `enabled` to `true` after comparison to use it for normal analysis. The bilingual evidence comparison uses 18 Chinese/English query pairs across 9 source profiles; paired workflow verification uses 8 requests across 4 bilingual task pairs, one execution per arm, with 2 isolated same-model AI reviews. The workflow command below uses the configured API. Each recorded comparison includes a verified `source.patch` against its manifest commit.

Prepare the local selected-document cache from the versioned source snapshots. This step reuses the frozen document identities and financial data:

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

## Optional retrieval services and evidence review

<details>
<summary><strong>Redis and Milvus setup</strong></summary>

Install the optional CPU dependencies and put a Redis server executable on PATH. The verified services are Redis 8.4.0 and Milvus Lite 3.2.2 over gRPC, using pymilvus 3.0.2. The manager starts loopback services, reuses an existing healthy service, and stops only processes it owns. Milvus imports the existing 13,426 × 384 contextual vectors with versioned IDs and FLAT/IP scoring.

```powershell
conda run --no-capture-output -n finevolab python -m pip install -r requirements/retrieval_services.in
conda run --no-capture-output -n finevolab python scripts/manage_retrieval_services.py start
conda run --no-capture-output -n finevolab python scripts/manage_retrieval_services.py import
conda run --no-capture-output -n finevolab python scripts/manage_retrieval_services.py status
```

Set `redis.enabled` to `true` in `configs/retrieval_services.json` to share public-corpus passage rankings across processes. Cached IDs are restored from the current source index and checked before use; uploads retain document-scoped BM25 retrieval. Set `vector_backend` to `milvus` to use the imported collection. Credentials can be supplied privately through `FINEVOLAB_REDIS_URL` and `FINEVOLAB_MILVUS_TOKEN`; use `FINEVOLAB_RETRIEVAL_SERVICES_CONFIG` for a private service configuration. The default backend is `numpy`, with Redis disabled. On this corpus, uncached Milvus Lite warm retrieval averaged 932.85 ms; Redis plus Milvus averaged 23.59 ms. Redis provides the measured latency improvement; Milvus provides the optional storage backend while preserving final retrieval results.

</details>

<details>
<summary><strong>Evidence review and service experiments</strong></summary>

Set `mode` in `configs/agent_review.json`, or `FINEVOLAB_REVIEW_MODE`, to `off`, `self_check`, or `reviewer`. The review modes inspect explanation and hybrid claims, share the existing total call/token/time budget and single repair allowance, and preserve deterministic numerical answers. The interface and reports display review decisions. Both review roles use the configured model in separate calls; the Reviewer has its own role and context.

The role comparison used `deepseek-flash`, 24 bilingual requests from 12 validation tasks, and one run per arm. All three arms used the same retrieval configuration and total budget. Mean workflow latency was 9.02 / 11.56 / 11.76 s; model calls were 52 / 76 / 75 for analyst / self-check / Reviewer. The 8 hybrid numerical answers matched across arms. In this comparison, self-check achieved the best strict score and is the preferred opt-in review mode; the default remains `off`. The 20 controlled predictions form 10 positive/negative pairs from 3 underlying tasks. Their acceptance score includes both claim support and narrative coverage. Reviewer rejected 2 supported controls for incomplete coverage. Per-case decisions, source patches, configurations, and paired results are archived in the [service comparison](project_notes/evaluations/retrieval_service_comparison.json).

Reproduce the comparisons under new run IDs after rebuilding retrieval resources. These commands use the live model for the role, claim-review, and authenticated HTTP comparisons; the service checks and summary use no model calls.

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

Without run-path options, the summary command aggregates the versioned archived runs. [Service acceptance](project_notes/run_logs/retrieval_services_checks.json) covers 135 query configurations, idempotent vector imports, idle connections, outages, explicit fallback, cache expiry and corruption, and service restart recovery.

</details>

<details>
<summary><strong>Read-only MCP tools and correlated execution traces</strong></summary>

The local MCP Server exposes metric lookup, growth and margin calculations, company comparison, and fixed-corpus report retrieval. The business workflow selects `direct` or `mcp` through `tool_backend`, retains the same planning and validation contracts, and verifies snapshot identity on remote results. The service enforces tool and company allowlists in [mcp.json](configs/mcp.json), strict request schemas, and read-only tool annotations. It uses the local stdio transport. The SDK Client discovers tools and validates structured results:

```powershell
conda run --no-capture-output -n finevolab python scripts/call_mcp.py
conda run --no-capture-output -n finevolab python scripts/call_mcp.py `
  --tool get_financial_metrics `
  --company MSFT --metric revenue --fiscal-year 2024
```

For MCP host integration, launch `scripts/serve_mcp.py` using the Python interpreter from the installed environment. Search is restricted to the public fixed index and takes `report_year`; metric and calculation tools take `fiscal_year`. Complex requests can be supplied through `--request-file`. Financial dates and disclosure years are retained in the returned source objects.

HTTP responses propagate `traceparent`; workflow phases, model calls, deterministic tools, and MCP calls share correlated trace and parent-span identifiers. Trace files contain relative durations, status, attempts, cache hits, and observed tokens. `FINEVOLAB_TRACE_MODE` selects `off`, `memory`, `file`, or `batch`; the managed invited service defaults to `batch`. Buffered export uses a bounded queue, backpressure, failure counters, and explicit shutdown flushing. Local exports are stored in `output/traces/`, `output/mcp/`, and task trace directories. The complete HTTP workflow test uses an isolated model response while running actual SDK planning, financial tools, validation, and report generation. Both tool backends are exercised through the same HTTP endpoint:

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

The benchmark measures equivalent direct and MCP calls, loopback TCP HTTP requests, strict parameter and access-scope rejection, and injected timeout / connection-failure recovery. It records per-request outcomes, nearest-rank p50 / p95, throughput, failure rate, timeout counts, source hashes, and data identities. Model-backed smoke runs are selected separately with `--real-model`.

The paired workflow benchmark uses 10 fixed numeric plans or 12 real-model regression questions, with the same questions, model, prompts, budgets, and three repetitions across both tool backends. Real-model requests travel through TCP HTTP and produce validated reports. The trace benchmark separately measures response latency and the time needed to drain client and server exports:

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

Formal local acceptance completed **1080/1080 requests**, **15/15 permission checks**, **1080 correlated traces / 2880 spans**, and **10/10 injected-failure recoveries**. Normal-load failure and timeout counts were zero. These measurements cover fixed tools and services; business-task accuracy retains its original evaluation scope. Per-request records, p50 / p95, throughput, source identities, and representative traces are in [MCP and Trace acceptance](project_notes/evaluations/mcp_trace_acceptance.json).

Controlled export measurement completed **2880/2880 requests** across four trace modes. At concurrency 4, MCP response p95 decreased from **2627.01 to 80.79 ms** with buffered export, a **96.92%** reduction; throughput including export draining increased from **2.50 to 4.68 requests/s**. File-backed groups retained **1440 complete traces / 4320 spans**. See [trace export comparison](project_notes/evaluations/trace_export_comparison.json).

The uncached business comparison passed **36/36 tasks per backend**, using **12 development-regression questions** with **3 repetitions**. The MCP arm executed **42 remote financial-tool calls**; both arms completed source validation and HTML/PDF reporting. Fixed-plan execution passed **60/60 tasks** without model requests. These runs measure planning, numerical and source checks, and narrative completeness. Process initialization is included in workflow latency; `direct` remains the default backend. The cache-enabled comparison is archived separately in the same [paired workflow evidence](project_notes/evaluations/mcp_workflow_comparison.json).

</details>

<div id="section-web"></div>

## Web interface and deployment

The [live showcase](https://finevolab.lunelu.com/) provides Chinese / English navigation, source-linked financial cases, and interactive experiment comparisons. It runs entirely as static files. Build it from the frozen observations and recorded results:

```powershell
conda run --no-capture-output -n finevolab python scripts/build_showcase.py
```

The output is in `output/showcase/`. The Pages workflow publishes this directory. Source files are in `showcase/`. The frontend checks use Node.js 24. Install the dependencies and run the interaction checks with:

```powershell
npm ci --prefix tests/frontend
npm run test:showcase --prefix tests/frontend
```

The [project showcase](https://finevolab.lunelu.com/) includes a real archived hybrid analysis with request planning, retrieved passages, tool outputs, validation, repair status, and recorded usage. Its source links resolve uploaded evidence to public SEC snapshots by matching SHA-256. The evidence explorer filters financial observations by company, fact fiscal year, metric, and original text, preserving report year, units, and actual periods.

The showcase combines project results, archived execution replay, and evidence browsing. Its primary action opens the dedicated analysis application, which provides invitation login, questions, execution status, cited results, task history, and HTML/PDF downloads. The legacy `experience.html` address redirects to the corresponding showcase section. Page routing and bilingual checks are recorded in [page acceptance](project_notes/run_logs/site_layout_checks.json).

<details>
<summary><strong>Invited analysis application</strong></summary>

Run invite-only analysis locally with the existing model configuration:

```powershell
conda run --no-capture-output -n finevolab python scripts/manage_experience.py invite --user owner
conda run --no-capture-output -n finevolab python scripts/manage_experience.py serve
```

Open `http://127.0.0.1:8085/` and use the invitation saved under `output/experience/invite-*.txt`. The server reads the existing `FINEVOLAB_MODEL_CONFIG` setting. Invitation hashes and policy are stored in `output/experience/access.json`; sessions and task ownership are stored in `output/experience/access.sqlite3`. All remain local. The dedicated application exposes owner-scoped tasks and report downloads. It enforces atomic per-user and global daily task quotas, concurrency, idempotent submissions, and per-task `RunBudget`. Daily quotas use UTC and count failed or interrupted executions. Restarted tasks are marked interrupted. Default limits are 5 tasks per user per day, 20 globally, and 1 concurrent task; each task allows 6 model calls, 10 tool calls, 20,000 tokens, and 240 seconds. Configure invitations and quotas before starting the server. Browser sessions last 12 hours. The default service binds to loopback. With `--public-url`, the public hostname requires HTTPS and uses secure session cookies; forwarded headers are trusted only from the local connector. The public Pages workspace runs replay and evidence browsing without model requests.

</details>

<details>
<summary><strong>Cloudflare Tunnel deployment</strong></summary>

To expose the same invited application through Cloudflare Tunnel, configure `configs/tunnel.json` and save only the connector token in `output/tunnel/token.txt`. The connector installer verifies the pinned release SHA-256. Start the backend in one terminal:

```powershell
conda run --no-capture-output -n finevolab python scripts/manage_tunnel.py install
conda run --no-capture-output -n finevolab python scripts/manage_experience.py serve --public-url https://app.lunelu.com
```

In a second terminal, start the hidden connector and check readiness:

```powershell
conda run --no-capture-output -n finevolab python scripts/manage_tunnel.py start
conda run --no-capture-output -n finevolab python scripts/manage_tunnel.py status
conda run --no-capture-output -n finevolab python scripts/manage_tunnel.py verify
```

In the Cloudflare tunnel configuration, add a published application route: hostname `app.lunelu.com`, service `http://127.0.0.1:8085`, and no path filter. The [live analysis workspace](https://app.lunelu.com/) uses this route. Both the local backend and connector must remain running. `verify` checks HTTPS routing, the live workspace, and rejection of anonymous task access without calling a model. Use `--proxy` for connector installation or public verification when a network proxy is required. `stop` stops only the managed connector. Set `FINEVOLAB_BROWSER_REPORT` to a distinct report filename when recording a separate browser acceptance run.

Workspace acceptance is recorded in [workspace checks](project_notes/run_logs/experience_acceptance.json). For rendered-browser checks, set `FINEVOLAB_CHROMIUM` to an installed Chromium executable and run `node tests/frontend/check_experience_live.cjs` while the local service is running. Set `FINEVOLAB_INVITATION_FILE` to the local invitation file and add `--real` to execute a real model-backed analysis, verify tools, citations, validation, reports, refresh recovery, and idempotency. The driver reads the invitation without printing it.

</details>

<div id="section-license"></div>

## License

FinEvoLab-specific code, documentation, and modifications are licensed under **Apache-2.0**, copyright **Zehao Feng (Zayne-Feng) and FinEvoLab contributors**. See [LICENSE](LICENSE), [COPYRIGHT](COPYRIGHT), and [NOTICE](NOTICE). Selected components derive from FinRobot Equity V1; their original notices are retained in [licenses/finrobot/](licenses/finrobot/).
