# -*- coding: utf-8 -*-
"""Lightweight route judge for natural, non-tourpass courses.

This is intentionally deterministic: it gives us AI-like judgement without
requiring an external LLM key at runtime.
"""
from __future__ import annotations

from typing import Any


def _template_len(duration: str) -> int:
    if duration == "2h":
        return 2
    if duration == "half-day":
        return 3
    return 4


def _trim(roles: list[str], duration: str) -> list[str]:
    return roles[: _template_len(duration)]


def _templates_for(duration: str, phase: str) -> list[tuple[str, list[str]]]:
    d = str(duration or "half-day").strip().lower()
    if phase == "evening_night":
        if d == "2h":
            return [
                ("judge_night_rest_finish", ["night_walk", "late_night_rest"]),
                ("judge_night_rest_direct", ["late_night_rest", "night_walk"]),
            ]
        if d == "half-day":
            return [
                ("judge_night_rest_finish", ["night_walk", "late_night_rest"]),
                ("judge_night_rest_direct", ["late_night_rest", "night_walk"]),
            ]
        return [
            ("judge_night_rest_finish", ["night_walk", "late_night_rest", "night_walk"]),
            ("judge_night_rest_direct", ["late_night_rest", "night_walk"]),
        ]

    if d == "2h":
        return [
            ("judge_spot_cafe_light", ["main_spot", "cafe_rest"]),
            ("judge_meal_spot_cafe", ["meal", "main_spot"]),
            ("judge_afternoon_late_open_finish", ["main_spot", "late_night_rest"]),
        ]
    if d == "half-day":
        return [
            ("judge_spot_meal_cafe", ["main_spot", "meal", "cafe_rest"]),
            ("judge_meal_spot_cafe", ["meal", "main_spot", "cafe_rest"]),
            ("judge_afternoon_late_open_finish", ["main_spot", "meal", "late_night_rest"]),
            ("judge_evening_meal_late_open", ["meal", "main_spot", "late_night_rest"]),
        ]
    return [
        ("judge_spot_meal_cafe", ["main_spot", "meal", "secondary_spot", "cafe_rest"]),
        ("judge_meal_spot_cafe", ["meal", "main_spot", "secondary_spot", "cafe_rest"]),
        ("judge_spot_cafe_light", ["main_spot", "cafe_rest", "secondary_spot", "meal"]),
        ("judge_afternoon_late_open_finish", ["main_spot", "meal", "secondary_spot", "late_night_rest"]),
        ("judge_evening_meal_late_open", ["meal", "main_spot", "secondary_spot", "late_night_rest"]),
    ]

def judge_natural_route_roles(
    *,
    meal_context: Any,
    duration: str,
    weather: dict[str, Any],
    intent: dict[str, Any],
    pool_counts: dict[str, int],
) -> dict[str, Any]:
    """Pick the most natural route shape for tourpass-off recommendations."""
    d = str(duration or "half-day").strip().lower()
    if d not in ("2h", "half-day", "full-day"):
        d = "half-day"

    phase = str(getattr(meal_context, "phase", "afternoon_default"))
    clock = str(getattr(meal_context, "clock_label", "12:00"))
    try:
        hour, minute = [int(x) for x in clock.split(":", 1)]
    except Exception:
        hour, minute = 12, 0
    tod = hour * 60 + minute
    pp = float(weather.get("precip_prob", 0) or 0)
    meals = int(pool_counts.get("restaurant", 0) or 0)
    cafes = int(pool_counts.get("cafe", 0) or 0)
    tourist = int(pool_counts.get("tourist", 0) or 0) + int(pool_counts.get("indoor", 0) or 0)
    goal = str(intent.get("trip_goal") or "")

    best: tuple[float, str, list[str], list[str]] | None = None
    for reason, roles in _templates_for(d, phase):
        score = 0.0
        notes: list[str] = []

        if roles and roles[0] == "meal":
            if phase == "lunch" or tod >= 18 * 60:
                score += 5.0
                notes.append("식사 시간이 가까워 식당을 앞으로 배치")
            elif phase == "dinner" and tod >= 17 * 60:
                score += 3.0
                notes.append("저녁 전후라 식사를 빠르게 연결")
            else:
                score -= 1.0
        if "meal" in roles:
            score += 1.2 if meals > 0 else -2.0
        if "cafe_rest" in roles:
            score += 1.0 if cafes > 0 else -0.5
        if roles and roles[0] in ("main_spot", "night_walk"):
            score += 1.0 if tourist > 0 else -1.0

        if phase == "pre_lunch" and roles[:2] == ["main_spot", "meal"]:
            score += 4.0
            notes.append("점심 전에는 먼저 한 곳 보고 식사")
        if phase == "afternoon_default" and roles[:3] == ["main_spot", "meal", "secondary_spot"]:
            score += 2.0
            notes.append("낮 시간대 기본 관광-식사-관광 흐름")
        if phase == "evening_night":
            if "late_night_rest" in roles:
                score += 5.0
                notes.append("밤 시간대라 술집/야간 휴식 후보 포함")
            if "cafe_rest" in roles:
                score -= 8.0
            if roles and roles[0] == "late_night_rest" and tod >= 22 * 60:
                score += 1.5
                notes.append("늦은 밤이라 휴식을 더 앞에 배치")

        if 15 * 60 <= tod < 20 * 60 and "late_night_rest" in roles:
            score += 2.2
            notes.append("오후 이후라 늦게까지 가능한 가게를 마무리로 배치")
        if 15 * 60 <= tod < 18 * 60 and roles and roles[0] == "meal":
            score -= 2.0

        if pp >= 55 and any(r in roles for r in ("cafe_rest", "late_night_rest")):
            score += 0.8
            notes.append("비 가능성이 있어 실내 휴식 슬롯 유지")
        if goal in ("culture", "festival") and "meal" in roles and "cafe_rest" in roles:
            score += 0.6
            notes.append("목적보다 시간대와 식사 흐름을 우선")

        if best is None or score > best[0]:
            best = (score, reason, roles, notes)

    assert best is not None
    score, reason, roles, notes = best
    return {
        "roles": roles,
        "reason": reason,
        "score": round(score, 3),
        "notes": notes[:3],
        "phase": phase,
        "clock": clock,
        "pool_counts": dict(pool_counts),
    }
