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
- **RAG**: BM25, multilingual E5, and RRF hybrid retrieval over the fixed corpus; retrieval within selected uploaded documents; combined numerical and narrative analysis.
- **Agent and tools**: Agents SDK interprets requests and invokes metric lookup, growth calculation, company comparison, and report tools. Programs compute amounts and formulas and assemble financial objects from actual tool results.
- **Harness**: Schema, source, and task-status validation, at most one targeted repair, version-bound stage checkpoints, and execution recovery.
- **Models and interface**: DeepSeek API, Qwen3-4B-Instruct-2507 LoRA BF16 / NF4 planning backends, FastAPI endpoints, a bilingual web interface, and HTML / PDF reports.

The default API mode runs with CPU retrieval. The LoRA service runs on an external GPU and connects through SSH port forwarding. LoRA handles `RequestPlan` planning, while the API model generates explanatory narratives. All backends share the financial tools, RAG, and harness.

The primary application is `finevolab/`, with reused Equity V1 modules and the web application shell in `finrobot_equity/`. The `finrobot_autogen/` and `finrobot_desktop/` directories retain the other upstream implementations.

## Quick start

The following entry point has been verified on Windows / PowerShell. Set `$condaExe` to your Conda executable. If the `finevolab` environment already exists, skip the environment creation and dependency installation commands.

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

Business strict scores combine the numerical, period, source, and task-contract checks applicable to each question with evidence support for narrative claims. Planner strict scores check the `RequestPlan` schema and relevant request semantics. In the paired quantization run, BF16 / NF4 peak allocated memory was 7.995 / 2.992 GiB and mean generation latency was 3.759 / 5.532 s.

The project has completed business-Agent evaluation, component ablations, QLoRA with two seeds, quantization comparisons, and live web acceptance. Run and source references are listed in the [experiment inventory](project_notes/evaluations/experiment_inventory.json); live LoRA service and web checks are recorded in [service acceptance](project_notes/run_logs/planner_deployment_checks.json). The complete core regression passed **188 tests**; see [Tunnel acceptance](project_notes/run_logs/tunnel_live_checks.json). An independent file copy exported from the submission scope passed 178 tests; see [reproduction-copy acceptance](project_notes/run_logs/experience_acceptance.json).

## Reproducing experiments

Code and inputs are organized as follows: `finevolab/` contains business modules, `scripts/` contains data and experiment entry points, `configs/` and `prompts/` contain functional configuration, `finevolab/templates/` contains HTML templates for the web interface and business reports, `data/` contains source snapshots and frozen datasets, and `tests/` contains regression and frontend checks. JSON / JSONL files, environment snapshots, and representative images under `project_notes/` provide versioned experimental evidence. Development notes and optional Markdown report templates remain local; formal summaries are recorded as JSON results.

The development / validation / test splits contain 50/50/100 business tasks and 600/120/240 internally authored bilingual planner tasks. The planner development split supplies the SFT training examples. Use the frozen datasets and manifests in the repository. The following commands replay numerical results from archived predictions or run a business comparison with 3 repetitions under a new run ID:

Paired experiments load the original engine from Git history, so use a clone with the complete commit history. `scripts/verify_repository_distribution.py` checks the submission scope, source snapshots, frozen inputs, and legal files.

```powershell
& $condaExe run --no-capture-output -n finevolab python -m unittest discover -s tests -q
& $condaExe run --no-capture-output -n finevolab python scripts/replay_planner_predictions.py --bundle data/training/planner_formal/frozen_v1 --split test --runs project_notes/experiments/planner-formal-deepseek-test project_notes/experiments/planner-formal-base-test project_notes/experiments/planner-formal-lora17-test --case-ids data/training/planner_formal/replay_case_ids.json --output output/reproduction/replay17
& $condaExe run --no-capture-output -n finevolab python scripts/run_scope_experiments.py --split test --paired --repeats 3 --run-id reproduction_scope_test
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

Start the LoRA service with `python scripts/serve_planner.py --bundle data/training/planner_formal/frozen_v1 --adapter output/training/reproduction-seed17/checkpoint-75 --port 8082`. Use SSH to forward remote `127.0.0.1:8082` to local `127.0.0.1:8787`, matching the loopback address in [planner_service.json](configs/planner_service.json). The web interface supports BF16 / NF4 selection. The original FinNLP submodule retains its pinned commit and can be fetched with `git submodule update --init --recursive`.

When using a retrained adapter, copy the `adapter_sha256` returned by the service's `GET /health` into the corresponding planner configuration and select that file through `FINEVOLAB_PLANNER_CONFIG`, keeping the client identity consistent with the actual weights.

## Static showcase

The [live showcase](https://finevolab.lunelu.com/) provides Chinese / English navigation, source-linked financial cases, and interactive experiment comparisons. It runs entirely as static files. Build it from the frozen observations and recorded results:

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/build_showcase.py
```

