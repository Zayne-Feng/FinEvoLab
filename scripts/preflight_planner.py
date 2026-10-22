"""Check token boundaries and optional CPU adapter training without model downloads."""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import sys
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.planner_benchmark import experiment_manifest, load_bundle, planner_messages
from finevolab.runtime import write_json
from scripts.train_planner import TrainingConfig, identity_callback


def token_probe(bundle, tokenizer_path):
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True)
    config = TrainingConfig.model_validate_json((ROOT / "configs/planner_training.json").read_text(encoding="utf-8"))
    result = {}
    for split in ("development", "validation", "test"):
        cases, contract = load_bundle(bundle, split)
        prompt_lengths, full_lengths, completion_lengths = [], [], []
        for case in cases:
            messages = planner_messages(contract, case.input.model_dump())
            prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=True)
            full = tokenizer.apply_chat_template(messages + [{"role": "assistant",
                "content": case.expected.model_dump_json()}], tokenize=True)
            if full[:len(prompt)] != prompt or len(full) <= len(prompt):
                raise ValueError("The training completion boundary differs from the inference prefix")
            if len(full) > config.max_length or len(prompt) > 8192 or len(full) - len(prompt) > 512:
                raise ValueError("An example exceeds the registered token budget")
            if tokenizer.eos_token_id not in full[len(prompt):]:
                raise ValueError("The completion has no assistant end token")
            prompt_lengths.append(len(prompt))
            full_lengths.append(len(full))
            completion_lengths.append(len(full) - len(prompt))
        result[split] = {"cases": len(cases), "max_prompt_tokens": max(prompt_lengths),
            "max_training_tokens": max(full_lengths), "max_completion_tokens": max(completion_lengths)}
    return {"status": "passed", "splits": result, "prefix_alignment": True,
        "eos_token": tokenizer.eos_token, "tokenizer_files": {p.name: sha256(p) for p in Path(tokenizer_path).iterdir() if p.is_file()}}


