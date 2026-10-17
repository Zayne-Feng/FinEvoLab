"""Freeze grouped planner datasets and score paired, source-independent predictions."""

from collections import Counter
import json
from pathlib import Path
import re
import statistics
import unicodedata
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from finevolab.experiments import sha256
from finevolab.grounded import RequestPlan, request_instruction
from finevolab.runtime import fingerprint, write_json
from finevolab.sdk_runtime import json_messages


SPLITS = ("development", "validation", "test")
ARMS = ("deepseek", "qwen_base", "qwen_lora")
CATEGORIES = {"metric", "growth", "comparison", "alignment", "summary", "explanation", "hybrid", "boundary", "selected_document"}


def coverage(cases: list) -> dict:
    categories = Counter(case.category for case in cases)
    return {"cases": len(cases), "groups": len({case.group_id for case in cases}),
        "categories": dict(categories), "missing_categories": sorted(CATEGORIES - set(categories)),
        "intents": dict(Counter(case.expected.intent for case in cases)),
        "languages": dict(Counter(case.language for case in cases))}


def experiment_manifest(root: Path, run_id: str, config: dict) -> dict:
    from finevolab.experiments import manifest
    if (root / "source_revision.json").exists():
        source = json.loads((root / "source_revision.json").read_text(encoding="utf-8"))
        for name, digest in source["files"].items():
            path = (root / name).resolve()
            if not path.is_relative_to(root.resolve()) or sha256(path) != digest:
                raise ValueError("The exported source snapshot changed")
        return {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
            "commit": source["commit"], "worktree_status": source["worktree_status"],
            "source_hashes": source["files"], "config": config, "config_sha256": fingerprint(config),
            "source_snapshot_sha256": sha256(root / "source_revision.json")}
    return manifest(root, run_id, config)


class SelectedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    company: str = Field(min_length=1)
    report_year: int = Field(ge=1900, le=2200)
    id: str = Field(min_length=1)


class PlannerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: str = Field(min_length=1)
    selected_documents: list[SelectedDocument] = Field(default_factory=list, max_length=5)


class PlannerCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    group_id: str = Field(min_length=1)
    split: str
    category: str = Field(min_length=1)
    language: str = Field(min_length=1)
    input: PlannerInput
    expected: RequestPlan
    semantic_fields: list[str] | None = None


