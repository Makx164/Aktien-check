"""Pure scoring helpers. Thresholds are passed in; they are not hardcoded here."""

from __future__ import annotations


def band_points(value, bands):
    """First matching band wins. `above` is exclusive, `atLeast` is inclusive."""
    if value is None or not bands:
        return None, None
    value = float(value)
    for band in bands:
        if band.get("else"):
            return float(band["points"]), band.get("note")
        if "above" in band and value > float(band["above"]):
            return float(band["points"]), band.get("note")
        if "atLeast" in band and value >= float(band["atLeast"]):
            return float(band["points"]), band.get("note")
    return None, None


def linear_points(value, zero_at, ten_at):
    """Map value onto 0..10. Higher ten_at means higher is better."""
    if value is None or zero_at is None or ten_at is None:
        return None
    if ten_at == zero_at:
        return None
    value = float(value)
    zero_at = float(zero_at)
    ten_at = float(ten_at)
    if ten_at > zero_at:
        if value <= zero_at:
            return 0.0
        if value >= ten_at:
            return 10.0
        return 10.0 * (value - zero_at) / (ten_at - zero_at)
    if value >= zero_at:
        return 0.0
    if value <= ten_at:
        return 10.0
    return 10.0 * (zero_at - value) / (zero_at - ten_at)


def round1(value):
    if value is None:
        return None
    return round(float(value) + 0.0, 1)


def mean(values):
    present = [float(v) for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


def weighted_mean(values_and_weights):
    """Mean of present values. A missing value stays out and is not scored as zero."""
    numerator = 0.0
    denominator = 0.0
    for value, weight in values_and_weights:
        if value is None:
            continue
        numerator += float(value) * float(weight)
        denominator += float(weight)
    if denominator == 0:
        return None
    return numerator / denominator


def weighted_total(scores_and_weights):
    """0–100 score from criterion scores.

    Criteria without a score are left out. The remaining weights are
    re-normalized, so a missing criterion does not count as zero.
    """
    num = 0.0
    den = 0.0
    for score, weight in scores_and_weights:
        if score is None:
            continue
        num += float(score) * float(weight)
        den += float(weight)
    if den == 0:
        return None
    return (num / den) * 10.0


def traffic_light(score, green_at, orange_at):
    if score is None:
        return None
    if score >= green_at:
        return "green"
    if score >= orange_at:
        return "orange"
    return "red"


def quality_phrase(available, total):
    return f"{available} von {total} Metriken verfügbar"


def format_de(value, digits):
    if value is None:
        return None
    text = f"{float(value):.{digits}f}"
    return text.replace(".", ",")


def format_value(kind, raw):
    if raw is None:
        return None
    if kind == "percent":
        return format_de(float(raw) * 100.0, 1) + " %"
    if kind == "pp":
        return format_de(float(raw), 1) + " Pp"
    if kind == "ratioPp":
        return format_de(float(raw) * 100.0, 1) + " Pp"
    if kind == "multiple":
        return format_de(float(raw), 2)
    if kind == "binary":
        return "ja" if raw else "nein"
    if kind == "months":
        return format_de(float(raw), 1) + " Monate"
    return format_de(float(raw), 2)


def apply_analyst_cap(score, analyst_count, below, max_points):
    if score is None or analyst_count is None:
        return score, False
    if analyst_count < below and score > max_points:
        return float(max_points), True
    return score, False


def coverage_decision(criteria, minimum, status_label="wenig Daten"):
    """Headline score from criteria that have data.

    A criterion with a numeric score counts, including a real zero.
    Below `minimum` criteria the headline is the status label and no number.
    """
    present = [item for item in criteria if item.get("score") is not None]
    missing = [item for item in criteria if item.get("score") is None]
    total = weighted_total((item.get("score"), item.get("weight", 1)) for item in criteria)
    status = status_label if len(present) < minimum else None
    if status is not None:
        total = None
    note = None
    if missing:
        groups = {}
        for item in missing:
            label = item.get("status") or status_label
            groups.setdefault(label, []).append(item["id"])
        sentences = [f"{label} bei {', '.join(ids)}." for label, ids in groups.items()]
        if status is not None:
            sentences.append(f"Weniger als {minimum} Kriterien haben Daten.")
        else:
            sentences.append("Der Score zählt nur Kriterien mit Daten.")
        note = " ".join(sentences)
    return {
        "score": round1(total) if total is not None else None,
        "status": status,
        "criteriaWithData": len(present),
        "note": note,
    }
