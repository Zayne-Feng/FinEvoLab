"""Resolve bounded financial context without persisting generated financial values."""

import json
import re
import time

from pydantic import BaseModel, ConfigDict
from typing import Literal


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["zh", "en"] = "zh"
    amount_scale: Literal["units", "millions", "billions"] = "units"


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS business_context (
            owner TEXT, conversation TEXT, revision INTEGER, context TEXT,
            source TEXT, expires REAL, task_id TEXT, PRIMARY KEY(owner, conversation));
        CREATE TABLE IF NOT EXISTS business_preferences (owner TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS memory_epochs (owner TEXT PRIMARY KEY, epoch INTEGER);
    """)


def snapshot(db, owner, conversation, source, enabled=True):
    pref = db.execute("SELECT value FROM business_preferences WHERE owner=?", (owner,)).fetchone()
    epoch = db.execute('SELECT epoch FROM memory_epochs WHERE owner=?', (owner,)).fetchone()
    result = {"conversation": conversation, "revision": 0, "epoch": epoch[0] if epoch else 0, "context": {}, "source": source,
              "preferences": json.loads(pref[0]) if pref else Preferences().model_dump(), "enabled": enabled}
    if conversation and enabled:
        row = db.execute("SELECT revision,context,source,expires FROM business_context WHERE owner=? AND conversation=?",
                         (owner, conversation)).fetchone()
        if row:
            result["revision"] = row[0]
            if row[2] == source and row[3] > time.time():
                result["context"] = json.loads(row[1])
    return result


def resolve(question, memory):
    context = memory.get("context", {}) if memory.get("enabled") else {}
    if not context or not re.search(r"上一年|上年|去年|那|它|再|相同|previous year|last year|\bit\b|\bthat\b|\bsame\b|\bagain\b", question, re.I):
        return question
    companies = context.get("companies", [])
    if not companies or len(companies) != 1:
        return question + "\nPlease clarify the company for this follow-up."
    year = context["fiscal_year"]
    explicit = re.search(r"(?<!\d)(19\d{2}|20\d{2}|21\d{2})(?!\d)", question)
    if re.search(r"上一年|上年|去年|previous year|last year", question, re.I) and not explicit:
        if re.search(r"比较|同比|compare|growth", question, re.I):
            return (f"{question}\nCalculate annual percentage growth for fiscal year {year}. "
                    f"Use {companies[0]} and metrics {', '.join(context['metrics'])} unless explicitly overridden above.")
        year -= 1
        question = re.sub(r"上一年|上年|去年|previous year|last year", f"fiscal year {year}", question, flags=re.I)
    elif explicit:
        year = int(explicit[1])
    return (f"{question}\nMissing-field context only: company {companies[0]}, fiscal year {year}, "
            f"metrics {', '.join(context['metrics'])}. Explicit fields in the question take precedence.")


def remember(db, owner, memory, state, ttl=86400):
    if not memory.get("enabled") or not memory.get("conversation"):
        return False
    epoch = db.execute('SELECT epoch FROM memory_epochs WHERE owner=?', (owner,)).fetchone()
    if memory.get('epoch', 0) != (epoch[0] if epoch else 0):
        return False
    from finevolab.grounded import RequestPlan
    phases = state.get("phases", {})
    result = phases.get("analyze", {}).get("result", {})
    if state.get("status") != "complete" or phases.get("validate", {}).get("result", {}).get("status") != "passed":
        return False
    try:
        plan = RequestPlan.model_validate(result.get("request")).model_dump()
    except ValueError:
        return False
    if plan["intent"] in {"invalid", "clarification", "unavailable"}:
        return False
    row = db.execute("SELECT revision FROM business_context WHERE owner=? AND conversation=?",
                     (owner, memory["conversation"])).fetchone()
    if (row[0] if row else 0) != memory["revision"]:
        return False
    db.execute("INSERT OR REPLACE INTO business_context VALUES (?,?,?,?,?,?,?)",
               (owner, memory["conversation"], memory["revision"] + 1, json.dumps(plan),
                memory["source"], time.time() + ttl, state["task_id"]))
    return True
