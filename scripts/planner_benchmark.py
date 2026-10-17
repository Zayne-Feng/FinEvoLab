"""Prepare, execute, and compare bounded request-planning experiments."""

import argparse
import asyncio
from contextlib import AsyncExitStack
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re
import os
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from filelock import FileLock
from finevolab.config import ModelConfig
from finevolab.experiments import sha256
from finevolab.planner_benchmark import (ARMS, SPLITS, experiment_manifest, freeze_bundle, load_bundle, paired_report,
    parsed_plan, planner_messages, summarize_predictions, validate_prediction_rows)
from finevolab.runtime import fingerprint, write_json

MODEL_SPEC = json.loads((ROOT / "configs/planner_model.json").read_text(encoding="utf-8"))


def packages():
    result = {}
    for name in ("torch", "transformers", "peft", "bitsandbytes", "openai", "pydantic"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    return result


def implementation_hashes():
    names = ("finevolab/planner_benchmark.py", "scripts/planner_benchmark.py", "finevolab/grounded.py",
             "finevolab/sdk_runtime.py", "finevolab/config.py", "finevolab/provider.py",
             "scripts/estimate_paired_uncertainty.py")
    return {name: sha256(ROOT / name) for name in names}


def adapter_identity(path):
    if path is None:
        return None
    directory = Path(path)
    files = {name: sha256(directory / name) for name in
             ("adapter_config.json", "adapter_model.safetensors", "training_identity.json")}
    identity = json.loads((directory / "training_identity.json").read_text(encoding="utf-8"))
    return {"files": files, "sha256": fingerprint(files), "training": identity}


def arm_identity(args, settings=None):
    if args.arm == "deepseek":
        if args.backend != "api" or settings is None:
            raise ValueError("The DeepSeek reference requires the API backend")
        if not settings.model.casefold().startswith("deepseek"):
            raise ValueError("The configured reference model is not DeepSeek")
        return {"backend": "api", "model": settings.model, "endpoint": settings.base_url,
                "extra_body": settings.extra_body, "adapter": None, "packages": packages()}
    if args.backend != "transformers" or not args.revision or not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise ValueError("Qwen arms require Transformers and an immutable 40-character model revision")
    if args.model != "Qwen/Qwen3-4B-Instruct-2507":
        raise ValueError("The Qwen arm differs from the registered model")
    if args.revision != MODEL_SPEC["revision"]:
        raise ValueError("The Qwen revision differs from the registered model")
    if (args.arm == "qwen_lora") != bool(args.adapter):
        raise ValueError("Only the LoRA arm requires an adapter directory")
    adapter = adapter_identity(args.adapter)
    if adapter is not None and any(adapter["training"].get(key) != expected for key, expected in
        (("model", args.model), ("revision", args.revision), ("bundle_sha256", sha256(args.bundle / "manifest.json")))):
        raise ValueError("The adapter was trained with a different model revision or dataset")
    import torch
    if args.device == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA is unavailable")
    hardware = {"gpu": torch.cuda.get_device_name(0) if args.device == "cuda" else None,
        "total_memory": torch.cuda.get_device_properties(0).total_memory if args.device == "cuda" else None,
        "cuda": torch.version.cuda}
    return {"backend": "transformers", "model": args.model, "revision": args.revision,
            "adapter": adapter, "dtype": args.dtype,
            "quantization": args.quantization, "device": args.device, "packages": packages(), "hardware": hardware,
            "threads": {name: os.environ.get(name) for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "TOKENIZERS_PARALLELISM")}}


