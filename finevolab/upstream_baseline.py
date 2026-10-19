"""Execute pinned upstream report components with a snapshot input adapter."""

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

from agents import ModelSettings, OpenAIChatCompletionsModel, RunConfig, Runner
import pandas as pd

from finevolab.provider import async_client
from finevolab.sdk_runtime import ResponseHooks


UPSTREAM = "2717499b8e30f242640af08c4ad9afd1113c2d45"


def archived_module(root: Path, name: str, path: str, commit=UPSTREAM):
    source = subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=root)
    module = types.ModuleType(name)
    module.__file__ = str(root / path)
    module.__package__ = name.rpartition(".")[0]
    sys.modules[name] = module
    exec(compile(source.decode("utf-8"), module.__file__, "exec"), module.__dict__)
    return module, hashlib.sha256(source).hexdigest()


def report_components(root):
    package = "_finrobot_v1_sections"
    if package not in sys.modules:
        module = types.ModuleType(package)
        module.__path__ = []
        sys.modules[package] = module
    hashes = {}
    for section in ("tagline", "company_overview", "investment_overview", "valuation_overview",
                    "risks", "competitor_analysis", "major_takeaways", "news_summary"):
        path = f"finrobot_equity/core/src/modules/equity_agents/{section}_agent.py"
        _, hashes[path] = archived_module(root, f"{package}.{section}_agent", path)
    path = "finrobot_equity/core/src/modules/equity_agents/agent_manager.py"
    manager, hashes[path] = archived_module(root, package + ".agent_manager", path)
    path = "finrobot_equity/core/src/modules/financial_data_processor.py"
    processor, hashes[path] = archived_module(root, "_finrobot_v1_processor", path)
    return manager.EquityResearchAgentManager(), processor, hashes


def snapshot_rows(store, company, year):
    values = []
    for fiscal in (year - 1, year):
        metrics = {name: store.get_metric(company, name, fiscal) for name in
                   ("revenue", "operating_income", "net_income")}
        values.append({"date": pd.Timestamp(metrics["revenue"].period_end), "year": fiscal,
            "revenue": float(metrics["revenue"].value), "operatingIncome": float(metrics["operating_income"].value),
            "netIncome": float(metrics["net_income"].value)})
    return pd.DataFrame(values)


async def analyze_upstream(root, settings, store, company, year):
    manager, processor, hashes = report_components(root)
    raw = snapshot_rows(store, company, year)
    table = processor.extract_historical_metrics_from_api_data({"income_statement": raw})
    if table is None or table.empty:
        raise ValueError("The original upstream processor returned no report table")
    def cell(metric, fiscal):
        return table.loc[table["metrics"] == metric, f"{fiscal}A"].iloc[0]
    normalized = {"revenue_current": str(Decimal(str(cell("Revenue", year)))),
        "revenue_previous": str(Decimal(str(cell("Revenue", year - 1)))),
        "growth_percent": str(Decimal(str(cell("Revenue Growth", year)).rstrip("%")))}
    section = manager.agents["major_takeaways"]
    schema = section.output_type
    instructions = section.instructions + "\nReturn a JSON data object with the single string field takeaways."
    prompt = manager._prepare_financial_data_prompt({"financial_metrics": table}, company, company)
    usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    def recorded(response):
        for name in usage:
            usage[name] += getattr(response.usage, name)
    async with async_client(settings) as client:
        model = OpenAIChatCompletionsModel(settings.model, client)
        agent = section.clone(model=model, instructions=instructions, output_type=None,
            model_settings=ModelSettings(temperature=0, max_tokens=1600,
                extra_body={**settings.extra_body, "response_format": {"type": "json_object"}}))
        result = await Runner.run(agent, prompt, max_turns=1, hooks=ResponseHooks(recorded),
            run_config=RunConfig(model=model, tracing_disabled=True, trace_include_sensitive_data=False))
    narrative = schema.model_validate_json(str(result.final_output)).takeaways
    return {"normalized": normalized, "section": narrative,
        "report_table": json.loads(table.where(pd.notna(table), None).to_json(orient="records")),
        "usage": usage, "source_hashes": hashes, "upstream_commit": UPSTREAM,
        "prompt_sha256": hashlib.sha256((instructions + prompt).encode()).hexdigest(),
        "identity": "Pinned V1 processor and original major_takeaways instructions with snapshot/transport adapters"}
