"""Recover monetary scale from table facts and source-located declarations."""

from decimal import Decimal, InvalidOperation
import re


def local_name(node) -> str:
    return (node.name or "").lower().split(":")[-1]


def unit_definitions(soup) -> dict[str, str]:
    result = {}
    for node in soup.find_all(lambda item: local_name(item) == "unit"):
        measures = node.find_all(lambda item: local_name(item) == "measure")
        if len(measures) == 1:
            value = measures[0].get_text(strip=True).split(":")[-1].upper()
            if re.fullmatch(r"[A-Z]{3}", value):
                result[node.get("id", "")] = value
    return result


def table_context(table, definitions: dict[str, str], preceding: list[tuple],
                  declarations: list[tuple], locator: str, declared_currency: str | None = None) -> dict:
    facts = []
    for position, node in enumerate(table.find_all(lambda item: local_name(item) == "nonfraction")):
        currency = definitions.get(node.get("unitref", ""))
        if currency is None:
            continue
        try:
            number = Decimal(node.get_text("", strip=True).replace(",", "").replace("(", "-").replace(")", ""))
            scale = int(node.get("scale", "0"))
        except (InvalidOperation, ValueError):
            continue
        if number and number.is_finite():
            facts.append((currency, scale, position, node.get("unitref", "")))
    choices = {(currency, scale) for currency, scale, _, _ in facts}
    if len(choices) == 1:
        currency, scale = next(iter(choices))
        return {"status": "verified", "currency": currency, "scale": scale,
                "multiplier": str(Decimal(10) ** scale), "method": "inline_xbrl_table_facts",
                "locator": locator + "|ix:nonfraction", "declarations": [
                    {"locator": locator + f"|ix:nonfraction={position}",
                     "text": f"unitRef={reference}; scale={scale}; unit={currency}"}
                    for _, _, position, reference in facts[:3]]}
    if len(choices) > 1:
        return {"status": "ambiguous", "method": "mixed_inline_units", "locator": locator,
                "alternatives": [{"currency": currency, "scale": scale} for currency, scale in sorted(choices)]}
    internal = table.get_text(" ", strip=True)
    candidates = [(locator + "|table-text", internal)] + list(reversed(preceding)) + declarations
    for position, text in candidates:
        scales = set()
        if re.search(r"\bin (?:USD |U\.?S\.? dollars |dollars )?millions\b", text, re.I):
            scales.add(6)
        if re.search(r"\bin (?:USD |U\.?S\.? dollars |dollars )?thousands\b", text, re.I):
            scales.add(3)
        if len(scales) != 1:
            continue
        currency = "USD" if re.search(r"\bUSD\b|U\.?S\.? dollars|US\$", text, re.I) else declared_currency
        scale = next(iter(scales))
        return {"status": "verified" if currency else "scale_only", "currency": currency, "scale": scale,
                "multiplier": str(Decimal(10) ** scale), "method": "source_declaration",
                "locator": position, "declarations": [{"locator": position, "text": text[:700]}]}
    return {"status": "unresolved", "method": "no_unambiguous_declaration", "locator": locator}


def context_prefix(context: dict) -> str:
    if context["status"] in {"verified", "scale_only"}:
        return (f"Monetary context: currency={context.get('currency') or 'unverified'}; "
                f"multiply raw monetary figures by {context['multiplier']}; "
                f"source={context['locator']}. Per-share and nonmonetary measures retain their own units.\n")
    return f"Monetary context: {context['status']}; source={context['locator']}.\n"
