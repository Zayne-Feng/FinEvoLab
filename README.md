# FinEvoLab

<p>
  <a href="README.md"><kbd>English</kbd></a>
  <a href="README_CN.md"><kbd>简体中文</kbd></a>
</p>

**A financial Agent with traceable sources: RAG retrieves evidence, tools compute values, and a harness validates and resumes execution.**

Built on FinRobot Equity V1. This README has been modified for FinEvoLab.

FinEvoLab is an independent derivative project built on FinRobot Equity V1. It connects public financial reports, structured financial metrics, model planning, and report generation into an executable analysis workflow, evaluated through component comparisons, QLoRA fine-tuning, and inference quantization experiments. The web interface supports Chinese / English and offers API or LoRA planning backends.

## Features

- **Reports and evidence**: SEC / company IR snapshots, financial metric extraction, period and unit checks, and citations that locate the original sources; supports selected HTML files and PDFs with extractable text.
- **RAG**: BM25, multilingual E5, and RRF hybrid retrieval over the fixed corpus; retrieval within selected uploaded documents; combined numerical and narrative analysis; optional shared Redis caching and a versioned Milvus vector backend.
- **Agent and tools**: Agents SDK interprets requests and invokes metric lookup, growth calculation, company comparison, and report tools. Programs compute amounts and formulas and assemble financial objects from actual tool results.
- **Harness**: Schema, source, and task-status validation, at most one targeted repair, version-bound stage checkpoints, execution recovery, and optional self-check or isolated evidence-review roles within the same task budget.
- **Models and interface**: DeepSeek API, Qwen3-4B-Instruct-2507 LoRA BF16 / NF4 planning backends, FastAPI endpoints, a bilingual web interface, and HTML / PDF reports.

The default API mode runs with CPU retrieval. The LoRA service runs on an external GPU and connects through SSH port forwarding. LoRA handles `RequestPlan` planning, while the API model generates explanatory narratives. All backends share the financial tools, RAG, and harness.

The primary application is `finevolab/`, with reused Equity V1 modules and the web application shell in `finrobot_equity/`. Use the Conda setup and application commands below.

## Quick start

Run the following commands from the repository root. The entry point has been verified on Windows / PowerShell. Set `$condaExe` to your Conda executable. If the `finevolab` environment already exists, skip the environment creation and dependency installation commands.

```powershell
$condaExe = 'D:\ProgramSoftware\Miniconda3\Scripts\conda.exe'
& $condaExe env create -f environments/development.yml
& $condaExe run -n finevolab python -m pip install -r requirements/development.lock.txt
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
```

