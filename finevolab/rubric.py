"""Validate independent rubric ratings and quantify calibration agreement."""

import statistics


def calibrate(rubric: dict, controls: dict, first: dict, second: dict) -> dict:
    if not first.get("rater") or not second.get("rater") or first["rater"] == second["rater"]:
        raise ValueError("Two distinct identified raters are required")
    dimensions = set(rubric["dimensions"])
    identifiers = {item["id"] for item in controls["samples"]}
    parsed = []
    for rating in (first, second):
        if rating.get("rubric_version") != rubric["version"]:
            raise ValueError("Rater rubric versions differ")
        rows = rating.get("ratings", [])
        if len(rows) != len(identifiers) or {item["sample_id"] for item in rows} != identifiers:
            raise ValueError("Every calibration sample must be rated exactly once")
        result = {}
        for item in rows:
            scores = item["scores"]
            if set(scores) != dimensions or any(type(value) is not int or value not in {0, 1, 2} for value in scores.values()):
                raise ValueError("Each rubric dimension requires an integer score from zero to two")
            result[item["sample_id"]] = scores
        parsed.append(result)
    differences = [abs(parsed[0][identifier][name] - parsed[1][identifier][name])
                   for identifier in sorted(identifiers) for name in sorted(dimensions)]
    exact = sum(value == 0 for value in differences) / len(differences)
    error = statistics.mean(differences)
    passed = (exact >= rubric["calibration"]["minimum_exact_dimension_agreement"]
              and error <= rubric["calibration"]["maximum_mean_absolute_difference"])
    return {"rubric_version": rubric["version"], "raters": [first["rater"], second["rater"]],
            "samples": len(identifiers), "dimension_comparisons": len(differences),
            "exact_dimension_agreement": exact, "mean_absolute_difference": error,
            "status": "agreement_passed_requires_adjudication" if passed else "recalibration_required",
            "automatic_judge_calibrated": False}
