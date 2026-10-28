"""Build a static showcase from pinned financial observations and experiment results."""

import argparse
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/Zayne-Feng/FinEvoLab"
DOMAIN = "finevolab.lunelu.com"


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def rounded(value, places=2):
    return str(Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def observation(reports, company, year, metric):
    for report in reports:
        if report["source"]["company"] != company or year not in report["source"].get("fiscal_years", [report["source"].get("fiscal_year")]):
            continue
        matches = [row for row in report["metrics"] if row["name"] == metric and row["fiscal_year"] == year]
        if len(matches) != 1:
            raise ValueError("A showcase observation must have one annual value")
        row = matches[0]
        evidence = next(item for item in report["evidence"] if item["id"] == row["evidence_id"])
        if row["currency"] != "USD" or row["unit"] != "USD" or not evidence["source_url"].startswith("https://"):
            raise ValueError("An observation requires USD units and an HTTPS source")
        return {"company": company, "year": year, "metric": metric, "value": row["value"],
            "billions": rounded(Decimal(row["value"]) / Decimal(10**9), 3),
            "period_start": row["period_start"], "period_end": row["period_end"],
            "source_url": evidence["source_url"], "evidence_id": evidence["id"], "locator": evidence["locator"]}
    raise ValueError("A required financial report is missing")


def build(output):
    source = ROOT / "showcase"
    output = output.resolve()
    if output == ROOT or output.is_relative_to(source.resolve()) or ROOT.is_relative_to(output):
        raise ValueError("The output must be separate from the source tree")
    scope = load("project_notes/experiments/scope-test-v1/assessment.json")
    planner = load("project_notes/evaluations/planner_formal_results.json")
    quant = load("project_notes/evaluations/planner_quantization.json")
    reports = load("data/processed/reports.json")["reports"]
    business_manifest = load("data/evaluation/scope_v3/manifest.json")
    planner_manifest = load("data/training/planner_formal/frozen_v1/manifest.json")
    verification = load("project_notes/run_logs/repository_distribution_checks.json")
    if not verification["workspace_tests"]["passed"]:
        raise ValueError("The regression record must contain a passing result")
    if planner["test_cases"] != planner_manifest["splits"]["test"]["cases"]:
        raise ValueError("Planner results must match the frozen test manifest")
    current = scope["groups"]["current"]
    previous = scope["groups"]["legacy"]
    if current["common"]["attempts"] != previous["common"]["attempts"]:
        raise ValueError("Paired business groups require the same denominator")
    if current["all"]["attempts"] % current["all"]["unique_questions"]:
        raise ValueError("Business repetitions require an integral task count")
    repetitions = current["all"]["attempts"] // current["all"]["unique_questions"]
    if current["common"]["attempts"] != current["common"]["unique_questions"] * repetitions:
        raise ValueError("Common tasks must use the same repetition count")
    seeds = planner["seeds"]
    arms = []
    for seed, arm, label in (("17", "qwen_base", "Qwen base"), ("17", "qwen_lora", "QLoRA · seed 17"),
                              ("29", "qwen_lora", "QLoRA · seed 29"), ("17", "deepseek", "DeepSeek")):
        group = seeds[seed]["arms"][arm]
        if group["cases"] != planner["test_cases"]:
            raise ValueError("Planner groups require the same test cases")
        arms.append({"label": label, "successes": group["strict_successes"], "total": group["cases"]})
    m23 = observation(reports, "MSFT", 2023, "revenue")
    m24 = observation(reports, "MSFT", 2024, "revenue")
    a23 = observation(reports, "AAPL", 2023, "revenue")
    operating = observation(reports, "MSFT", 2024, "operating_income")
    if (m24["period_start"], m24["period_end"]) != (operating["period_start"], operating["period_end"]):
        raise ValueError("Operating margin requires matching financial periods")
    growth = (Decimal(m24["value"]) / Decimal(m23["value"]) - 1) * 100
    margin = Decimal(operating["value"]) / Decimal(m24["value"]) * 100
    cases = {"growth": {"observations": [m23, m24], "result": rounded(growth),
                "formula": f"({m24['billions']} − {m23['billions']}) / {m23['billions']} × 100 = {rounded(growth)}%"},
             "comparison": {"observations": [a23, m23],
                "result": rounded((Decimal(a23["value"]) - Decimal(m23["value"])) / Decimal(10**9), 3),
                "periods_aligned": (a23["period_start"], a23["period_end"]) == (m23["period_start"], m23["period_end"])},
             "margin": {"observations": [m24, operating], "result": rounded(margin),
                "formula": f"{operating['billions']} / {m24['billions']} × 100 = {rounded(margin)}%"}}
    quant_groups = []
    for key, label in (("none", "BF16"), ("nf4", "NF4")):
        group = quant["groups"][key]
        quant_groups.append({"label": label, "successes": group["first"]["successes"], "total": quant["cases"],
            "memory": rounded(group["peak_allocated_gib"], 3), "latency": rounded(group["latency"]["mean"], 3)})
    payload = {"repository": REPOSITORY, "domain": DOMAIN, "cases": cases, "planner": arms,
        "business": {"total": current["all"]["attempts"], "successes": current["all"]["strict_successes"],
            "questions": current["all"]["unique_questions"], "repetitions": repetitions,
            "common_questions": current["common"]["unique_questions"], "common_total": current["common"]["attempts"],
            "common_before": previous["common"]["strict_successes"], "common_after": current["common"]["strict_successes"]},
        "splits": {"business": business_manifest["counts"],
            "planner": {name: split["cases"] for name, split in planner_manifest["splits"].items()}},
        "planner_template_clusters": planner["template_clusters"], "selected_epoch": planner["selection"]["selected_epoch"],
        "regression_tests": verification["workspace_tests"]["tests"],
        "quantization": quant_groups, "memory_reduction": rounded(quant["memory_reduction"] * 100),
        "latency_increase": rounded((quant["mean_latency_ratio"] - 1) * 100),
        "planner_gain": rounded(Decimal(arms[1]["successes"] - arms[0]["successes"]) / planner["test_cases"] * 100),
        "evidence": {"business": "project_notes/experiments/scope-test-v1/assessment.json",
            "planner": "project_notes/evaluations/planner_formal_results.json",
            "quantization": "project_notes/evaluations/planner_quantization.json"}}
    output.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "styles.css", "app.js", "favicon.svg"):
        shutil.copyfile(source / name, output / name)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    (output / "data.js").write_text("window.FINEVOLAB_SHOWCASE=" + encoded + ";\n", encoding="utf-8")
    (output / "CNAME").write_text(DOMAIN + "\n", encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    names = {item.name for item in output.iterdir()}
    expected = {"index.html", "styles.css", "app.js", "favicon.svg", "data.js", "CNAME", ".nojekyll"}
    if names != expected:
        raise ValueError("The publication directory contains unexpected files")
    print(json.dumps({"status": "built", "files": sorted(names), "model_calls": 0, "domain": DOMAIN}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "output/showcase")
    build(parser.parse_args().output)