class TransformersBackend:
    def __init__(self, args):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        if args.device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA is unavailable")
        if args.dtype == "bfloat16" and args.device == "cuda" and not torch.cuda.is_bf16_supported():
            raise ValueError("The selected GPU does not support bfloat16")
        if args.quantization != "none" and args.device != "cuda":
            raise ValueError("NF4 evaluation requires CUDA")
        dtype = getattr(torch, args.dtype)
        options = {"revision": args.revision, "dtype": dtype, "device_map": args.device}
        if args.quantization == "nf4":
            options["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype)
        self.tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
        self.model = AutoModelForCausalLM.from_pretrained(args.model, **options)
        if args.adapter:
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, args.adapter, is_trainable=False)
        self.model.eval()
        self.model.generation_config.temperature = None
        self.model.generation_config.top_p = None
        self.model.generation_config.top_k = None
        self.args, self.torch = args, torch
        self.hardware = {"device": args.device, "gpu": torch.cuda.get_device_name(0) if args.device == "cuda" else None,
                         "cuda": torch.version.cuda}

    async def generate(self, messages):
        torch = self.torch
        started = time.monotonic()
        inputs = self.tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt")
        if inputs["input_ids"].shape[-1] > self.args.max_input_tokens:
            raise ValueError("The model input exceeds the frozen token limit")
        inputs = inputs.to(self.model.device)
        if self.args.device == "cuda":
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
        with torch.inference_mode():
            output = self.model.generate(**inputs, do_sample=False, max_new_tokens=self.args.max_new_tokens,
                pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id)
        if self.args.device == "cuda":
            torch.cuda.synchronize()
        count = inputs["input_ids"].shape[-1]
        generated = output[0, count:]
        return {"raw": self.tokenizer.decode(generated, skip_special_tokens=True),
            "elapsed_seconds": time.monotonic() - started,
            "usage": {"requests": 1, "input_tokens": count, "output_tokens": len(generated), "total_tokens": count + len(generated)},
            "peak_allocated_bytes": torch.cuda.max_memory_allocated() if self.args.device == "cuda" else None}


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def check_selection(path, config):
    if path is None:
        raise ValueError("Test execution requires a frozen validation selection")
    seal = json.loads(path.read_text(encoding="utf-8"))
    identity = {key: config[key] for key in ("bundle_sha256", "generation", "implementation")}
    allowed = [seal["arms"].get(config["arm"])] + seal.get("additional_models", {}).get(config["arm"], [])
    if seal["shared"] != identity or config["model"] not in allowed:
        raise ValueError("The model or execution contract differs from the validation selection")