Build the retrieval resources from the repository root on first use; reuse existing matching resources. The encoding scripts download the pinned Embedding model revision and generate vectors and caches locally.

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/build_index.py
& $condaExe run --no-capture-output -n finevolab python scripts/build_vectors.py
& $condaExe run --no-capture-output -n finevolab python scripts/build_contextual_index.py
```

Offline mode uses fixed financial-report snapshots. The natural-language Agent reads the local `finrobot_equity/core/config/config.ini`, or a private configuration specified through `FINEVOLAB_MODEL_CONFIG`. Fill in a valid key and model name in the minimal configuration below. DeepSeek requests explicitly disable Thinking.

```ini
[API_KEYS]
openai_api_key = YOUR_API_KEY
openai_model = YOUR_MODEL_NAME
openai_base_url = https://api.deepseek.com/
```

```powershell
& $condaExe run --no-capture-output -n finevolab python -m uvicorn finevolab.integrated_web:app --host 127.0.0.1 --port 8003
```

Open **http://127.0.0.1:8003/finevolab**. Try “Compare Apple and Microsoft revenue for fiscal year 2023 and explain the fiscal-period differences,” or “Calculate Microsoft year-over-year revenue growth for fiscal year 2023 and cite the report to explain the change.” Dependency locks are in [environments/](environments/) and [requirements/](requirements/). The retrieval model and revision are pinned in [retrieval.json](configs/retrieval.json).

## Results

| Evaluation scope | Strict successes and resource metrics | Evidence |
| --- | --- | --- |
| Common supported business-Agent tasks | Earlier FinEvoLab engine 179/195 → improved engine 195/195; 65 questions, 3 repetitions each | [Business evaluation](project_notes/experiments/scope-test-v1/assessment.json) |
| Expanded business-Agent main test | 291/300, 97.00%; 100 questions, 3 repetitions each | [Full results](project_notes/experiments/scope-test-v1/assessment.json) |
| Request-planner formal test | Qwen base 109/240; two LoRA seeds: 212/240 and 213/240; DeepSeek 227/240 | [QLoRA comparison](project_notes/evaluations/planner_formal_results.json) |
| BF16 / NF4 inference with the same weights | Strict successes: 212/240 and 211/240; NF4 reduced peak allocated memory by 62.58% and increased mean latency by 47.18% | [Quantization results](project_notes/evaluations/planner_quantization.json) |
| Retrieval cache comparison | 135 query configurations × 5 repetitions; identical results; warm mean 68.52 → 6.16 ms, 11.12× | [Cache comparison](project_notes/experiments/retrieval-cache-v1/summary.json) |
| Shared Redis retrieval cache | Four arms, 77 questions × 3 repetitions; 693 paired comparisons with identical passages; warm mean 91.13 → 22.44 ms, 4.06× | [Service comparison](project_notes/evaluations/retrieval_service_comparison.json) |
| Same-budget narrative review | Analyst 22/24; self-check 24/24; isolated Reviewer 23/24; 72 real workflows with two blind scoring contexts | [Role comparison](project_notes/experiments/agent-roles-v1/summary.json) |
| Controlled unsupported claims | Acceptance decisions: source validation 10/20, self-check 20/20, Reviewer 18/20; both model review modes rejected all 10 unsupported controls | [Support controls](project_notes/experiments/claim-review-v3/summary.json) |
| Optional semantic reranking | Complete gold-quote hits in top 5: 15/36 → 23/36; paired workflow strict successes 8/8 for both arms | [Evidence comparison](project_notes/experiments/reranker-bilingual-v1/summary.json), [Workflow verification](project_notes/experiments/runtime-refinement-v1/summary.json) |

Business strict scores combine the numerical, period, source, and task-contract checks applicable to each question with evidence support for narrative claims. Planner strict scores check the `RequestPlan` schema and relevant request semantics. In the paired quantization run, BF16 / NF4 peak allocated memory was 7.995 / 2.992 GiB and mean generation latency was 3.759 / 5.532 s.

The project has completed business-Agent evaluation, component ablations, QLoRA with two seeds, quantization comparisons, and live web acceptance. Run and source references are listed in the [experiment inventory](project_notes/evaluations/experiment_inventory.json); live LoRA service and web checks are recorded in [service acceptance](project_notes/run_logs/planner_deployment_checks.json). The complete core regression passed **224 tests**; see [runtime verification](project_notes/run_logs/service_integration_checks.json). The archived reproduction export passed 178 tests; see [reproduction-copy acceptance](project_notes/run_logs/experience_acceptance.json).

## Reproducing experiments

Code and inputs are organized as follows: `finevolab/` contains business modules, `scripts/` contains data and experiment entry points, `configs/` and `prompts/` contain functional configuration, `finevolab/templates/` contains HTML templates for the web interface and business reports, `data/` contains source snapshots and frozen datasets, and `tests/` contains regression and frontend checks. JSON / JSONL files, environment snapshots, and representative images under `project_notes/` provide versioned experimental evidence. Development notes and optional Markdown report templates remain local; formal summaries are recorded as JSON results.

The development / validation / test splits contain 50/50/100 business tasks and 600/120/240 internally authored bilingual planner tasks. The planner development split supplies the SFT training examples. Use the frozen datasets and manifests in the repository. The following commands replay numerical results from archived predictions or run a business comparison with 3 repetitions under a new run ID, review narrative evidence, and aggregate strict scores:

Paired experiments load the original engine from Git history, so use a clone with the complete commit history. `scripts/verify_repository_distribution.py` checks the submission scope, source snapshots, frozen inputs, and legal files.

```powershell
& $condaExe run --no-capture-output -n finevolab python -m unittest discover -s tests -q
& $condaExe run --no-capture-output -n finevolab python scripts/replay_planner_predictions.py --bundle data/training/planner_formal/frozen_v1 --split test --runs project_notes/experiments/planner-formal-deepseek-test project_notes/experiments/planner-formal-base-test project_notes/experiments/planner-formal-lora17-test --case-ids data/training/planner_formal/replay_case_ids.json --output output/reproduction/replay17
& $condaExe run --no-capture-output -n finevolab python scripts/run_scope_experiments.py --split test --paired --repeats 3 --run-id reproduction_scope_test
& $condaExe run --no-capture-output -n finevolab python scripts/review_scope_answers.py --experiment reproduction_scope_test --split test --run-id reproduction_scope_review
& $condaExe run --no-capture-output -n finevolab python scripts/summarize_scope_results.py --experiment reproduction_scope_test --review reproduction_scope_review
```

Business comparisons use the configured live API. Training and quantization were verified on an RTX 3090 with 24 GB VRAM, with the model revision pinned in `configs/planner_model.json`. The complete environment snapshot from the formal run is recorded in [planner_serving_environment.txt](project_notes/run_logs/planner_serving_environment.txt). Install dependencies in a separate CUDA GPU Python environment and run:

```bash
python -m pip install -r requirements/planner_training.in -r requirements/planner_serving.in
python scripts/train_planner.py --bundle data/training/planner_formal/frozen_v1 --config configs/planner_formal_seed17.json --output output/training/reproduction-seed17
python scripts/train_planner.py --bundle data/training/planner_formal/frozen_v1 --config configs/planner_formal_seed29.json --output output/training/reproduction-seed29
python scripts/run_quantization_comparison.py --bundle data/training/planner_formal/frozen_v1 --adapter output/training/reproduction-seed17/checkpoint-75 --mode none --output output/reproduction/quant-bf16
python scripts/run_quantization_comparison.py --bundle data/training/planner_formal/frozen_v1 --adapter output/training/reproduction-seed17/checkpoint-75 --mode nf4 --output output/reproduction/quant-nf4
```

For planner evaluation on the same questions, use `scripts/planner_benchmark.py predict`. Select the arm with `--arm deepseek --backend api`, `--arm qwen_base --backend transformers`, or `--arm qwen_lora --backend transformers --adapter <checkpoint>`. The formal settings are `--repeats 1 --repairs 0 --max-new-tokens 512 --max-input-tokens 8192`, with the same `--bundle` and a separate `--output` for each run. The reported comparison uses the validation-selected common epoch 1, corresponding to checkpoint 75.

First run `--split validation` for DeepSeek, Qwen base, and both LoRA seeds, saving their outputs in the directories below. Freeze the validation selection with seed 17 as the primary LoRA arm and seed 29 as its replicate:

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/planner_benchmark.py seal --bundle data/training/planner_formal/frozen_v1 --runs output/reproduction/deepseek-validation output/reproduction/base-validation output/reproduction/lora17-validation --replicate-runs output/reproduction/lora29-validation --output output/reproduction/selection.json
```