def cpu_probe(bundle, tokenizer_path):
    import torch
    from datasets import Dataset
    from peft import LoraConfig, PeftModel
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import WhitespaceSplit
    from transformers import AutoTokenizer, PreTrainedTokenizerFast, Qwen3Config, Qwen3ForCausalLM, TrainerCallback, set_seed
    from trl import SFTConfig, SFTTrainer
    set_seed(17)
    torch.set_num_threads(2)
    vocabulary = {"<pad>": 0, "<unk>": 1, "<eos>": 2, "system": 3, "user": 4,
                  "assistant": 5, "Return": 6, "JSON": 7, "revenue": 8, "metric": 9}
    backend = Tokenizer(WordLevel(vocabulary, unk_token="<unk>"))
    backend.pre_tokenizer = WhitespaceSplit()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, pad_token="<pad>", unk_token="<unk>", eos_token="<eos>")
    tokenizer.chat_template = "{% for m in messages %}{{ m['role'] + ' ' + m['content'] + ' ' }}{% if m['role'] == 'assistant' %}{{ '<eos> ' }}{% endif %}{% endfor %}{% if add_generation_prompt %}{{ 'assistant ' }}{% endif %}"
    configuration = Qwen3Config(vocab_size=len(vocabulary), hidden_size=16, intermediate_size=32,
        num_hidden_layers=1, num_attention_heads=2, num_key_value_heads=1, head_dim=8,
        max_position_embeddings=128, pad_token_id=0, eos_token_id=2, bos_token_id=None)
    data = Dataset.from_list([{"prompt": [{"role": "system", "content": "Return JSON"},
        {"role": "user", "content": "revenue"}], "completion": [{"role": "assistant", "content": "metric"}]}] * 2)
    with tempfile.TemporaryDirectory() as temporary:
        configuration._name_or_path = str(Path(temporary) / "base")
        configuration.save_pretrained(configuration._name_or_path)
        options = SFTConfig(output_dir=temporary, max_length=None, use_cpu=True, bf16=False, fp16=False,
            per_device_train_batch_size=1, max_steps=1, save_strategy="steps", save_steps=1,
            report_to="none", completion_only_loss=True, packing=False, seed=17, dataloader_pin_memory=False)
        identity = {"fixture": "tiny-cpu-preflight"}
        trainer = SFTTrainer(model=Qwen3ForCausalLM(configuration), processing_class=tokenizer, args=options,
            train_dataset=data, peft_config=LoraConfig(r=2, lora_alpha=4, target_modules=["q_proj", "v_proj"], task_type="CAUSAL_LM"),
            callbacks=[identity_callback(TrainerCallback, identity)])
        batch = trainer.data_collator([trainer.train_dataset[0]])
        labels = batch["labels"][0].tolist()
        if -100 not in labels or not any(value >= 0 for value in labels):
            raise ValueError("The completion-only loss mask is invalid")
        result = trainer.train()
        if not torch.isfinite(torch.tensor(result.training_loss)):
            raise ValueError("The CPU training loss is not finite")
        adapter = Path(temporary) / "adapter"
        trainer.save_model(str(adapter))
        if json.loads((Path(temporary) / "checkpoint-1/training_identity.json").read_text(encoding="utf-8")) != identity:
            raise ValueError("The checkpoint identity callback did not save its metadata")
        PeftModel.from_pretrained(Qwen3ForCausalLM(configuration), adapter)
        trainer.args.max_steps = 2
        resumed = trainer.train(resume_from_checkpoint=str(Path(temporary) / "checkpoint-1"))
        if trainer.state.global_step != 2 or not torch.isfinite(torch.tensor(resumed.training_loss)):
            raise ValueError("The CPU checkpoint did not resume to the requested step")
        real_tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True)
        cases, contract = load_bundle(bundle, "development")
        examples = [{"prompt": planner_messages(contract, case.input.model_dump()),
            "completion": [{"role": "assistant", "content": case.expected.model_dump_json()}]} for case in cases]
        configuration.vocab_size = len(real_tokenizer)
        configuration.eos_token_id = real_tokenizer.eos_token_id
        configuration.pad_token_id = real_tokenizer.pad_token_id
        prepared = SFTTrainer(model=Qwen3ForCausalLM(configuration), processing_class=real_tokenizer,
            args=options, train_dataset=Dataset.from_list(examples))
        for index, case in enumerate(cases):
            row = prepared.train_dataset[index]
            expected_tokens = real_tokenizer.apply_chat_template(examples[index]["prompt"] + examples[index]["completion"], tokenize=True)
            prefix = real_tokenizer.apply_chat_template(examples[index]["prompt"], add_generation_prompt=True, tokenize=True)
            labels = prepared.data_collator([row])["labels"][0].tolist()
            if row["input_ids"] != expected_tokens or labels[:len(prefix)] != [-100] * len(prefix) or labels[len(prefix):] != expected_tokens[len(prefix):]:
                raise ValueError("The real training loss mask differs from the audited completion")
        return {"status": "passed", "model": "random_tiny_qwen3", "device": "cpu", "initial_steps": 1,
            "resumed_global_step": trainer.state.global_step,
            "completion_only_mask_checked": True, "checkpoint_reload_checked": True,
            "real_training_masks_checked": len(cases), "checkpoint_identity_checked": True,
            "finite_loss": True, "parameters": sum(p.numel() for p in trainer.model.parameters()),
            "packages": {name: version(name) for name in ("torch", "transformers", "trl", "peft", "datasets", "accelerate",
                "fsspec", "multiprocess", "dill", "pyarrow", "psutil")}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--cpu-smoke", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {**experiment_manifest(ROOT, "planner-local-preflight", {"bundle_sha256": sha256(args.bundle / "manifest.json")}),
              "tokenizer": token_probe(args.bundle, args.tokenizer), "model_requests": 0,
              "pretrained_weights_loaded": False, "gpu_training_executed": False}
    if args.cpu_smoke:
        report["cpu_smoke"] = cpu_probe(args.bundle, args.tokenizer)
        checked = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True)
        report["dependency_check"] = {"returncode": checked.returncode, "output": checked.stdout + checked.stderr}
        if checked.returncode:
            write_json(args.output, report)
            raise ValueError("The isolated CPU dependencies are inconsistent")
    write_json(args.output, report)
    print(json.dumps({"status": "passed", "output": str(args.output)}))
