"""Score structured tasks and mechanically verify passage-grounded submissions."""

from pathlib import Path

from finevolab.evaluation import score_task
from finevolab.grounded import validate_explanation


def assess(root: Path, case: dict, output: dict) -> dict:
    if case["category"] != "explanation":
        return score_task(case, output)
    validation = validate_explanation(root, output)
    expected = case["expected"]
    plan = output["request"]
    checks = {"source_integrity": validation["source_integrity"],
              "request_scope": case["company"] in plan["companies"] and plan["fiscal_year"] == case["fiscal_year"],
              "topic": plan["topic"] == expected["topic"],
              "nonempty_claims": output["answer"]["status"] == "complete" and bool(output["answer"]["claims"])}
    return {"score": float(all(checks.values())), "checks": checks,
            "score_definition": "mechanical_source_and_scope_not_semantic_support"}
