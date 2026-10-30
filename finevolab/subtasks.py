"""Persist validated model steps and cumulative task budget consumption."""

from pathlib import Path

from finevolab.runtime import BudgetExceeded, fingerprint, read_json, write_json


class SubtaskJournal:
    def __init__(self, root: Path, identity: dict, budget):
        self.root = root / fingerprint(identity)
        self.path = self.root / "usage.json"
        self.budget = budget
        self.usage = read_json(self.path) if self.path.exists() else {"calls": 0, "tokens": 0, "tools": 0, "pending": [], "repairs": 0}

    def load(self, key, schema, validate=None):
        path = self.root / (key + ".json")
        if not path.exists():
            return None
        try:
            record = read_json(path)
            if record["key"] != key or fingerprint({"result": record["result"]}) != record["checksum"]:
                return None
            result = schema.model_validate(record["result"])
            if validate:
                validate(result)
            return result
        except (ValueError, KeyError, TypeError):
            return None

    def save(self, key, value):
        result = value.model_dump(mode="json")
        write_json(self.root / (key + ".json"), {"key": key, "result": result,
                   "checksum": fingerprint({"result": result})})

    def reserve(self):
        if self.usage["pending"]:
            raise BudgetExceeded("A previous model request has unresolved usage")
        if self.usage["calls"] >= self.budget.max_model_calls or self.usage["tokens"] >= self.budget.max_total_tokens:
            raise BudgetExceeded("Cumulative subtask budget exceeded")
        self.usage["calls"] += 1
        index = self.usage["calls"]
        self.usage["pending"].append(index)
        write_json(self.path, self.usage)
        return index

    def complete(self, index, tokens):
        if index not in self.usage["pending"]:
            raise ValueError("Model usage was already recorded")
        self.usage["tokens"] += tokens
        self.usage["pending"].remove(index)
        write_json(self.path, self.usage)
        if self.usage["tokens"] > self.budget.max_total_tokens:
            raise BudgetExceeded("Cumulative subtask token budget exceeded")

    def repair_used(self):
        self.usage["repairs"] = 1
        write_json(self.path, self.usage)

    def tool_called(self, calls):
        self.usage["tools"] = calls
        write_json(self.path, self.usage)
