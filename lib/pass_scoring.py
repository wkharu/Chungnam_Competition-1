# -*- coding: utf-8 -*-
"""패스퀘스트·투어패스 가중 보조 점수(날씨·시간·현실성 필터 이후 레이어)."""

from __future__ import annotations

from typing import Any

TICKET_HOURS = {
    "24h": 24,
    "36h": 36,
    "48h": 48,
    "single": 12,
    "theme": 24,
    "undecided": 20,
    "none": 8,
}

DURATION_HOURS = {"2h": 2, "half-day": 5, "full-day": 10}


def pass_value_to_score(level: str) -> float:
    return {"high": 0.95, "medium": 0.65, "low": 0.35, "unknown": 0.45}.get(level, 0.45)


def benefit_type_score(btype: str) -> float:
    return {"free": 1.0, "discount": 0.75, "unknown": 0.5, "none": 0.2}.get(btype, 0.5)


def score_pass_fit_row(row: dict[str, Any], benefit_priority: str) -> float:
    base = float(row.get("tourpass_confidence") or 0.35)
    if row.get("tourpass_available") is not True:
        base *= 0.48
    if benefit_priority == "high":
        base = min(1.0, base * 1.12)
    elif benefit_priority == "none":
        base *= 0.92
    return max(0.0, min(1.0, base))


def score_time_ticket_fit(ticket: str, duration_key: str, n_steps: int) -> float:
    th = float(TICKET_HOURS.get(ticket, 8))
    dh = float(DURATION_HOURS.get(duration_key, 5))
    load = max(1, min(6, n_steps))
    need = dh + (load - 3) * 0.45
    if th + 1e-6 >= need:
        fit = 0.72 + min(0.28, (th - need) / 40.0)
    else:
        fit = 0.35 + max(0.0, (th / max(need, 1.0)) - 0.5)
    if ticket == "48h" and duration_key in ("half-day", "full-day"):
        fit = min(1.0, fit + 0.08)
    if ticket in ("24h", "single") and duration_key == "full-day" and n_steps >= 5:
        fit *= 0.82
    return max(0.0, min(1.0, fit))


def score_benefit_row(row: dict[str, Any], benefit_priority: str) -> float:
    s = benefit_type_score(str(row.get("pass_benefit_type") or "unknown"))
    s = s * 0.55 + pass_value_to_score(str(row.get("pass_value_level") or "unknown")) * 0.45
    if benefit_priority == "high":
        s = min(1.0, s * 1.08)
    return max(0.0, min(1.0, s))


def local_spend_from_role(role: str | None) -> float:
    if role == "meal":
        return 1.0
    if role == "cafe_rest":
        return 0.88
    if role in ("secondary_spot", "finish", "night_walk"):
        return 0.42
    return 0.38


def score_local_spend(
    steps: list[dict[str, Any]],
    catalog_rows: list[dict[str, Any]],
) -> float:
    if not steps:
        return 0.5
    acc = 0.0
    for st, row in zip(steps, catalog_rows):
        role = st.get("step_role")
        v = local_spend_from_role(str(role) if role else None)
        cat = str(row.get("pass_category") or "unknown")
        if cat in ("restaurant", "cafe", "local"):
            v = max(v, 0.92)
        elif cat == "experience":
            v = max(v, 0.58)
        elif cat == "accommodation":
            v = max(v, 0.5)
        acc += v
    return max(0.0, min(1.0, acc / len(steps)))


def score_pass_route_efficiency(
    movement_burden: str | None,
    n_steps: int,
    avg_pass: float,
) -> float:
    mb = (movement_burden or "").lower()
    burden = 0.5
    if "가벼" in mb or "짧" in mb or "적" in mb:
        burden = 0.78
    if "무겁" in mb or "멀" in mb or "길" in mb:
        burden = 0.36
    step_pen = max(0.42, 1.0 - max(0, n_steps - 3) * 0.11)
    return max(0.0, min(1.0, burden * 0.52 + avg_pass * 0.28 + step_pen * 0.2))


def indoor_ratio_from_steps(steps: list[dict[str, Any]], rec_by_name: dict[str, dict[str, Any]]) -> float:
    if not steps:
        return 0.0
    hits = 0
    for st in steps:
        nm = str(st.get("name") or "").strip()
        row = rec_by_name.get(nm) or {}
        cat = str(row.get("category") or "")
        role = str(st.get("step_role") or "")
        if cat == "indoor" or role in ("cafe_rest", "meal"):
            hits += 1
    return hits / len(steps)


def score_pass_completion_ease(
    duration_key: str,
    n_steps: int,
    precip_prob: float,
    indoor_ratio: float,
) -> float:
    dur_ease = {"2h": 0.94, "half-day": 0.82, "full-day": 0.68}.get(duration_key, 0.8)
    step_ease = max(0.38, 1.0 - max(0, n_steps - 4) * 0.14)
    weather_pen = min(1.0, 1.0 - max(0.0, float(precip_prob) - 38.0) / 130.0)
    return max(
        0.0,
        min(1.0, dur_ease * 0.34 + step_ease * 0.36 + weather_pen * 0.22 + indoor_ratio * 0.08),
    )


def distance_burden_proxy(movement_burden: str | None) -> float:
    mb = (movement_burden or "").lower()
    if "가벼" in mb or "짧" in mb:
        return 0.25
    if "무겁" in mb or "멀" in mb:
        return 0.85
    return 0.5


def meal_timing_fit_score(steps: list[dict[str, Any]]) -> float:
    roles = [str(s.get("step_role") or "") for s in steps]
    if "meal" in roles:
        return 0.92
    if "cafe_rest" in roles:
        return 0.72
    return 0.45


def avg_review_score_from_steps(steps: list[dict[str, Any]]) -> float:
    rs: list[float] = []
    for st in steps:
        try:
            r = float(st.get("rating") or 0.0)
        except (TypeError, ValueError):
            r = 0.0
        if r > 0:
            rs.append(min(1.0, r / 5.0))
    if not rs:
        return 0.55
    return sum(rs) / len(rs)