The output is in `output/showcase/`. The Pages workflow publishes this directory. Source files are in `showcase/`; frontend checks run with `npm run test:showcase --prefix tests/frontend` after installing the frontend dependencies.

The [analysis workspace](https://finevolab.lunelu.com/experience.html) at `experience.html` includes a real archived hybrid analysis with request planning, retrieved passages, tool outputs, validation, repair status, and recorded usage. Its source links resolve uploaded evidence to public SEC snapshots by matching SHA-256. The evidence explorer filters financial observations by company, fact fiscal year, metric, and original text, preserving report year, units, and actual periods.

Run invite-only analysis locally with the existing model configuration:

```powershell
& $condaExe run --no-capture-output -n finevolab python scripts/manage_experience.py invite --user owner
& $condaExe run --no-capture-output -n finevolab python scripts/manage_experience.py serve
```

Open `http://127.0.0.1:8085/experience.html` and use the invitation saved under `output/experience/invite-*.txt`. The server reads the existing `FINEVOLAB_MODEL_CONFIG` setting. Invitation hashes and policy are stored in `output/experience/access.json`; sessions and task ownership are stored in `output/experience/access.sqlite3`. All remain local. The dedicated application exposes owner-scoped tasks and report downloads. It enforces atomic per-user and global daily task quotas, concurrency, idempotent submissions, and per-task `RunBudget`. Daily quotas use UTC and count failed or interrupted executions. Restarted tasks are marked interrupted. Default limits are 5 tasks per user per day, 20 globally, and 1 concurrent task; each task allows 6 model calls, 10 tool calls, 20,000 tokens, and 240 seconds. Configure invitations and quotas before starting the server. Browser sessions last 12 hours. The default service binds to loopback. With `--public-url`, the public hostname requires HTTPS and uses secure session cookies; forwarded headers are trusted only from the local connector. The public Pages workspace runs replay and evidence browsing without model requests.

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

In the Cloudflare tunnel configuration, add a published application route: hostname `app.lunelu.com`, service `http://127.0.0.1:8085`, and no path filter. The [live analysis workspace](https://app.lunelu.com/experience.html) uses this route. Both the local backend and connector must remain running. `verify` checks HTTPS routing, the live workspace, and rejection of anonymous task access without calling a model. Use `--proxy` for connector installation or public verification when a network proxy is required. `stop` stops only the managed connector. Set `FINEVOLAB_BROWSER_REPORT` to a distinct report filename when recording a separate browser acceptance run.

Workspace acceptance is recorded in [workspace checks](project_notes/run_logs/experience_acceptance.json). For rendered-browser checks, set `FINEVOLAB_CHROMIUM` to an installed Chromium executable and run `node tests/frontend/check_experience_live.cjs` while the local service is running. Set `FINEVOLAB_INVITATION_FILE` to the local invitation file and add `--real` to execute a real model-backed analysis, verify tools, citations, validation, reports, refresh recovery, and idempotency. The driver reads the invitation without printing it.

## License

FinEvoLab's own code and documentation use **Apache-2.0**; copyright and licensing are stated in [COPYRIGHT](COPYRIGHT). The upstream [LICENSE](LICENSE), [NOTICE](NOTICE), and [Trademark policy](TRADEMARK_POLICY.md) are retained. Third-party data, models, and other materials remain subject to their respective terms.