def normalize_question(question: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", question).casefold())


def validate_cases(rows: list[dict]) -> list[PlannerCase]:
    for row in rows:
        RequestPlan.model_validate(row["expected"], strict=True)
    cases = [PlannerCase.model_validate(row) for row in rows]
    if not cases or {case.split for case in cases} != set(SPLITS):
        raise ValueError("All three nonempty splits are required")
    identifiers, questions, groups = set(), set(), {}
    for case in cases:
        if case.semantic_fields is not None and (not case.semantic_fields or
            len(set(case.semantic_fields)) != len(case.semantic_fields) or
            not set(case.semantic_fields) <= set(RequestPlan.model_fields)):
            raise ValueError("Semantic fields must name distinct planner fields")
        normalized = normalize_question(case.input.question)
        identity = (normalized, fingerprint(case.input.model_dump()["selected_documents"]))
        if not normalized or case.id in identifiers or identity in questions:
            raise ValueError("Duplicate case IDs or normalized model inputs")
        if case.group_id in groups and groups[case.group_id] != case.split:
            raise ValueError("A paraphrase group crosses dataset splits")
        identifiers.add(case.id)
        questions.add(identity)
        groups[case.group_id] = case.split
    return cases


def freeze_bundle(source: Path, directory: Path, candidate: dict, aliases: dict) -> dict:
    document = json.loads(source.read_text(encoding="utf-8"))
    cases = validate_cases(document["cases"])
    documents = {item["id"]: item for item in document.get("documents", [])}
    if len(documents) != len(document.get("documents", [])):
        raise ValueError("Duplicate source document identities")
    for case in cases:
        for selected in case.input.selected_documents:
            actual = documents.get(selected.id, {})
            if any(actual.get(key) != value for key, value in selected.model_dump().items()):
                raise ValueError("Selected document metadata differs from the source catalogue")
    if directory.exists():
        raise ValueError("The dataset directory already exists; use a new version")
    directory.mkdir(parents=True)
    write_json(directory / "contract.json", {
        "instruction": request_instruction(candidate), "schema": RequestPlan.model_json_schema(), "aliases": aliases})
    write_json(directory / "documents.json", document.get("documents", []))
    for split in SPLITS:
        subset = [case for case in cases if case.split == split]
        write_json(directory / f"{split}.json", {"cases": [case.model_dump() for case in subset]})
        with (directory / f"{split}_inputs.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for case in subset:
                handle.write(json.dumps({"case_id": case.id, "input": case.input.model_dump()}, ensure_ascii=False) + "\n")
    with (directory / "train.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            if case.split == "development":
                messages = json_messages(RequestPlan, request_instruction(candidate), case.input.model_dump())
                handle.write(json.dumps({"prompt": messages, "completion": [{"role": "assistant",
                    "content": case.expected.model_dump_json()}]}, ensure_ascii=False) + "\n")
    frozen = {"version": 1, "dataset_version": document.get("version"), "source_sha256": sha256(source), "source_role": document.get("source_role", "authored_synthetic_planner_pilot"),
        "splits": {split: {"cases": sum(c.split == split for c in cases),
            "groups": len({c.group_id for c in cases if c.split == split})} for split in SPLITS},
        "files": {p.name: sha256(p) for p in sorted(directory.iterdir())}}
    write_json(directory / "manifest.json", frozen)
    return frozen


def load_bundle(directory: Path, split: str) -> tuple[list[PlannerCase], dict]:
    if split not in SPLITS:
        raise ValueError("Unknown split")
    frozen = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in frozen["files"].items():
        if Path(name).name != name or sha256(directory / name) != digest:
            raise ValueError("A frozen dataset file changed")
    contract = json.loads((directory / "contract.json").read_text(encoding="utf-8"))
    if contract["schema"] != RequestPlan.model_json_schema():
        raise ValueError("The planner schema changed")
    all_rows = [row for name in SPLITS for row in json.loads(
        (directory / f"{name}.json").read_text(encoding="utf-8"))["cases"]]
    cases = validate_cases(all_rows)
    return [case for case in cases if case.split == split], contract


def planner_messages(contract: dict, payload: dict) -> list[dict]:
    parsed = PlannerInput.model_validate(payload)
    return [{"role": "system", "content": contract["instruction"] +
             "\nReturn a JSON data object. Schema: " + json.dumps(contract["schema"])},
            {"role": "user", "content": json.dumps(parsed.model_dump(), ensure_ascii=False)}]


def json_object(raw: str) -> dict:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON object keys")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError("A JSON object is required")
    return value


def parsed_plan(raw: str) -> RequestPlan:
    return RequestPlan.model_validate(json_object(raw), strict=True)


def scored_fields(plan: RequestPlan) -> list[str]:
    fields = ["intent", "companies", "fiscal_year", "scope"]
    if plan.intent not in {"clarification", "invalid", "unavailable"}:
        fields.append("report_year")
        if plan.topic != "risk" or plan.intent not in {"explanation", "hybrid"}:
            fields.append("metrics")
    if plan.intent in {"explanation", "hybrid"}:
        fields.append("topic")
    if plan.intent == "hybrid":
        fields.append("numeric_intent")
    return fields


def canonical_plan(plan: RequestPlan, aliases: dict) -> dict:
    result = plan.model_dump()
    def company(value):
        normalized = value.strip().rstrip(".").casefold()
        return aliases.get(normalized, normalized.upper())
    result["companies"] = sorted(company(value) for value in plan.companies)
    result["metrics"] = sorted(plan.metrics)
    result["report_year"] = plan.report_year or plan.fiscal_year
    return result


def score_prediction(case: PlannerCase, raw: str, aliases: dict) -> dict:
    fields = case.semantic_fields or scored_fields(case.expected)
    result = {"json_valid": False, "schema_valid": False, "score": 0,
              "semantic_score": 0, "semantic_fields": {field: False for field in fields},
              "fields": {field: False for field in fields}}
    try:
        value = json_object(raw)
        result["json_valid"] = True
        expected = canonical_plan(case.expected, aliases)
        actual = dict(value)
        companies = actual.get("companies")
        if isinstance(companies, list) and all(isinstance(c, str) for c in companies):
            actual["companies"] = sorted(aliases.get(c.strip().rstrip(".").casefold(),
                c.strip().rstrip(".").upper()) for c in companies)
        if isinstance(actual.get("metrics"), list) and all(isinstance(m, str) for m in actual["metrics"]):
            actual["metrics"] = sorted(actual["metrics"])
        if actual.get("report_year") is None:
            actual["report_year"] = actual.get("fiscal_year")
        result["semantic_fields"] = {field: type(actual.get(field)) is type(expected[field]) and
            actual.get(field) == expected[field] for field in fields}
        result["semantic_score"] = int(all(result["semantic_fields"].values()))
        predicted = parsed_plan(raw)
    except (ValueError, TypeError):
        return result
    result["schema_valid"] = True
    actual = canonical_plan(predicted, aliases)
    result["fields"] = {field: actual[field] == expected[field] for field in fields}
    result["score"] = int(all(result["fields"].values()))
    return result


def validate_prediction_rows(rows: list[dict], cases: list[PlannerCase], repeats: int,
                             require_complete: bool = True, max_repairs: int = 1) -> dict:
    if repeats < 1:
        raise ValueError("Repetitions must be positive")
    expected = {(case.id, repeat) for case in cases for repeat in range(repeats)}
    inputs = {case.id: fingerprint(case.input.model_dump()) for case in cases}
    found = {}
    for row in rows:
        key = (row["case_id"], row["repeat"])
        if type(row["repeat"]) is not int or key not in expected or key in found:
            raise ValueError("Duplicate or unexpected prediction identity")
        attempts = row.get("attempts", [])
        if not attempts or len(attempts) > max_repairs + 1 or any(not isinstance(a.get("raw"), str) for a in attempts):
            raise ValueError("One original attempt and at most one repair are required")
        if row.get("input_sha256") != inputs[row["case_id"]]:
            raise ValueError("A prediction used a different model input")
        if len(attempts) == 2:
            if attempts[0].get("error_type"):
                raise ValueError("Transport errors cannot trigger format repairs")
            try:
                parsed_plan(attempts[0]["raw"])
            except ValueError:
                pass
            else:
                raise ValueError("A schema-valid output cannot trigger a format repair")
        if any(type(a.get("elapsed_seconds")) not in {int, float} or
               not 0 <= a["elapsed_seconds"] < float("inf") for a in attempts):
            raise ValueError("Invalid attempt latency")
        for attempt in attempts:
            usage = attempt.get("usage")
            if usage is not None and (any(type(usage.get(field)) is not int or usage[field] < 0 for field in
                ("requests", "input_tokens", "output_tokens", "total_tokens")) or usage["requests"] != 1 or
                usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]):
                raise ValueError("Invalid attempt token usage")
        found[key] = row
    if require_complete and set(found) != expected:
        raise ValueError("A complete paired prediction grid is required")
    return found


def summarize_predictions(cases: list[PlannerCase], rows: list[dict], aliases: dict) -> dict:
    indexed = {case.id: case for case in cases}
    evaluated = [{"case": indexed[row["case_id"]], "row": row,
        "first": score_prediction(indexed[row["case_id"]], row["attempts"][0]["raw"], aliases),
        "final": score_prediction(indexed[row["case_id"]], row["attempts"][-1]["raw"], aliases)} for row in rows]
    result = {"attempted": len(rows), "repairs": sum(len(row["attempts"]) > 1 for row in rows)}
    for phase in ("first", "final"):
        values = [item[phase] for item in evaluated]
        result[phase] = {"successes": sum(value["score"] for value in values),
            "semantic_successes": sum(value["semantic_score"] for value in values),
            "semantic_success_rate": statistics.mean(value["semantic_score"] for value in values) if values else None,
            "success_rate": statistics.mean(value["score"] for value in values) if values else None,
            "json_valid": sum(value["json_valid"] for value in values),
            "schema_valid": sum(value["schema_valid"] for value in values),
            "fields": {field: {"correct": sum(value["fields"].get(field, False) for value in values),
                "eligible": sum(field in value["fields"] for value in values)}
                for field in sorted({field for value in values for field in value["fields"]})},
            "categories": {category: {"attempts": sum(item["case"].category == category for item in evaluated),
                "successes": sum(item[phase]["score"] for item in evaluated if item["case"].category == category),
                "semantic_successes": sum(item[phase]["semantic_score"] for item in evaluated if item["case"].category == category)}
                for category in sorted({item["case"].category for item in evaluated})}}
    attempts = [attempt for row in rows for attempt in row["attempts"]]
    known = [attempt["usage"] for attempt in attempts if attempt.get("usage") is not None]
    result.update(known_tokens=sum(item["total_tokens"] for item in known),
        unknown_usage_attempts=len(attempts) - len(known), model_attempts=len(attempts),
        mean_total_seconds=statistics.mean(sum(a["elapsed_seconds"] for a in row["attempts"]) for row in rows) if rows else None,
        errors=dict(Counter(a["error_type"] for a in attempts if a.get("error_type"))),
        peak_allocated_bytes=max((a.get("peak_allocated_bytes") or 0 for a in attempts), default=0) or None)
    return result


def paired_report(cases: list[PlannerCase], left: list[dict], right: list[dict], aliases: dict,
                  repeats: int, phase: str = "first", metric: str = "score") -> dict:
    from scripts.estimate_paired_uncertainty import estimate
    if phase not in {"first", "final"} or metric not in {"score", "semantic_score"}:
        raise ValueError("Unknown scoring phase")
    left_map = validate_prediction_rows(left, cases, repeats)
    right_map = validate_prediction_rows(right, cases, repeats)
    index = 0 if phase == "first" else -1
    pairs = []
    for case in cases:
        for repeat in range(repeats):
            key = (case.id, repeat)
            lscore = score_prediction(case, left_map[key]["attempts"][index]["raw"], aliases)[metric]
            rscore = score_prediction(case, right_map[key]["attempts"][index]["raw"], aliases)[metric]
            pairs.append({"case_id": case.id, "group_id": case.group_id, "repeat": repeat,
                          "left": lscore, "right": rscore, "difference": rscore - lscore})
    grouped = {}
    for pair in pairs:
        grouped.setdefault(pair["group_id"], []).append(pair["difference"])
    cluster_pairs = [{"case_id": group, "difference": statistics.mean(values)} for group, values in grouped.items()]
    uncertainty = estimate({"complete_pairing": True, "observed_pairs": len(cluster_pairs),
        "expected_pairs": len(cluster_pairs), "left_run": "left", "right_run": "right", "pairs": cluster_pairs})
    return {"phase": phase, "metric": metric, "complete_pairing": True, "pairs": pairs,
        "wins": sum(p["difference"] > 0 for p in pairs), "losses": sum(p["difference"] < 0 for p in pairs),
        "ties": sum(p["difference"] == 0 for p in pairs),
        "execution_weighted_difference": statistics.mean(p["difference"] for p in pairs),
        "group_weighted_uncertainty": uncertainty}
