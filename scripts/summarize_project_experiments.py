"""Inventory experiment artifacts and reconcile canonical usage ledgers."""

from collections import Counter, defaultdict
from decimal import Decimal
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from finevolab.experiments import manifest, sha256
from finevolab.runtime import write_json
from finevolab.tools import FinancialStore


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def records(path):
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def inventory():
    runs = []
    inventory_path = ROOT / "project_notes/evaluations/experiment_inventory.json"
    run_order = {row["run_id"]: index for index, row in enumerate(read(inventory_path).get("runs", []))} if inventory_path.is_file() else {}
    for area in ("experiments", "optimization", "evaluations"):
        for directory in sorted((ROOT / "project_notes" / area).iterdir()):
            if not directory.is_dir():
                continue
            ledgers = sorted(directory.glob("*.jsonl"))
            manifest_path, summary_path = directory / "manifest.json", directory / "summary.json"
            if not ledgers and not manifest_path.exists():
                continue
            metadata = read(manifest_path) if manifest_path.exists() else {}
            summary = read(summary_path) if summary_path.exists() else {}
            summary = summary if isinstance(summary, dict) else {}
            primary_name = "tasks.jsonl" if area == "experiments" else "evaluations.jsonl" if area == "optimization" else "requests.jsonl"
            primary = directory / primary_name
            counts, digests = {}, {}
            for path in ledgers:
                counts[path.name] = sum(1 for _ in records(path))
                digests[path.relative_to(ROOT).as_posix()] = sha256(path)
            cases, statuses, arms = set(), Counter(), defaultdict(lambda: {"records": 0, "scored_records": 0, "sum_scores": 0})
            if primary.exists():
                for row in records(primary):
                    case = row.get("case", {})
                    identifier = row.get("case_id") or (case.get("id") if isinstance(case, dict) else case)
                    if identifier:
                        cases.add(str(identifier))
                    statuses[str(row.get("execution_status", row.get("status", "unspecified")))] += 1
                    arm = str(row.get("arm", row.get("candidate", row.get("variant", "unspecified"))))
                    arms[arm]["records"] += 1
                    if isinstance(row.get("score"), (int, float)):
                        arms[arm]["scored_records"] += 1
                        arms[arm]["sum_scores"] += row["score"]
            for path in (manifest_path, summary_path, directory / "assessment.json", directory / "invalidated.json"):
                if path.exists():
                    digests[path.relative_to(ROOT).as_posix()] = sha256(path)
            config = metadata.get("config", {})
            runs.append({"area": area, "run_id": directory.name,
                "created_at": metadata.get("created_at"), "code_commit": metadata.get("commit"),
                "summary_status": summary.get("status"), "invalidated": (directory / "invalidated.json").exists(),
                "dataset_path": config.get("dataset_path", config.get("dataset_dir")),
                "ledger_records": counts, "primary_ledger": primary_name if primary.exists() else None,
                "unique_recorded_case_ids": len(cases) if cases else None,
                "execution_status_counts": dict(statuses), "arms": dict(arms),
                "summary_path": summary_path.relative_to(ROOT).as_posix() if summary_path.exists() else None,
                "assessment_path": (directory / "assessment.json").relative_to(ROOT).as_posix()
                    if (directory / "assessment.json").exists() else None,
                "source_sha256": digests})
    def chronological(row):
        try:
            return (datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")).timestamp(), row["run_id"])
        except (AttributeError, TypeError, ValueError):
            if row["run_id"] in run_order:
                return (run_order[row["run_id"]], row["run_id"])
            try:
                stamp = datetime.strptime(row["run_id"][:8], "%Y%m%d").replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                stamp = float("inf")
            return (stamp, row["run_id"])
    return sorted(runs, key=chronological)


def usage():
    entries, fingerprints, source_hashes, unknown = [], {}, {}, {}
    for name in ("project_costs.json", "followup_costs.json"):
        path = ROOT / "project_notes/evaluations" / name
        ledger = read(path)
        source_hashes[path.relative_to(ROOT).as_posix()] = sha256(path)
        unknown[name] = ledger.get("unknown_usage")
        for entry in ledger["entries"]:
            identity = (entry["source"], str(entry["identity"]))
            value = (entry["model"], entry["usage"])
            if identity in fingerprints:
                if fingerprints[identity] != value:
                    raise ValueError("Canonical usage identities disagree")
                continue
            fingerprints[identity] = value
            entries.append(entry)
    models, groups = {}, {}
    for key, destination in (("model", models), ("group", groups)):
        for entry in entries:
            name = entry[key]
            target = destination.setdefault(name, {"entries": 0, "requests": 0, "input_tokens": 0,
                "output_tokens": 0, "total_tokens": 0, "priced_entries": 0,
                "estimated_usd_lower": Decimal(0), "estimated_usd_upper": Decimal(0)})
            target["entries"] += 1
            for field in ("requests", "input_tokens", "output_tokens", "total_tokens"):
                target[field] += entry["usage"][field]
            if entry.get("estimated_cost") is not None:
                target["priced_entries"] += 1
                target["estimated_usd_lower"] += Decimal(entry["estimated_cost"]["lower"])
                target["estimated_usd_upper"] += Decimal(entry["estimated_cost"]["upper"])
        for target in destination.values():
            target["pricing_status"] = ("priced" if target["priced_entries"] == target["entries"] else
                                        "unpriced" if not target["priced_entries"] else "partially_priced")
            for field in ("estimated_usd_lower", "estimated_usd_upper"):
                target[field] = str(target[field])
    return {"canonical_entries": len(entries), "models": models, "groups": groups,
        "known_requests": sum(entry["usage"]["requests"] for entry in entries),
        "known_tokens": sum(entry["usage"]["total_tokens"] for entry in entries),
        "known_priced_usd_lower": str(sum(Decimal(row["estimated_usd_lower"]) for row in models.values())),
        "known_priced_usd_upper": str(sum(Decimal(row["estimated_usd_upper"]) for row in models.values())),
        "unknown_usage": unknown, "invoice_cost": None, "source_sha256": source_hashes,
        "deduplication": "source path and canonical record identity; aggregate ledgers retained as authoritative"}


def datasets():
    result, identifiers, questions = [], set(), set()
    for path in sorted((ROOT / "data/evaluation").rglob("*.json")):
        data = read(path)
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            continue
        tasks = data["tasks"]
        result.append({"path": path.relative_to(ROOT).as_posix(), "records": len(tasks), "sha256": sha256(path)})
        for task in tasks:
            if not isinstance(task, dict):
                continue
            if task.get("id"):
                identifiers.add(task["id"])
            if task.get("question"):
                questions.add(" ".join(task["question"].split()))
    return {"split_files": result, "unique_task_ids": len(identifiers), "exact_question_strings": len(questions),
        "semantic_independence_claimed": False}


def main():
    for name in ("renewed-planner-test-v3", "renewed-regression-v1"):
        if not (ROOT / "project_notes/experiments" / name / "assessment.json").exists():
            raise ValueError("The final experiment assessments are incomplete")
    runs = inventory()
    verifications = []
    ignored = set(subprocess.check_output(["git", "ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", "project_notes/run_logs"], cwd=ROOT).decode().split("\0"))
    for path in sorted((ROOT / "project_notes/run_logs").glob("*.json")):
        if path.relative_to(ROOT).as_posix() in ignored:
            continue
        record = read(path)
        record = record if isinstance(record, dict) else {}
        verifications.append({"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path),
            "run_id": record.get("run_id"), "status": record.get("status"),
            "created_at": record.get("created_at"), "code_commit": record.get("commit", record.get("code_commit"))})
    store = FinancialStore(ROOT / "data/processed/reports.json")
    facts = {(metric.company, metric.name, metric.fiscal_year) for metric in store.metrics}
    source_counts = {}
    for name in ("filing_sources.json", "acceptance_sources.json"):
        data = read(ROOT / "data" / name)
        source_counts[name] = len(data) if isinstance(data, list) else len(data.get("sources", []))
    result = {**manifest(ROOT, "project-experiment-inventory", {"calculator": sha256(Path(__file__))}),
        "status": "completed", "runs": runs, "verification_records": verifications,
        "directory_counts": dict(Counter(row["area"] for row in runs)),
        "recorded_task_rows": sum(row["ledger_records"].get("tasks.jsonl", 0) for row in runs if row["area"] == "experiments"),
        "recorded_optimization_evaluations": sum(row["ledger_records"].get("evaluations.jsonl", 0) for row in runs if row["area"] == "optimization"),
        "recorded_reflection_attempts": sum(sum(record.get("status") != "started" for record in records(
            ROOT / "project_notes/optimization" / row["run_id"] / "reflections.jsonl"))
            for row in runs if row["area"] == "optimization" and "reflections.jsonl" in row["ledger_records"]),
        "recorded_evaluation_response_rows": sum(row["ledger_records"].get("requests.jsonl", 0) for row in runs if row["area"] == "evaluations"),
        "invalidated_run_ids": [row["run_id"] for row in runs if row["invalidated"]],
        "invalidation_marker_policy": "Only explicit invalidated.json markers; superseded execution statuses remain unchanged",
        "datasets": datasets(), "usage": usage(), "financial_data": {"metric_records": len(store.metrics),
            "unique_fact_ids": len(facts), "main_2022_2024_fact_ids": sum(2022 <= identity[2] <= 2024 for identity in facts),
            "source_catalogue_records": source_counts},
        "counting_policy": "Execution rows, optimization evaluations and review responses are distinct; do not sum them as independent questions"}
    write_json(ROOT / "project_notes/evaluations/experiment_inventory.json", result)
    print(json.dumps({key: result[key] for key in ("directory_counts", "recorded_task_rows", "recorded_optimization_evaluations",
        "recorded_evaluation_response_rows", "financial_data")}))
    print(json.dumps({key: result["usage"][key] for key in ("canonical_entries", "known_requests", "known_tokens",
        "known_priced_usd_lower", "known_priced_usd_upper")}))


if __name__ == "__main__":
    main()