async def predict(args):
    if args.repeats < 1 or args.max_new_tokens < 1 or args.max_input_tokens < 1:
        raise ValueError("Repetitions and token limits must be positive")
    cases, contract = load_bundle(args.bundle, args.split)
    settings = ModelConfig.load(args.config) if args.backend == "api" else None
    config = {"bundle_sha256": sha256(args.bundle / "manifest.json"), "split": args.split, "arm": args.arm,
        "repeats": args.repeats, "model": arm_identity(args, settings), "implementation": implementation_hashes(),
        "generation": {"temperature": 0, "max_new_tokens": args.max_new_tokens,
            "max_input_tokens": args.max_input_tokens, "repairs": args.repairs,
            "output_constraint": "prompt_only", "automatic_transport_retries": 0}}
    if args.split == "test":
        check_selection(args.selection, config)
        config["selection_sha256"] = sha256(args.selection)
    if args.output.resolve().is_relative_to(args.bundle.resolve()):
        raise ValueError("Predictions must not overwrite the frozen dataset")
    args.output.mkdir(parents=True, exist_ok=True)
    with FileLock(str(args.output / "execution.lock"), timeout=0):
        path = args.output / "manifest.json"
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8"))["config"] != config:
                raise ValueError("The run configuration changed; use a new output directory")
        else:
            write_json(path, experiment_manifest(ROOT, args.output.name, config))
        rows = read_rows(args.output / "predictions.jsonl")
        done = validate_prediction_rows(rows, cases, args.repeats, require_complete=False, max_repairs=args.repairs)
        inflight = args.output / "inflight.json"
        if inflight.exists():
            interrupted = json.loads(inflight.read_text(encoding="utf-8"))
            if (interrupted["case_id"], interrupted["repeat"]) not in done:
                raise ValueError("An interrupted attempt has unknown usage; preserve this run and start a new run ID")
            inflight.unlink()
        if len(done) == len(cases) * args.repeats:
            write_json(args.output / "summary.json", {"status": "completed",
                **summarize_predictions(cases, rows, contract["aliases"]),
                "predictions_sha256": sha256(args.output / "predictions.jsonl")})
            print(json.dumps({"status": "already_completed", "model_requests_added": 0}))
            return
        async with AsyncExitStack() as stack:
            if settings:
                from finevolab.provider import async_client
                parent = await stack.enter_async_context(async_client(settings))
                client = parent.with_options(max_retries=0)
                async def generate(messages):
                    started = time.monotonic()
                    response = await client.chat.completions.create(model=settings.model, messages=messages,
                        temperature=0, max_tokens=args.max_new_tokens, extra_body=settings.extra_body)
                    usage = response.usage
                    return {"raw": response.choices[0].message.content or "",
                        "elapsed_seconds": time.monotonic() - started,
                        "usage": {"requests": 1, "input_tokens": usage.prompt_tokens,
                            "output_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens} if usage else None}
            else:
                backend = TransformersBackend(args)
                generate = backend.generate
                write_json(args.output / "hardware.json", backend.hardware)
            for repeat in range(args.repeats):
                order = cases if repeat % 2 == 0 else list(reversed(cases))
                for case in order:
                    if (case.id, repeat) in done:
                        continue
                    messages = planner_messages(contract, case.input.model_dump())
                    attempts = []
                    for attempt_index in range(args.repairs + 1):
                        write_json(inflight, {"case_id": case.id, "repeat": repeat, "attempt": attempt_index,
                                             "input_sha256": fingerprint(case.input.model_dump())})
                        started = time.monotonic()
                        try:
                            attempt = await generate(messages)
                        except Exception as error:
                            attempt = {"raw": "", "elapsed_seconds": time.monotonic() - started,
                                       "usage": None, "error_type": type(error).__name__}
                        attempts.append(attempt)
                        if attempt.get("error_type"):
                            break
                        try:
                            parsed_plan(attempt["raw"])
                            break
                        except ValueError:
                            if attempt_index < args.repairs:
                                messages += [{"role": "assistant", "content": attempt["raw"]},
                                    {"role": "user", "content": "Return only a valid JSON object matching the supplied schema. "
                                     "Preserve the request meaning and use only the supplied input."}]
                    row = {"case_id": case.id, "repeat": repeat, "attempts": attempts,
                           "input_sha256": fingerprint(case.input.model_dump())}
                    with (args.output / "predictions.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    rows.append(row)
                    inflight.unlink()
                    print(json.dumps({"completed": len(rows), "planned": len(cases) * args.repeats,
                        "case_id": case.id, "arm": args.arm}), flush=True)
        validate_prediction_rows(rows, cases, args.repeats, max_repairs=args.repairs)
        summary = summarize_predictions(cases, rows, contract["aliases"])
        write_json(args.output / "summary.json", {"status": "completed", **summary,
            "predictions_sha256": sha256(args.output / "predictions.jsonl")})


def checked_run(directory, bundle, split):
    config = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["config"]
    if config["bundle_sha256"] != sha256(bundle / "manifest.json") or config["split"] != split:
        raise ValueError("The run uses a different dataset or split")
    rows = read_rows(directory / "predictions.jsonl")
    cases, _ = load_bundle(bundle, split)
    validate_prediction_rows(rows, cases, config["repeats"], max_repairs=config["generation"]["repairs"])
    inputs = {case.id: fingerprint(case.input.model_dump()) for case in cases}
    if any(row.get("input_sha256") != inputs[row["case_id"]] for row in rows):
        raise ValueError("A prediction used a different model input")
    return config, rows


def validate_arm_contracts(configs, split):
    if set(configs) != set(ARMS):
        raise ValueError("All three experiment arms are required")
    for name in ("generation", "implementation", "repeats"):
        if any(config[name] != configs["qwen_base"][name] for config in configs.values()):
            raise ValueError("The paired execution contract differs")
    base, tuned = configs["qwen_base"]["model"], configs["qwen_lora"]["model"]
    if {k: v for k, v in base.items() if k != "adapter"} != {k: v for k, v in tuned.items() if k != "adapter"}:
        raise ValueError("Qwen arms differ in more than adapter weights")
    if split == "test" and (any(not config.get("selection_sha256") for config in configs.values()) or
                            len({config["selection_sha256"] for config in configs.values()}) != 1):
        raise ValueError("Test arms must use the same validation selection")


def compare(args):
    cases, contract = load_bundle(args.bundle, args.split)
    configs, records = {}, {}
    for directory in args.runs:
        config, rows = checked_run(directory, args.bundle, args.split)
        if config["arm"] in configs:
            raise ValueError("Duplicate experiment arm")
        configs[config["arm"]], records[config["arm"]] = config, rows
    validate_arm_contracts(configs, args.split)
    repeats = configs["qwen_base"]["repeats"]
    result = {"split": args.split, "bundle_sha256": sha256(args.bundle / "manifest.json"),
        "status": "completed", "groups": {arm: summarize_predictions(cases, rows, contract["aliases"])
            for arm, rows in records.items()}, "comparisons": {},
        "inputs": {str(directory): {"manifest": sha256(directory / "manifest.json"),
            "predictions": sha256(directory / "predictions.jsonl")} for directory in args.runs}}
    for left, right in (("qwen_base", "qwen_lora"), ("deepseek", "qwen_lora")):
        result["comparisons"][f"{left}_to_{right}"] = {phase: paired_report(cases,
            records[left], records[right], contract["aliases"], repeats, phase) for phase in ("first", "final")}
        result["comparisons"][f"{left}_to_{right}"]["semantic"] = paired_report(cases,
            records[left], records[right], contract["aliases"], repeats, "first", "semantic_score")
    write_json(args.output, result)
    print(json.dumps({"status": "completed", "output": str(args.output)}))


def seal(args):
    configs = {}
    for directory in args.runs:
        config, _ = checked_run(directory, args.bundle, "validation")
        if config["arm"] in configs:
            raise ValueError("Duplicate validation arm")
        configs[config["arm"]] = config
    if set(configs) != set(ARMS) or args.output.exists():
        raise ValueError("Three validation arms and a new selection path are required")
    validate_arm_contracts(configs, "validation")
    reference = configs["qwen_base"]
    additional = []
    for directory in getattr(args, "replicate_runs", None) or []:
        config, _ = checked_run(directory, args.bundle, "validation")
        if config["arm"] != "qwen_lora":
            raise ValueError("Additional validation runs must be LoRA replicates")
        validate_arm_contracts({**configs, "qwen_lora": config}, "validation")
        additional.append(config["model"])
    write_json(args.output, {"shared": {key: reference[key] for key in
        ("bundle_sha256", "generation", "implementation")}, "arms": {arm: config["model"] for arm, config in configs.items()},
        "additional_models": {"qwen_lora": additional},
        "validation_runs": {str(path): {"manifest": sha256(path / "manifest.json"),
            "predictions": sha256(path / "predictions.jsonl")} for path in args.runs}})
    print(json.dumps({"status": "sealed", "output": str(args.output)}))


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--source", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    for command in ("predict", "compare", "seal"):
        child = commands.add_parser(command)
        child.add_argument("--bundle", type=Path, required=True)
        child.add_argument("--output", type=Path, required=True)
        if command != "seal":
            child.add_argument("--split", choices=SPLITS, required=True)
        if command in {"compare", "seal"}:
            child.add_argument("--runs", type=Path, nargs=3, required=True)
            if command == "seal":
                child.add_argument("--replicate-runs", type=Path, nargs="*", default=[])
        else:
            child.add_argument("--arm", choices=ARMS, required=True)
            child.add_argument("--backend", choices=["api", "transformers"], required=True)
            child.add_argument("--config", type=Path)
            child.add_argument("--model", default="Qwen/Qwen3-4B-Instruct-2507")
            child.add_argument("--revision", default=MODEL_SPEC["revision"])
            child.add_argument("--adapter", type=Path)
            child.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
            child.add_argument("--dtype", choices=["bfloat16", "float16", "float32"], default="bfloat16")
            child.add_argument("--quantization", choices=["none", "nf4"], default="none")
            child.add_argument("--repeats", type=int, default=3)
            child.add_argument("--repairs", type=int, choices=[0, 1], default=0)
            child.add_argument("--max-new-tokens", type=int, default=512)
            child.add_argument("--max-input-tokens", type=int, default=8192)
            child.add_argument("--selection", type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        frozen = freeze_bundle(args.source, args.output,
            json.loads((ROOT / "prompts/benchmark.json").read_text(encoding="utf-8")),
            json.loads((ROOT / "configs/company_aliases.json").read_text(encoding="utf-8")))
        print(json.dumps(frozen["splits"]))
    elif args.command == "predict":
        asyncio.run(predict(args))
    elif args.command == "compare":
        compare(args)
    else:
        seal(args)


if __name__ == "__main__":
    main()