Then run each arm with `--split test --selection output/reproduction/selection.json`, retaining its validation model / adapter, environment, and generation settings. Save the test outputs under the matching directories below and compare each LoRA seed against the shared DeepSeek and Qwen base controls:

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/planner_benchmark.py compare --bundle data/training/planner_formal/frozen_v1 --split test --runs output/reproduction/deepseek-test output/reproduction/base-test output/reproduction/lora17-test --output output/reproduction/planner_seed17.json
& $condaExe run --no-capture-output -n finevolab python scripts/planner_benchmark.py compare --bundle data/training/planner_formal/frozen_v1 --split test --runs output/reproduction/deepseek-test output/reproduction/base-test output/reproduction/lora29-test --output output/reproduction/planner_seed29.json
```

Each CLI lists its complete arguments through `--help`. Original predictions and per-arm manifests are stored under `project_notes/experiments/planner-formal-*`.

Start the LoRA service with `python scripts/serve_planner.py --bundle data/training/planner_formal/frozen_v1 --adapter output/training/reproduction-seed17/checkpoint-75 --port 8082`. Use SSH to forward remote `127.0.0.1:8082` to local `127.0.0.1:8787`, matching the loopback address in [planner_service.json](configs/planner_service.json). The web interface supports BF16 / NF4 selection.

When using a retrained adapter, copy the `adapter_sha256` returned by the service's `GET /health` into the corresponding planner configuration and select that file through `FINEVOLAB_PLANNER_CONFIG`, keeping the client identity consistent with the actual weights.

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
'@ | & $condaExe run --no-capture-output -n finevolab python -
```

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/benchmark_retrieval.py --mode cache --baseline 8a10053a --output output/reproduction/retrieval_cache
& $condaExe run --no-capture-output -n finevolab python scripts/prepare_reranker.py
& $condaExe run --no-capture-output -n finevolab python scripts/benchmark_retrieval.py --mode rerank --split validation --queries data/evaluation/retrieval_refinement/bilingual_queries.json --output output/reproduction/reranker_bilingual
& $condaExe run --no-capture-output -n finevolab python scripts/benchmark_runtime_refinement.py --output output/reproduction/runtime_refinement
```

## Optional retrieval services and evidence review

Install the optional CPU dependencies and put a Redis server executable on PATH. The verified services are Redis 8.4.0 and Milvus Lite 3.2.2 over gRPC, using pymilvus 3.0.2. The manager starts loopback services, reuses an existing healthy service, and stops only processes it owns. Milvus imports the existing 13,426 × 384 contextual vectors with versioned IDs and FLAT/IP scoring.

```powershell
& $condaExe run --no-capture-output -n finevolab python -m pip install -r requirements/retrieval_services.in
& $condaExe run --no-capture-output -n finevolab python scripts/manage_retrieval_services.py start
& $condaExe run --no-capture-output -n finevolab python scripts/manage_retrieval_services.py import
& $condaExe run --no-capture-output -n finevolab python scripts/manage_retrieval_services.py status
```

Set `redis.enabled` to `true` in `configs/retrieval_services.json` to share public-corpus passage rankings across processes. Cached IDs are restored from the current source index and checked before use; uploads retain document-scoped BM25 retrieval. Set `vector_backend` to `milvus` to use the imported collection. Credentials can be supplied privately through `FINEVOLAB_REDIS_URL` and `FINEVOLAB_MILVUS_TOKEN`; use `FINEVOLAB_RETRIEVAL_SERVICES_CONFIG` for a private service configuration. The default backend is `numpy`, with Redis disabled. On this corpus, uncached Milvus Lite warm retrieval averaged 932.85 ms; Redis plus Milvus averaged 23.59 ms. Redis provides the measured latency improvement; Milvus provides the optional storage backend while preserving final retrieval results.

Set `mode` in `configs/agent_review.json`, or `FINEVOLAB_REVIEW_MODE`, to `off`, `self_check`, or `reviewer`. The review modes inspect explanation and hybrid claims, share the existing total call/token/time budget and single repair allowance, and preserve deterministic numerical answers. The interface and reports display review decisions. Both review roles use the configured model in separate calls; the Reviewer has its own role and context.

The role comparison used `deepseek-flash`, 24 bilingual requests from 12 validation tasks, and one run per arm. All three arms used the same retrieval configuration and total budget. Mean workflow latency was 9.02 / 11.56 / 11.76 s; model calls were 52 / 76 / 75 for analyst / self-check / Reviewer. The 8 hybrid numerical answers matched across arms. In this comparison, self-check achieved the best strict score and is the preferred opt-in review mode; the default remains `off`. The 20 controlled predictions form 10 positive/negative pairs from 3 underlying tasks. Their acceptance score includes both claim support and narrative coverage. Reviewer rejected 2 supported controls for incomplete coverage. Per-case decisions, source patches, configurations, and paired results are archived in the [service comparison](project_notes/evaluations/retrieval_service_comparison.json).

Reproduce the comparisons under new run IDs after rebuilding retrieval resources. These commands use the live model for the role, claim-review, and authenticated HTTP comparisons; the service checks and summary use no model calls.

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/verify_retrieval_services.py --output output/reproduction/service_checks.json
& $condaExe run --no-capture-output -n finevolab python scripts/build_showcase.py --output output/reproduction/service_site
& $condaExe run --no-capture-output -n finevolab python scripts/verify_service_analysis.py --site output/reproduction/service_site --output output/reproduction/service_analysis_checks.json
& $condaExe run --no-capture-output -n finevolab python scripts/benchmark_retrieval_services.py --repeats 3 --output output/reproduction/retrieval_services
& $condaExe run --no-capture-output -n finevolab python scripts/benchmark_agent_roles.py --output output/reproduction/agent_roles
& $condaExe run --no-capture-output -n finevolab python scripts/benchmark_claim_review.py --output output/reproduction/claim_review
& $condaExe run --no-capture-output -n finevolab python scripts/summarize_service_experiments.py --retrieval-run output/reproduction/retrieval_services --roles-run output/reproduction/agent_roles --controls-run output/reproduction/claim_review --output output/reproduction/service_summary.json
& $condaExe run --no-capture-output -n finevolab python scripts/manage_retrieval_services.py stop
```

