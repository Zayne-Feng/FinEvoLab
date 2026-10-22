"""Coordinate checkpoint selection, sealed evaluation, verified export, and instance shutdown."""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import sha256
from finevolab.planner_benchmark import load_bundle
from finevolab.runtime import write_json

BUNDLE = ROOT / "data/training/planner_formal/frozen_v1"
EXPERIMENTS = ROOT / "project_notes/experiments"
EVALUATIONS = ROOT / "project_notes/evaluations"
REMOTE = "/workspace/FinEvoLab-formal-v1"
RUNTIME = "/workspace/finevolab-runtime/formal-v1"
SELECTION = EVALUATIONS / "planner_formal_selection.json"


def local(*arguments):
    subprocess.run([sys.executable, str(ROOT / "scripts/planner_benchmark.py"), *map(str, arguments)],
                   check=True, cwd=ROOT)


class Connection:
    def __init__(self, args):
        self.options = ["-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=15", "-i", str(args.key)]
        self.port = str(args.port)
        self.host = args.host

    def shell(self, script):
        encoded = base64.b64encode(script.encode()).decode()
        command = ["ssh", *self.options, "-p", self.port, self.host,
                   f"printf %s {encoded} | base64 -d | bash"]
        for attempt in range(3):
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode == 0:
                return result.stdout
            if attempt == 2:
                raise RuntimeError((result.stdout + result.stderr)[-2500:])
            time.sleep(5)

    def copy(self, source, destination):
        subprocess.run(["scp", *self.options, "-P", self.port, str(source), str(destination)], check=True)

    def wait(self, name):
        while True:
            status = self.shell(f"supervisorctl status {name} || true").strip()
            print(status, flush=True)
            if "EXITED" in status:
                return
            if not any(word in status for word in ("RUNNING", "STARTING")):
                raise RuntimeError(status)
            time.sleep(45)

    def export(self, phase, epoch=None):
        code = f'''from pathlib import Path
import hashlib,json,zipfile
root=Path({REMOTE!r})
paths=[]
for directory in sorted((root/'project_notes/experiments').glob('planner-formal-*')):
    if directory.name.endswith('-{phase}'):
        assert (directory/'summary.json').exists(), str(directory)
        paths += [p for p in directory.iterdir() if p.suffix in {{'.json','.jsonl'}}]
paths += list((root/'project_notes/run_logs').glob('planner_formal*.json'))
for seed in (17,29):
    training=root/f'output/training/formal-seed{{seed}}'
    paths += [training/name for name in ('manifest.json','training_result.json','environment.txt')]
    if {epoch!r} is not None:
        adapter=training/f'checkpoint-{{{epoch!r}*75}}'
        paths += [adapter/name for name in ('adapter_config.json','adapter_model.safetensors','training_identity.json')]
archive=Path({RUNTIME!r})/'{phase}_export.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for p in paths:
        z.write(p,p.relative_to(root))
print(json.dumps({{'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'files':len(paths)}}))
'''
        encoded = base64.b64encode(code.encode()).decode()
        output = self.shell(f"printf %s {encoded} | base64 -d | /workspace/finevolab-venv/bin/python")
        receipt = json.loads(output.strip().splitlines()[-1])
        destination = ROOT / f"output/cloud/planner_formal_{phase}_export.zip"
        self.copy(f"{self.host}:{RUNTIME}/{phase}_export.zip", destination)
        if sha256(destination) != receipt["sha256"]:
            raise ValueError("The downloaded archive hash differs")
        with ZipFile(destination) as archive:
            if archive.testzip() is not None:
                raise ValueError("The downloaded archive failed its CRC check")
            for name in archive.namelist():
                target = (ROOT / name).resolve()
                if not target.is_relative_to(ROOT) or Path(name).is_absolute():
                    raise ValueError("The archive has an invalid destination")
                value = archive.read(name)
                if target.exists() and hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(value).digest():
                    raise ValueError(f"A local artifact differs: {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(value)
        write_json(EVALUATIONS / f"planner_formal_{phase}_export.json", receipt)


def select():
    scores = []
    for epoch in (1, 2):
        summaries = [json.loads((EXPERIMENTS / f"planner-formal-lora{seed}-epoch{epoch}-validation/summary.json").read_text())
                     for seed in (17, 29)]
        if any(s["status"] != "completed" or s["attempted"] != 120 for s in summaries):
            raise ValueError("Validation is incomplete")
        scores.append({"epoch": epoch, "strict_successes": sum(s["first"]["successes"] for s in summaries),
                       "semantic_successes": sum(s["first"]["semantic_successes"] for s in summaries),
                       "denominator": 240})
    chosen = max(scores, key=lambda row: (row["strict_successes"], row["semantic_successes"], -row["epoch"]))
    epoch = chosen["epoch"]
    runs = [EXPERIMENTS / "planner-formal-deepseek-validation", EXPERIMENTS / "planner-formal-base-validation",
            EXPERIMENTS / f"planner-formal-lora17-epoch{epoch}-validation"]
    secondary = EXPERIMENTS / f"planner-formal-lora29-epoch{epoch}-validation"
    for seed, tuned in ((17, runs[-1]), (29, secondary)):
        local("compare", "--bundle", BUNDLE, "--split", "validation", "--runs", *runs[:2], tuned,
              "--output", EVALUATIONS / f"planner_formal_seed{seed}_validation.json")
    if not SELECTION.exists():
        local("seal", "--bundle", BUNDLE, "--runs", *runs, "--replicate-runs", secondary, "--output", SELECTION)
    write_json(EVALUATIONS / "planner_formal_selection_details.json", {"selected_epoch": epoch,
        "candidates": scores, "rule": "mean_strict_then_mean_semantic_then_earlier_epoch",
        "primary_seed": 17, "replicate_seed": 29, "test_used": False,
        "bundle_sha256": sha256(BUNDLE / "manifest.json"), "selection_sha256": sha256(SELECTION)})
    return epoch


def start_test(connection, epoch):
    connection.copy(SELECTION, f"{connection.host}:{RUNTIME}/selection.json")
    script = f'''#!/bin/bash
set -euo pipefail
export PYTHONUNBUFFERED=1 HF_HOME=/workspace/.hf_home HF_HUB_OFFLINE=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 HF_HUB_DISABLE_XET=1 OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false
cd {REMOTE}
PY=/workspace/finevolab-venv/bin/python
B=data/training/planner_formal/frozen_v1
$PY scripts/planner_benchmark.py predict --bundle "$B" --split test --arm qwen_base --backend transformers --repeats 1 --selection {RUNTIME}/selection.json --output project_notes/experiments/planner-formal-base-test
for seed in 17 29; do
  $PY scripts/planner_benchmark.py predict --bundle "$B" --split test --arm qwen_lora --backend transformers --adapter output/training/formal-seed${{seed}}/checkpoint-{epoch*75} --repeats 1 --selection {RUNTIME}/selection.json --output project_notes/experiments/planner-formal-lora${{seed}}-test
done
printf 'Test phase completed\\n'
'''
    encoded = base64.b64encode(script.encode()).decode()
    configuration = f'''[program:finevolab_formal_test]
command={RUNTIME}/test.sh
autostart=true
autorestart=false
startsecs=0
stdout_logfile={RUNTIME}/test.log
stderr_logfile={RUNTIME}/test_error.log
stdout_logfile_maxbytes=0
stderr_logfile_maxbytes=0
stopasgroup=true
killasgroup=true
'''
    config_encoded = base64.b64encode(configuration.encode()).decode()
    connection.shell(f"set -e\nprintf %s {encoded} | base64 -d > {RUNTIME}/test.sh\nchmod +x {RUNTIME}/test.sh\n"
        f"printf %s {config_encoded} | base64 -d > /etc/supervisor/conf.d/finevolab_formal_test.conf\n"
        "supervisorctl reread\nsupervisorctl update")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="root@163.142.222.112")
    parser.add_argument("--port", type=int, default=40045)
    parser.add_argument("--key", type=Path, default=Path.home() / ".ssh/id_vast_ed25519")
    args = parser.parse_args()
    load_bundle(BUNDLE, "validation")
    connection = Connection(args)
    connection.wait("finevolab_formal_validation")
    connection.export("validation")
    epoch = select()
    start_test(connection, epoch)
    with (ROOT / "project_notes/run_logs/planner_formal_api_test.log").open("a", encoding="utf-8") as log:
        api = subprocess.Popen([sys.executable, str(ROOT / "scripts/planner_benchmark.py"), "predict",
            "--bundle", str(BUNDLE), "--split", "test", "--arm", "deepseek", "--backend", "api",
            "--repeats", "1", "--selection", str(SELECTION), "--output",
            str(EXPERIMENTS / "planner-formal-deepseek-test")], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        connection.wait("finevolab_formal_test")
        connection.export("test", epoch)
        if api.wait() != 0:
            raise RuntimeError("The API test failed; inspect its retained log")
    for seed in (17, 29):
        local("compare", "--bundle", BUNDLE, "--split", "test", "--runs",
              EXPERIMENTS / "planner-formal-deepseek-test", EXPERIMENTS / "planner-formal-base-test",
              EXPERIMENTS / f"planner-formal-lora{seed}-test", "--output",
              EVALUATIONS / f"planner_formal_seed{seed}_test.json")
    code = '''import os,shutil,subprocess
key=os.environ['CONTAINER_API_KEY']
result=subprocess.run([shutil.which('vastai'),'stop','instance','57775489','--api-key',key],capture_output=True,text=True)
print((result.stdout+result.stderr).replace(key,'[REDACTED]'))
raise SystemExit(result.returncode)
'''
    encoded = base64.b64encode(code.encode()).decode()
    stopped = connection.shell(f"printf %s {encoded} | base64 -d | /workspace/finevolab-venv/bin/python")
    write_json(EVALUATIONS / "planner_formal_completion.json", {"status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(), "selected_epoch": epoch,
        "selection_sha256": sha256(SELECTION), "instance_stop_response": stopped,
        "test_exports_verified": True, "training_seeds": [17, 29]})
    print(json.dumps({"status": "completed", "instance_stop_requested": True}), flush=True)


if __name__ == "__main__":
    from filelock import FileLock
    with FileLock(str(ROOT / "output/cloud/planner_formal_driver.lock"), timeout=0):
        if (EVALUATIONS / "planner_formal_completion.json").exists():
            print(json.dumps({"status": "already_completed"}))
        else:
            main()
