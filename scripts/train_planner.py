"""Train completion-only QLoRA adapters on a frozen request-planning split."""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.planner_benchmark import experiment_manifest, load_bundle, planner_messages
from finevolab.runtime import write_json

MODEL_SPEC = json.loads((ROOT / "configs/planner_model.json").read_text(encoding="utf-8"))


class TrainingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: Literal["Qwen/Qwen3-4B-Instruct-2507"]
    quantization: Literal["nf4"]
    dtype: Literal["bfloat16"]
    max_length: int = Field(ge=128)
    lora_rank: int = Field(gt=0)
    lora_alpha: int = Field(gt=0)
    lora_dropout: float = Field(ge=0, lt=1)
    learning_rate: float = Field(gt=0, allow_inf_nan=False)
    num_train_epochs: float = Field(gt=0, allow_inf_nan=False)
    per_device_train_batch_size: int = Field(gt=0)
    gradient_accumulation_steps: int = Field(gt=0)
    seed: int = Field(ge=0)


def identity_callback(base_class, identity):
    class IdentityCallback(base_class):
        def on_save(self, training_args, state, control, **kwargs):
            write_json(Path(training_args.output_dir) / f"checkpoint-{state.global_step}" / "training_identity.json", identity)
    return IdentityCallback()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--revision", default=MODEL_SPEC["revision"])
    parser.add_argument("--config", type=Path, default=ROOT / "configs/planner_training.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--resume-from-checkpoint", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise ValueError("An immutable 40-character model revision is required")
    if args.revision != MODEL_SPEC["revision"]:
        raise ValueError("The training revision differs from the registered model")
    if (args.output.exists() and args.resume_from_checkpoint is None) or args.max_steps == 0 or args.max_steps < -1:
        raise ValueError("A new output directory and a positive step limit or -1 are required")
    if args.resume_from_checkpoint is not None and (
        not args.resume_from_checkpoint.resolve().is_relative_to(args.output.resolve()) or
        not (args.resume_from_checkpoint / "trainer_state.json").is_file() or
        (args.output / "training_result.json").exists()):
        raise ValueError("Resume requires an unfinished run and its own saved checkpoint")
    training, contract = load_bundle(args.bundle, "development")
    validation, _ = load_bundle(args.bundle, "validation")
    config = TrainingConfig.model_validate_json(args.config.read_text(encoding="utf-8")).model_dump()
    import torch
    from datasets import Dataset
    from peft import LoraConfig, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainerCallback, set_seed
    from trl import SFTConfig, SFTTrainer
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise ValueError("Training requires a CUDA GPU with bfloat16 support")
    set_seed(config["seed"])
    tokenizer = AutoTokenizer.from_pretrained(config["model"], revision=args.revision)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    def dataset(cases):
        rows = []
        for case in cases:
            prompt = planner_messages(contract, case.input.model_dump())
            completion = [{"role": "assistant", "content": case.expected.model_dump_json()}]
            tokens = tokenizer.apply_chat_template(prompt + completion, tokenize=True)
            if len(tokens) > config["max_length"]:
                raise ValueError("A training or validation example exceeds the token limit")
            rows.append({"prompt": prompt, "completion": completion})
        return Dataset.from_list(rows)
    train_dataset, validation_dataset = dataset(training), dataset(validation)
    options = SFTConfig(output_dir=str(args.output), max_length=None,
        eos_token=tokenizer.eos_token, learning_rate=config["learning_rate"], num_train_epochs=config["num_train_epochs"],
        per_device_train_batch_size=config["per_device_train_batch_size"], per_device_eval_batch_size=1,
        gradient_accumulation_steps=config["gradient_accumulation_steps"], seed=config["seed"],
        data_seed=config["seed"], bf16=True, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False}, completion_only_loss=True,
        eval_strategy="epoch", save_strategy="epoch", logging_steps=1, report_to="none",
        max_steps=args.max_steps, packing=False, load_best_model_at_end=False)
    args.output.mkdir(parents=True, exist_ok=args.resume_from_checkpoint is not None)
    experiment = {"bundle_sha256": sha256(args.bundle / "manifest.json"), "model_revision": args.revision,
        "training": config, "max_steps": args.max_steps, "train_cases": len(training),
        "validation_cases": len(validation), "test_used": False,
        "code": {p: sha256(ROOT / p) for p in ("scripts/train_planner.py", "finevolab/planner_benchmark.py")},
        "packages": {name: version(name) for name in ("torch", "transformers", "peft", "trl", "datasets", "bitsandbytes")},
        "gpu": torch.cuda.get_device_name(0), "cuda": torch.version.cuda}
    if args.resume_from_checkpoint is not None:
        if json.loads((args.output / "manifest.json").read_text(encoding="utf-8"))["config"] != experiment:
            raise ValueError("The training configuration changed since the checkpoint")
    else:
        write_json(args.output / "manifest.json", experiment_manifest(ROOT, args.output.name, experiment))
    identity = {"model": config["model"], "revision": args.revision,
        "bundle_sha256": experiment["bundle_sha256"], "training_manifest_sha256": sha256(args.output / "manifest.json")}
    if args.resume_from_checkpoint is not None and json.loads(
        (args.resume_from_checkpoint / "training_identity.json").read_text(encoding="utf-8")) != identity:
        raise ValueError("The checkpoint belongs to a different training run")
    (args.output / "environment.txt").write_text(
        subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True), encoding="utf-8")
    quantization = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(config["model"], revision=args.revision,
        device_map={"": 0}, dtype=torch.bfloat16, quantization_config=quantization)
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model.config.use_cache = False
    adapter = LoraConfig(r=config["lora_rank"], lora_alpha=config["lora_alpha"],
        lora_dropout=config["lora_dropout"], target_modules="all-linear", task_type="CAUSAL_LM", bias="none")
    trainer = SFTTrainer(model=model, processing_class=tokenizer, args=options,
        train_dataset=train_dataset, eval_dataset=validation_dataset, peft_config=adapter,
        callbacks=[identity_callback(TrainerCallback, identity)])
    result = trainer.train(resume_from_checkpoint=str(args.resume_from_checkpoint) if args.resume_from_checkpoint else None)
    trainer.save_model(str(args.output / "adapter"))
    write_json(args.output / "adapter/training_identity.json", identity)
    tokenizer.save_pretrained(args.output / "tokenizer")
    write_json(args.output / "training_result.json", {"status": "completed", "metrics": result.metrics,
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "adapter_sha256": sha256(args.output / "adapter/adapter_model.safetensors"),
        "adapter_config_sha256": sha256(args.output / "adapter/adapter_config.json")})
    print(json.dumps({"status": "completed", "output": str(args.output)}))


if __name__ == "__main__":
    main()
