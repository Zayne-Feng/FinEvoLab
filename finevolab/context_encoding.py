"""Encode repeated financial observations without removing source fields or text."""

from copy import deepcopy
import json

FIELDS = ("company", "name", "fiscal_year", "value", "currency", "unit", "period_start",
          "period_end", "period_kind", "evidence_id", "report_year", "extraction_method")
GUIDANCE = ("Financial objects may use observation_row references. Resolve each reference against "
            "_observations: map its columns to the corresponding row values. All source fields and "
            "passage text are preserved. Return the ordinary output schema, not this input encoding.")


def encode_context(payload: dict) -> tuple[str, dict]:
    original = json.dumps(payload, ensure_ascii=False)
    rows, lookup = [], {}
    def visit(value):
        if isinstance(value, dict):
            if set(value) == set(FIELDS):
                row = [value[key] for key in FIELDS]
                key = json.dumps(row, sort_keys=True, ensure_ascii=False)
                if key not in lookup:
                    lookup[key] = len(rows)
                    rows.append(row)
                return {"observation_row": lookup[key]}
            return {key: visit(item) for key, item in value.items()}
        return [visit(item) for item in value] if isinstance(value, list) else value
    reserved = any(key in payload for key in ("_context_encoding", "_observations"))
    reserved = reserved or '"observation_row"' in original
    packed = deepcopy(payload) if reserved else visit(payload)
    if rows:
        packed.update(_context_encoding="observations_v1", _observations={"columns": list(FIELDS), "rows": rows})
    encoded = json.dumps(packed, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) + (len(GUIDANCE) if rows else 0) >= len(original):
        encoded, rows = original, []
    restored = json.loads(encoded) if reserved else decode_context(encoded)
    if restored != payload:
        raise ValueError("Context encoding changed the source payload")
    return encoded, {"original_chars": len(original), "encoded_chars": len(encoded),
                     "observation_rows": len(rows), "lossless": True}


def decode_context(text: str) -> dict:
    value = json.loads(text)
    if value.get("_context_encoding") != "observations_v1":
        return value
    columns = value.pop("_observations")
    value.pop("_context_encoding")
    def visit(item):
        if isinstance(item, dict):
            if set(item) == {"observation_row"}:
                return dict(zip(columns["columns"], columns["rows"][item["observation_row"]], strict=True))
            return {key: visit(child) for key, child in item.items()}
        return [visit(child) for child in item] if isinstance(item, list) else item
    return visit(value)