Without run-path options, the summary command aggregates the versioned archived runs. [Service acceptance](project_notes/run_logs/retrieval_services_checks.json) covers 135 query configurations, idempotent vector imports, idle connections, outages, explicit fallback, cache expiry and corruption, and service restart recovery.

## Static showcase

The [live showcase](https://finevolab.lunelu.com/) provides Chinese / English navigation, source-linked financial cases, and interactive experiment comparisons. It runs entirely as static files. Build it from the frozen observations and recorded results:

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/build_showcase.py
```

The output is in `output/showcase/`. The Pages workflow publishes this directory. Source files are in `showcase/`; frontend checks run with `npm run test:showcase --prefix tests/frontend` after installing the frontend dependencies.

The [project showcase](https://finevolab.lunelu.com/) includes a real archived hybrid analysis with request planning, retrieved passages, tool outputs, validation, repair status, and recorded usage. Its source links resolve uploaded evidence to public SEC snapshots by matching SHA-256. The evidence explorer filters financial observations by company, fact fiscal year, metric, and original text, preserving report year, units, and actual periods.

The showcase combines project results, archived execution replay, and evidence browsing. Its primary action opens the dedicated analysis application, which provides invitation login, questions, execution status, cited results, task history, and HTML/PDF downloads. The legacy `experience.html` address redirects to the corresponding showcase section. Page routing and bilingual checks are recorded in [page acceptance](project_notes/run_logs/site_layout_checks.json).

Run invite-only analysis locally with the existing model configuration:

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/manage_experience.py invite --user owner
& $condaExe run --no-capture-output -n finevolab python scripts/manage_experience.py serve
```

Open `http://127.0.0.1:8085/` and use the invitation saved under `output/experience/invite-*.txt`. The server reads the existing `FINEVOLAB_MODEL_CONFIG` setting. Invitation hashes and policy are stored in `output/experience/access.json`; sessions and task ownership are stored in `output/experience/access.sqlite3`. All remain local. The dedicated application exposes owner-scoped tasks and report downloads. It enforces atomic per-user and global daily task quotas, concurrency, idempotent submissions, and per-task `RunBudget`. Daily quotas use UTC and count failed or interrupted executions. Restarted tasks are marked interrupted. Default limits are 5 tasks per user per day, 20 globally, and 1 concurrent task; each task allows 6 model calls, 10 tool calls, 20,000 tokens, and 240 seconds. Configure invitations and quotas before starting the server. Browser sessions last 12 hours. The default service binds to loopback. With `--public-url`, the public hostname requires HTTPS and uses secure session cookies; forwarded headers are trusted only from the local connector. The public Pages workspace runs replay and evidence browsing without model requests.

To expose the same invited application through Cloudflare Tunnel, configure `configs/tunnel.json` and save only the connector token in `output/tunnel/token.txt`. The connector installer verifies the pinned release SHA-256. Start the backend in one terminal:

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/manage_tunnel.py install
& $condaExe run --no-capture-output -n finevolab python scripts/manage_experience.py serve --public-url https://app.lunelu.com
```

In a second terminal, start the hidden connector and check readiness:

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/manage_tunnel.py start
& $condaExe run --no-capture-output -n finevolab python scripts/manage_tunnel.py status
& $condaExe run --no-capture-output -n finevolab python scripts/manage_tunnel.py verify
```

In the Cloudflare tunnel configuration, add a published application route: hostname `app.lunelu.com`, service `http://127.0.0.1:8085`, and no path filter. The [live analysis workspace](https://app.lunelu.com/) uses this route. Both the local backend and connector must remain running. `verify` checks HTTPS routing, the live workspace, and rejection of anonymous task access without calling a model. Use `--proxy` for connector installation or public verification when a network proxy is required. `stop` stops only the managed connector. Set `FINEVOLAB_BROWSER_REPORT` to a distinct report filename when recording a separate browser acceptance run.

Workspace acceptance is recorded in [workspace checks](project_notes/run_logs/experience_acceptance.json). For rendered-browser checks, set `FINEVOLAB_CHROMIUM` to an installed Chromium executable and run `node tests/frontend/check_experience_live.cjs` while the local service is running. Set `FINEVOLAB_INVITATION_FILE` to the local invitation file and add `--real` to execute a real model-backed analysis, verify tools, citations, validation, reports, refresh recovery, and idempotency. The driver reads the invitation without printing it.

## License

FinEvoLab's own code and documentation use **Apache-2.0**; copyright and licensing are stated in [COPYRIGHT](COPYRIGHT). The upstream [LICENSE](LICENSE), [NOTICE](NOTICE), and [Trademark policy](TRADEMARK_POLICY.md) are retained. Third-party data, models, and other materials remain subject to their respective terms.
