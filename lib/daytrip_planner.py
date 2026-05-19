# -*- coding: utf-8 -*-
"""
날씨·의도·거리를 반영한 당일 코스(Plan A/B) 조합 및 API 페이로드.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any

from lib.distance import haversine
from lib.intent_hints import _COMPANION_HINTS, _GOAL_TAG_HINTS, _tags_lower
from lib.recommend_ui import build_ui_fields_for_destination
from lib.course_view import build_consumer_course_view
from lib.course_flow import (
    build_outing_plan_places,
    cafe_placeholder_dict,
    infer_venue_kind,
    meal_placeholder_dict,
    time_band_for_hour,
)
from lib.tourpass_catalog import catalog_row_for_place
from lib.itinerary_builder import build_itinerary_for_course, trip_start_datetime
from lib.meal_context import build_meal_context, step_roles_for_meal_context
from lib.places import fetch_continuation_candidates
from lib.route_judge import judge_natural_route_roles
from lib.venue_hours_policy import trip_context_consumer_note, trip_detail_band


def _google_meal_row_to_dest(row: dict[str, Any]) -> dict[str, Any]:
    types = [str(x).lower() for x in (row.get("types") or [])]
    is_cafe = any("cafe" in t or "coffee" in t for t in types)
    tags = ["카페", "디저트", "커피"] if is_cafe else ["맛집", "식사", "한식"]
    try:
        r_show = float(row.get("rating") or 0.0)
    except (TypeError, ValueError):
        r_show = 0.0
    try:
        rev_n = int(row.get("review_count") or 0)
    except (TypeError, ValueError):
        rev_n = 0
    return {
        "id": str(row.get("place_id") or ""),
        "name": row["name"],
        "city": "",
        "category": "indoor",
        "tags": tags,
        "score": 0.52,
        "weather_score": 0.5,
        "coords": {"lat": float(row["lat"]), "lng": float(row["lng"])},
        "address": str(row.get("address") or ""),
        "image": str(row.get("photo_url") or ""),
        "copy": "주변 식음료 검색으로 연결한 후보예요. 영업 시간은 방문 전 확인해 주세요.",
        "source": "google_places_itinerary",
        "rating": r_show,
        "review_count": rev_n,
        "weather_weights": {"sunny": 0.5, "rainy": 0.75, "fine_dust_limit": "bad"},
        "golden_hour_bonus": False,
        "temp_range": {"min": -20, "max": 40},
    }


def _pool_category_counts(pool: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"tourist": 0, "restaurant": 0, "cafe": 0, "indoor": 0}
    for p in pool:
        vk = infer_venue_kind(p)
        cat = str(p.get("category") or "")
        if vk == "meal":
            counts["restaurant"] += 1
        elif vk == "cafe":
            counts["cafe"] += 1
        elif cat == "indoor":
            counts["indoor"] += 1
        else:
            counts["tourist"] += 1
    return counts


def inject_meal_places_for_plan(
    plan_raw: list[dict[str, Any]],
    roles: list[str],
    *,
    anchor_lat: float,
    anchor_lng: float,
    meal_ctx: Any,
) -> tuple[list[dict[str, Any]], bool]:
    """식당/카페 슬롯이 관광지로 흐트러지면 Places 후보나 안내 슬롯으로 바로잡는다."""
    out: list[dict[str, Any]] = [dict(p) for p in plan_raw]
    any_insufficient = False
    verify_meal = bool(
        meal_ctx is not None and getattr(meal_ctx, "requires_verified_meal_place", False)
    )

    for i, role in enumerate(roles):
        if role not in ("meal", "cafe_rest", "late_night_rest") or i >= len(out):
            continue

        pl = out[i]
        nm = str(pl.get("name") or "").strip()
        vk = infer_venue_kind(pl) if nm else None
        is_placeholder = bool(pl.get("meal_data_insufficient"))

        if role == "meal":
            need_fetch = (not nm) or is_placeholder or (verify_meal and vk != "meal")
            search_types = ["restaurant", "korean_restaurant", "chinese_restaurant", "meal_takeaway"]
        elif role == "late_night_rest":
            need_fetch = (not nm) or is_placeholder or vk not in ("meal", "cafe")
            search_types = ["bar", "pub", "wine_bar", "restaurant"]
        else:
            need_fetch = (not nm) or is_placeholder or vk != "cafe"
            search_types = ["cafe", "coffee_shop", "bakery"]

        if not need_fetch:
            continue

        lat, lng = anchor_lat, anchor_lng
        if i > 0 and out[i - 1].get("coords"):
            lat = float(out[i - 1]["coords"].get("lat") or lat)
            lng = float(out[i - 1]["coords"].get("lng") or lng)

        rows, _, _, _ = fetch_continuation_candidates(
            lat,
            lng,
            search_types,
            max_results=10,
        )
        used_names = {
            str(p.get("name") or "").strip()
            for j, p in enumerate(out)
            if j != i and str(p.get("name") or "").strip()
        }
        picked_row = next(
            (row for row in rows if str(row.get("name") or "").strip() not in used_names),
            None,
        )
        if picked_row is not None:
            out[i] = _google_meal_row_to_dest(picked_row)
        elif role == "meal":
            out[i] = meal_placeholder_dict(lat, lng)
            any_insufficient = True
        elif role == "late_night_rest":
            out[i] = cafe_placeholder_dict(lat, lng)
            out[i]["name"] = "야간 휴식 장소 데이터 부족"
            out[i]["tags"] = ["술집", "야간", "휴식"]
            out[i]["copy"] = "주변에서 영업 중인 술집/야간 휴식 장소를 찾지 못했어요. 지도 앱으로 영업 여부를 확인해 주세요."
            any_insufficient = True
        else:
            out[i] = cafe_placeholder_dict(lat, lng)
            any_insufficient = True
    return out, any_insufficient

def intent_score_multiplier(dest: dict[str, Any], intent: dict[str, str]) -> float:
    """기존 종합 score에 곱해 의도에 맞게 순위만 가볍게 조정."""
    m = 1.0
    cat = dest.get("category") or ""
    tags = _tags_lower(dest)
    goal = intent["trip_goal"]
    comp = intent["companion"]

    for hint in _GOAL_TAG_HINTS.get(goal, ()):
        if any(hint.lower() in tg for tg in tags):
            m += 0.06
    if goal == "indoor" and cat == "indoor":
        m += 0.12

    for hint in _COMPANION_HINTS.get(comp, ()):
        if any(hint.lower() in tg for tg in tags):
            m += 0.04

    # 메타: companion_fit (선택 필드)
    cf = dest.get("companion_fit")
    if isinstance(cf, list) and comp in cf:
        m += 0.08

    return min(m, 1.45)


def target_place_count(duration: str) -> int:
    """Plan A/B에 넣을 장소 개수 — 일정 길이에 따라 명확히 구분."""
    if duration == "2h":
        return 2
    if duration == "half-day":
        return 3
    return 4


def with_adjusted_scores(
    recommendations: list[dict[str, Any]], intent: dict[str, str]
) -> list[dict[str, Any]]:
    """
    과거에는 의도를 곱셈 보정했으나, goal_fit이 match_from_api 총점에 포함됨.
    풀 정렬용으로 score 기준만 유지한다.
    """
    _ = intent
    out = [{**d, "adjusted_score": d.get("score", 0)} for d in recommendations]
    out.sort(key=lambda x: x["adjusted_score"], reverse=True)
    return out


def nearest_neighbor_order(
    places: list[dict[str, Any]], user_lat: float, user_lng: float
) -> list[dict[str, Any]]:
    """단순 이동 부담 추정용: 사용자 기준 근접 탐욕 순서 (내비게이션 경로 최적화 아님)."""
    if not places:
        return []
    remaining = places.copy()
    ordered: list[dict[str, Any]] = []
    cur_lat, cur_lng = user_lat, user_lng

    while remaining:
        best_i = 0
        best_d = math.inf
        for i, p in enumerate(remaining):
            lat = p["coords"]["lat"]
            lng = p["coords"]["lng"]
            if lat == 0 and lng == 0:
                dist = 9999.0
            else:
                dist = haversine(cur_lat, cur_lng, lat, lng)
            if dist < best_d:
                best_d = dist
                best_i = i
        nxt = remaining.pop(best_i)
        ordered.append(nxt)
        cur_lat = nxt["coords"]["lat"]
        cur_lng = nxt["coords"]["lng"]
    return ordered


def _pick_pool(adjusted: list[dict[str, Any]], pool_size: int = 24) -> list[dict[str, Any]]:
    return adjusted[: max(pool_size, 12)]


def _pass_context_active(pass_context: dict[str, Any] | None) -> bool:
    if not pass_context:
        return False
    return str(pass_context.get("tourpass_mode") or "").strip().lower() in ("1", "true", "yes", "on")


def _tourpass_row(place: dict[str, Any]) -> dict[str, Any]:
    if place.get("tourpass_available") is not None:
        return {
            "tourpass_available": place.get("tourpass_available"),
            "tourpass_confidence": place.get("tourpass_confidence"),
            "pass_category": place.get("pass_category"),
            "pass_benefit_type": place.get("pass_benefit_type"),
        }
    return catalog_row_for_place(str(place.get("name") or ""))


def _tourpass_place_score(place: dict[str, Any]) -> float:
    row = _tourpass_row(place)
    if row.get("tourpass_available") is not True:
        return 0.0
    base = float(row.get("tourpass_confidence") or 0.45)
    cat = str(row.get("pass_category") or "unknown")
    cat_bonus = {
        "experience": 0.14,
        "cafe": 0.12,
        "local": 0.12,
        "restaurant": 0.12,
        "attraction": 0.10,
        "accommodation": 0.04,
    }.get(cat, 0.02)
    return max(0.0, min(1.0, base + cat_bonus))


def _tourpass_category(place: dict[str, Any]) -> str:
    return str(_tourpass_row(place).get("pass_category") or "unknown")


def _tourpass_priority_adjusted(adjusted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for place in adjusted:
        ps = _tourpass_place_score(place)
        if ps <= 0:
            out.append(place)
            continue
        p = {**place}
        p["adjusted_score"] = round(float(p.get("adjusted_score") or p.get("score") or 0) + ps * 0.28, 4)
        p["tourpass_course_priority_score"] = round(ps, 4)
        out.append(p)
    out.sort(key=lambda x: x.get("adjusted_score", 0), reverse=True)
    return out


def _nearest_tourpass_pick(
    candidates: list[dict[str, Any]],
    cur_lat: float,
    cur_lng: float,
    used: set[str],
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_key: tuple[float, float] | None = None
    for p in candidates:
        nm = str(p.get("name") or "")
        if not nm or nm in used:
            continue
        row = _tourpass_row(p)
        if row.get("tourpass_available") is not True:
            continue
        coords = p.get("coords") or {}
        try:
            lat = float(coords.get("lat") or 0)
            lng = float(coords.get("lng") or 0)
        except (TypeError, ValueError):
            continue
        if lat == 0 and lng == 0:
            continue
        d = haversine(cur_lat, cur_lng, lat, lng)
        key = (d, -_tourpass_place_score(p))
        if best is None or key < best_key:  # type: ignore[operator]
            best = p
            best_key = key
    return best


def _promote_tourpass_plan_mix(
    places: list[dict[str, Any]],
    roles: list[str],
    pool: list[dict[str, Any]],
    user_lat: float,
    user_lng: float,
) -> tuple[list[dict[str, Any]], bool]:
    if not places or not roles:
        return places, False
    used: set[str] = set()
    out: list[dict[str, Any]] = []
    changed = False
    cur_lat, cur_lng = user_lat, user_lng

    desired_by_role = {
        "main_spot": ("attraction", "experience"),
        "secondary_spot": ("experience", "attraction", "local"),
        "meal": ("local", "restaurant", "cafe"),
        "cafe_rest": ("cafe", "local"),
        "finish": ("cafe", "local", "experience"),
        "night_walk": ("cafe", "local", "attraction"),
        "late_night_rest": ("cafe", "local", "restaurant"),
    }

    for i, role in enumerate(roles):
        original = places[i] if i < len(places) else None
        desired = desired_by_role.get(str(role), ("attraction", "experience", "cafe", "local"))
        candidates = [p for p in pool if _tourpass_category(p) in desired]
        pick = _nearest_tourpass_pick(candidates, cur_lat, cur_lng, used)
        if pick is None:
            pick = _nearest_tourpass_pick(pool, cur_lat, cur_lng, used)
        if pick is None and original is not None and str(original.get("name") or "") not in used:
            pick = original
        if pick is None:
            continue
        nm = str(pick.get("name") or "")
        used.add(nm)
        out.append(pick)
        if original is None or nm != str(original.get("name") or ""):
            changed = True
        coords = pick.get("coords") or {}
        try:
            cur_lat = float(coords.get("lat") or cur_lat)
            cur_lng = float(coords.get("lng") or cur_lng)
        except (TypeError, ValueError):
            pass

    return (out if out else places), changed


def _tourpass_hits(places: list[dict[str, Any]]) -> int:
    return sum(1 for p in places if _tourpass_row(p).get("tourpass_available") is True)


def _is_indoor_heavy_candidate(d: dict[str, Any]) -> bool:
    if d.get("category") == "indoor":
        return True
    w = d.get("weather_weights") or {}
    return float(w.get("rainy", 0)) >= 0.85


def _indoor_pick_predicate(
    d: dict[str, Any], *, category_only: bool
) -> bool:
    """강수 대비 메인 코스는 야외(우천 내성만 높음)를 실내로 오인하지 않도록 구분."""
    if category_only:
        return (d.get("category") or "") == "indoor"
    return _is_indoor_heavy_candidate(d)


def build_plan_places(
    pool: list[dict[str, Any]],
    n: int,
    user_lat: float,
    user_lng: float,
    exclude_names: set[str],
    prefer_indoor: bool,
    *,
    indoor_category_only: bool = False,
) -> list[dict[str, Any]]:
    """pool에서 조건에 맞게 n곡까지 선택 후 NN 정렬."""
    if n <= 0:
        return []
    filt: list[dict[str, Any]] = []
    for d in pool:
        if d["name"] in exclude_names:
            continue
        if prefer_indoor and not _indoor_pick_predicate(
            d, category_only=indoor_category_only
        ):
            continue
        filt.append(d)
    if prefer_indoor and len(filt) < n:
        for d in pool:
            if d["name"] in exclude_names or d in filt:
                continue
            if not _indoor_pick_predicate(d, category_only=indoor_category_only):
                continue
            filt.append(d)
            if len(filt) >= max(n * 3, 12):
                break
    if prefer_indoor and len(filt) < n and not indoor_category_only:
        for d in pool:
            if d["name"] in exclude_names or d in filt:
                continue
            filt.append(d)
            if len(filt) >= n * 2:
                break
    if not prefer_indoor and len(filt) < n:
        filt = [d for d in pool if d["name"] not in exclude_names][: max(n * 2, 8)]

    filt = filt[: max(n * 3, 9)]
    top = filt[:n] if len(filt) >= n else filt
    return nearest_neighbor_order(top, user_lat, user_lng)[:n]


def environment_triggers(weather: dict[str, Any], scores: dict[str, Any]) -> list[dict[str, str]]:
    """투명한 조건 코드 + 사람이 읽을 수 있는 문구."""
    triggers: list[dict[str, str]] = []
    pp = float(weather.get("precip_prob", 0))
    dust = int(weather.get("dust", 1))
    temp = float(weather.get("temp", 20))

    if pp >= 60:
        triggers.append(
            {
                "code": "precip_prob_ge_60",
                "label": "강수확률 60% 이상 — 실내·짧은 동선 우선 구간",
            }
        )
    elif pp >= 50:
        triggers.append(
            {
                "code": "precip_prob_ge_50",
                "label": "강수확률 50% 이상 — 실내·혼합 코스를 우선하는 구간",
            }
        )
    elif pp >= 30:
        triggers.append(
            {
                "code": "precip_prob_ge_30",
                "label": "강수확률 30% 이상 — 짧은 동선·실내 병행을 고려",
            }
        )

    if dust >= 3:
        triggers.append(
            {
                "code": "bad_fine_dust",
                "label": "미세먼지 나쁨 수준 — 실내·마스크 고려",
            }
        )

    if temp < 0 or temp > 33:
        triggers.append(
            {
                "code": "extreme_temperature",
                "label": "기온이 매우 낮거나 높음 — 체감·안전 확인",
            }
        )

    if scores.get("is_raining"):
        triggers.append(
            {
                "code": "precip_prob_model_rainy",
                "label": "강수확률이 높아 야외 활동에 제약이 있을 수 있음(예보 기준)",
            }
        )

    return triggers


def outdoor_heavy_trigger(plan_places: list[dict[str, Any]], duration: str) -> dict[str, str] | None:
    if duration == "2h":
        return None
    out = sum(1 for p in plan_places if p.get("category") == "outdoor")
    if out >= 2:
        return {
            "code": "plan_a_outdoor_heavy",
            "label": "Plan A가 야외 비중이 큼 — 대안(실내) 코스를 함께 제시",
        }
    return None


def _get_meta_str(dest: dict[str, Any], key: str, default: str = "") -> str:
    v = dest.get(key)
    return str(v) if v is not None else default


def _default_avg_stay(dest: dict[str, Any], duration: str) -> int:
    v = dest.get("avg_stay_minutes")
    if isinstance(v, (int, float)) and v > 0:
        return int(v)
    if duration == "2h":
        return 60
    if duration == "half-day":
        return 90
    return 120


def explain_place(
    dest: dict[str, Any],
    weather: dict[str, Any],
    scores: dict[str, Any],
    intent: dict[str, str],
) -> list[str]:
    """결정론적 설명 문장."""
    lines: list[str] = []
    if dest.get("recommendation_summary"):
        lines.append(str(dest["recommendation_summary"]))
    cat = dest.get("category", "")
    w = dest.get("weather_weights") or {}
    tags = dest.get("tags") or []

    if cat == "indoor":
        lines.append(
            f"실내형이라 비 올 때도 부담이 덜해요(우천 가중 {float(w.get('rainy', 0)):.1f})."
        )
    else:
        lines.append(
            f"야외형이라 맑은 날 가중({float(w.get('sunny', 0)):.1f})을 봤어요."
        )

    if dest.get("golden_hour_bonus") and scores.get("is_golden_hour"):
        lines.append("지금 시간대가 노을·사진에 유리할 수 있어요.")
    elif dest.get("golden_hour_bonus"):
        lines.append("노을·사진 포인트 태그가 있어요(시각은 달라질 수 있어요).")

    goal = intent["trip_goal"]
    hints = _GOAL_TAG_HINTS.get(goal, ())
    hit = [h for h in hints if any(h in str(t) for t in tags)]
    if hit:
        lines.append(f"여행 목표「{goal}」와 태그 키워드({', '.join(hit[:3])})가 맞닿습니다.")

    comp = intent["companion"]
    ch = _COMPANION_HINTS.get(comp, ())
    hit_c = [h for h in ch if any(h in str(t) for t in tags)]
    if hit_c:
        lines.append(f"동행 유형「{comp}」에 어울리는 태그({', '.join(hit_c[:2])})가 있습니다.")

    if dest.get("narrative_enrichment_line") and float(
        dest.get("storytelling_match_confidence") or 0
    ) >= 0.4:
        lines.append(str(dest["narrative_enrichment_line"]))

    return lines[:5]


def checks_for_plan(
    places: list[dict[str, Any]],
    intent: dict[str, str],
    weather: dict[str, Any],
    scores: dict[str, Any],
    plan_label: str,
) -> list[str]:
    """출발 전에 알아두면 좋은 점(짧게, 안내 톤)."""
    out: list[str] = []
    pp = float(weather.get("precip_prob", 0))
    dust = int(weather.get("dust", 1))
    out.append("운영시간과 휴무는 공식 채널에서 한 번 더 확인해 주세요.")

    if intent["transport"] == "car":
        out.append("주차는 현장·지도 앱으로 확인하는 것이 좋습니다.")
    else:
        out.append("대중교통은 배차·노선이 바뀔 수 있으니 출발 전 시간표를 확인해 주세요.")

    outd = sum(1 for p in places if p.get("category") == "outdoor")
    if outd >= 2:
        out.append("야외 비중이 높은 코스라 날씨가 바뀌면 대체 코스를 함께 보는 것이 좋습니다.")
    if pp >= 40:
        out.append("강수확률이 있는 편이라 우산·실내 대안을 챙기면 여유 있습니다.")
    if dust >= 3:
        out.append("미세먼지가 나쁜 날에는 실내 활동을 섞는 편이 편합니다.")

    if any(p.get("golden_hour_bonus") for p in places):
        out.append("사진·노을 목적이면 일몰 시각 전후로 맞추면 좋습니다.")

    return out[:6]


def plan_title(plan: str, intent: dict[str, str], indoor: bool) -> str:
    g = intent["trip_goal"]
    d = intent["duration"]
    if plan == "A":
        return f"오늘의 메인 코스 ({d}, 목표: {g})"
    if indoor:
        return "날씨 악화·실내 대안 코스 (Plan B)"
    return "대체 코스 (Plan B)"


def serialize_place(
    dest: dict[str, Any],
    weather: dict[str, Any],
    scores: dict[str, Any],
    intent: dict[str, str],
) -> dict[str, Any]:
    d_clear = {**dest}
    d_clear["recommendation_summary"] = None
    why_detailed = explain_place(d_clear, weather, scores, intent)[:4]
    ui = build_ui_fields_for_destination(dest, weather, scores, intent)
    concise = ui["concise_explanation_lines"]
    out: dict[str, Any] = {
        "id": dest.get("id", ""),
        "name": dest.get("name", ""),
        "city": dest.get("city", ""),
        "category": dest.get("category", ""),
        "tags": dest.get("tags", []),
        "score": dest.get("score"),
        "weather_score": dest.get("weather_score"),
        "distance_km": dest.get("distance_km"),
        "address": dest.get("address") or "",
        "copy": dest.get("copy", ""),
        "image": dest.get("image"),
        "coords": dest.get("coords"),
        "avg_stay_minutes": _default_avg_stay(dest, intent["duration"]),
        "activity_level": dest.get("activity_level") or "moderate",
        "opening_hours_note": dest.get("opening_hours_note"),
        "parking_note": dest.get("parking_note"),
        "why": concise,
        "why_detailed": why_detailed,
        "score_breakdown": dest.get("score_breakdown"),
        "score_contributions": dest.get("score_contributions"),
        "recommendation_summary": dest.get("recommendation_summary"),
        "total_score_100": ui["total_score_100"],
        "score_axis_display": ui["score_axis_display"],
        "top_reason_tokens": ui["top_reason_tokens"],
        "concise_explanation_lines": ui["concise_explanation_lines"],
        "why_recommend_bullets": ui.get("why_recommend_bullets"),
        "decision_conclusion": ui.get("decision_conclusion"),
        "lead_weather_sentence": ui.get("lead_weather_sentence"),
        "lead_place_sentence": ui.get("lead_place_sentence"),
        "practical_info": ui["practical_info"],
        "caution_lines": ui["caution_lines"],
        "place_identity": ui.get("place_identity"),
        "place_identity_summary": ui.get("place_identity_summary"),
        "why_today_narrative": ui.get("why_today_narrative"),
        "expectation_bullets": ui.get("expectation_bullets"),
        "expectation_points": ui.get("expectation_points"),
        "enriched_tags": ui.get("enriched_tags"),
        "narrative_archetype": ui.get("narrative_archetype"),
        "tourpass_available": dest.get("tourpass_available"),
        "tourpass_confidence": dest.get("tourpass_confidence"),
        "pass_category": dest.get("pass_category"),
        "pass_benefit_type": dest.get("pass_benefit_type"),
        "pass_value_level": dest.get("pass_value_level"),
        "tourpass_city": dest.get("tourpass_city"),
        "tourpass_catalog_name": dest.get("tourpass_catalog_name"),
        "tourpass_match_type": dest.get("tourpass_match_type"),
    }
    for _k in (
        "story_summary",
        "story_tags",
        "emotional_copy",
        "narrative_enrichment_line",
        "storytelling_match_confidence",
    ):
        if dest.get(_k) is not None:
            out[_k] = dest[_k]
    return out


def plan_why_summary(
    places: list[dict[str, Any]], weather: dict[str, Any], intent: dict[str, str]
) -> list[str]:
    lines = [
        "당일 단기예보와 정적 메타(태그·실내/야외)를 합쳐 순서를 잡았습니다.",
    ]
    if intent["transport"] == "public":
        lines.append("대중교통은 이동 시간 없이 순서만 제안합니다.")
    return lines


def _enrich_recommendation_row(
    rec: dict[str, Any],
    weather: dict[str, Any],
    scores: dict[str, Any],
    intent: dict[str, str],
    *,
    rank_index: int = 0,
    peer_scores: list[float] | None = None,
) -> dict[str, Any]:
    """랭킹 카드·API 하위 호환용으로 UI 설명 필드를 붙인다."""
    ui = build_ui_fields_for_destination(
        rec, weather, scores, intent, rank_index=rank_index, peer_scores=peer_scores
    )
    sl = {**rec, "recommendation_summary": None}
    detail = explain_place(sl, weather, scores, intent)[:4]
    return {
        **rec,
        **ui,
        "why": ui["concise_explanation_lines"],
        "why_detailed": detail,
    }


def build_daytrip_payload(
    *,
    weather: dict[str, Any],
    match_result: dict[str, Any],
    intent: dict[str, str],
    trip_context: dict[str, Any] | None = None,
    pass_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    match_from_api 결과 + 날씨 원본 + 사용자 의도 → Plan A/B 및 메타.
    """
    trip_context = trip_context or {}
    pass_context = pass_context or {}
    scores = match_result["weather"]
    user_lat = float(match_result["user_coords"]["lat"])
    user_lng = float(match_result["user_coords"]["lng"])
    if trip_context.get("user_lat") is not None:
        user_lat = float(trip_context["user_lat"])
    if trip_context.get("user_lng") is not None:
        user_lng = float(trip_context["user_lng"])
    raw_recs = match_result["recommendations"]

    adjusted = with_adjusted_scores(raw_recs, intent)
    tourpass_active = _pass_context_active(pass_context)
    if tourpass_active:
        adjusted = _tourpass_priority_adjusted(adjusted)
    pool = _pick_pool(adjusted, 44 if tourpass_active else 28)
    n = target_place_count(intent["duration"])
    n = min(n, len(pool)) if pool else 0

    pp = float(weather.get("precip_prob", 0) or 0)
    trip_hour = int(weather.get("hour") if weather.get("hour") is not None else datetime.now().hour)
    trip_minute = int(weather.get("minute", 0) or 0)
    date_iso = (
        str(trip_context.get("current_date_iso") or weather.get("current_date_iso") or "")
        .strip()
        or None
    )
    meal_pref = str(
        trip_context.get("meal_preference") or intent.get("meal_preference") or "none"
    )
    mc = build_meal_context(trip_hour, trip_minute)
    natural_route_mode = not tourpass_active
    route_judge_decision: dict[str, Any] | None = None
    if natural_route_mode:
        route_judge_decision = judge_natural_route_roles(
            meal_context=mc,
            duration=intent["duration"],
            weather=weather,
            intent=intent,
            pool_counts=_pool_category_counts(pool),
        )
        roles_ov = list(route_judge_decision.get("roles") or [])
    else:
        roles_ov = step_roles_for_meal_context(mc, intent["duration"])
    use_meal_driven = natural_route_mode and roles_ov is not None
    meal_strict = mc.requires_verified_meal_place
    # 48% 이상이면 메인 코스를 실내 후보 풀에서 먼저 구성(50~60% ‘실내·혼합 우선’에 맞춤)
    rainy_main = pp >= 48.0

    pool_for_a = pool
    if rainy_main:
        in_ranked = [d for d in adjusted if (d.get("category") or "") == "indoor"]
        if in_ranked:
            pool_for_a = in_ranked[: max(40, n * 5)]

    names_a: set[str] = set()
    plan_a_raw: list[dict[str, Any]] = []
    plan_a_shape_reason: str | None = None
    plan_a_roles: list[str] = []
    meal_insufficient = False
    if n > 0:
        plan_a_raw, plan_a_roles, plan_a_shape_reason = build_outing_plan_places(
            pool_for_a,
            intent=intent,
            weather=weather,
            user_lat=user_lat,
            user_lng=user_lng,
            exclude_names=set(),
            hour=trip_hour,
            roles_override=roles_ov,
            skip_template_exceptions=natural_route_mode or use_meal_driven,
            meal_substitution_mode="strict" if meal_strict else "default",
        )
        if plan_a_raw and route_judge_decision and natural_route_mode:
            plan_a_shape_reason = str(route_judge_decision.get("reason") or plan_a_shape_reason)
        if plan_a_raw and tourpass_active:
            plan_a_raw, _tp_changed = _promote_tourpass_plan_mix(
                plan_a_raw,
                plan_a_roles,
                pool_for_a,
                user_lat,
                user_lng,
            )
            if _tp_changed:
                plan_a_shape_reason = "tourpass_priority_mix"
        if plan_a_raw:
            plan_a_raw, meal_insufficient = inject_meal_places_for_plan(
                plan_a_raw,
                plan_a_roles,
                anchor_lat=user_lat,
                anchor_lng=user_lng,
                meal_ctx=mc,
            )
        names_a = {p["name"] for p in plan_a_raw}
        if not plan_a_raw and raw_recs:
            take = min(n, len(raw_recs))
            plan_a_raw = nearest_neighbor_order(raw_recs[:take], user_lat, user_lng)[
                :take
            ]
            plan_a_roles = ["main_spot"] + ["secondary_spot"] * (len(plan_a_raw) - 1)
            plan_a_shape_reason = plan_a_shape_reason or "fallback_ranking_only"
            names_a = {p["name"] for p in plan_a_raw}

    env_tr = environment_triggers(weather, scores)
    oh = outdoor_heavy_trigger(plan_a_raw, intent["duration"])
    if oh:
        env_tr = env_tr + [oh]

    plan_b_raw, plan_b_roles, _plan_b_shape = build_outing_plan_places(
        pool,
        intent=intent,
        weather=weather,
        user_lat=user_lat,
        user_lng=user_lng,
        exclude_names=set(names_a),
        hour=trip_hour,
    )
    if not plan_b_raw and pool:
        fallback = [p for p in pool if p["name"] not in names_a][:n]
        plan_b_raw = nearest_neighbor_order(fallback, user_lat, user_lng)
        plan_b_roles = ["main_spot"] + ["secondary_spot"] * (len(plan_b_raw) - 1)
    if not plan_b_raw and raw_recs and names_a:
        alt = [p for p in raw_recs if p["name"] not in names_a][: max(n, 2)]
        if alt:
            plan_b_raw = nearest_neighbor_order(alt, user_lat, user_lng)[:n]
            plan_b_roles = ["main_spot"] + ["secondary_spot"] * (len(plan_b_raw) - 1)

    names_ab = set(names_a) | {p["name"] for p in plan_b_raw}

    # Diversity guard: reject plan_b if ≥50% of places overlap with plan_a
    if plan_b_raw and names_a:
        b_names = {p["name"] for p in plan_b_raw}
        overlap = b_names & names_a
        if len(overlap) >= max(1, len(b_names) * 0.5):
            # Try deeper in the pool for truly different places
            deeper = [p for p in adjusted if p["name"] not in names_a][:n * 2]
            if len(deeper) >= n:
                plan_b_raw = nearest_neighbor_order(deeper[:n], user_lat, user_lng)
                plan_b_roles = ["main_spot"] + ["secondary_spot"] * (len(plan_b_raw) - 1)
            else:
                plan_b_raw = []
                plan_b_roles = []
        names_ab = set(names_a) | {p["name"] for p in plan_b_raw}

    plan_c_raw: list[dict[str, Any]] = []
    plan_c_roles: list[str] = []
    if len(pool) > len(names_ab):
        plan_c_raw, plan_c_roles, _ = build_outing_plan_places(
            pool,
            intent=intent,
            weather=weather,
            user_lat=user_lat,
            user_lng=user_lng,
            exclude_names=set(names_ab),
            hour=trip_hour,
        )
    if plan_c_raw:
        set_c = frozenset(p["name"] for p in plan_c_raw)
        set_b = frozenset(p["name"] for p in plan_b_raw) if plan_b_raw else frozenset()
        # Reject plan_c if exact match OR ≥50% overlap with plan_a or plan_b
        overlap_a = set_c & frozenset(names_a)
        overlap_b = set_c & set_b
        if (set_c == frozenset(names_a)
                or (plan_b_raw and set_c == set_b)
                or len(overlap_a) >= max(1, len(set_c) * 0.5)
                or (plan_b_raw and len(overlap_b) >= max(1, len(set_c) * 0.5))):
            plan_c_raw = []

    def avg_score(ps: list[dict[str, Any]]) -> float:
        if not ps:
            return 0.0
        return round(sum(p["score"] for p in ps) / len(ps), 3)

    plan_a_places = [serialize_place(p, weather, scores, intent) for p in plan_a_raw]
    plan_b_places = [serialize_place(p, weather, scores, intent) for p in plan_b_raw]
    plan_c_places = [serialize_place(p, weather, scores, intent) for p in plan_c_raw]

    def _attach_roles(places: list[dict[str, Any]], roles: list[str]) -> None:
        for i, pl in enumerate(places):
            if i < len(roles):
                pl["step_role"] = roles[i]

    _attach_roles(plan_a_places, plan_a_roles)
    _attach_roles(plan_b_places, plan_b_roles)
    _attach_roles(plan_c_places, plan_c_roles)

    sky_map = {1: "맑음", 3: "구름많음", 4: "흐림"}
    cur_date_disp = date_iso or ""
    if not cur_date_disp and weather.get("base_date"):
        bd = str(weather["base_date"])
        if len(bd) == 8:
            cur_date_disp = f"{bd[:4]}-{bd[4:6]}-{bd[6:8]}"

    weather_summary = {
        "temp": weather["temp"],
        "precip_prob": weather["precip_prob"],
        "sky": weather["sky"],
        "sky_text": sky_map.get(int(weather.get("sky", 1)), "알수없음"),
        "dust": weather["dust"],
        "base_date": weather.get("base_date"),
        "base_time": weather.get("base_time"),
        "hour": weather.get("hour"),
        "minute": trip_minute,
    }

    input_summary = {
        "companion": intent["companion"],
        "trip_goal": intent["trip_goal"],
        "duration": intent["duration"],
        "transport": intent["transport"],
        "adult_count": intent.get("adult_count", "1"),
        "child_count": intent.get("child_count", "0"),
        "city": match_result["city"],
        "current_time": f"{trip_hour:02d}:{trip_minute:02d}",
        "current_date": cur_date_disp,
        "user_location": {"lat": user_lat, "lng": user_lng},
        "meal_preference": meal_pref,
    }
    _pc = pass_context
    if _pc:
        from lib.pass_quest import pass_context_active

        input_summary["tourpass_mode"] = pass_context_active(_pc)
        input_summary["tourpass_ticket_type"] = str(
            _pc.get("tourpass_ticket_type") or "none"
        )
        input_summary["benefit_priority"] = str(_pc.get("benefit_priority") or "none")
        pg = str(_pc.get("pass_goal") or "").strip()
        if pg:
            input_summary["pass_goal"] = pg
        input_summary["purchased_status"] = str(
            _pc.get("purchased_status") or "not_planned"
        )

    plan_a_checks = checks_for_plan(plan_a_raw, intent, weather, scores, "A")
    plan_b_checks = checks_for_plan(plan_b_raw, intent, weather, scores, "B")

    trigger_conditions = list(env_tr)
    b_indoor = all(_is_indoor_heavy_candidate(p) for p in plan_b_raw) if plan_b_raw else False

    generated_at = datetime.now(timezone.utc).isoformat()
    _trip_band_detail = trip_detail_band(trip_hour)
    _trip_notice = trip_context_consumer_note(_trip_band_detail)
    _rank_scores = [float(x.get("score", 0.0)) for x in raw_recs]

    _enriched = [
        _enrich_recommendation_row(
            r, weather, scores, intent, rank_index=i, peer_scores=_rank_scores
        )
        for i, r in enumerate(raw_recs)
    ]
    today_pitch: str = ""
    today_pitch_src: str = "none"
    if _enriched:
        from lib.today_course_pitch import (
            build_struct_from_top_recommendation,
            generate_today_course_pitch,
        )

        _struct = build_struct_from_top_recommendation(
            _enriched[0], weather, scores, intent
        )
        today_pitch, today_pitch_src = generate_today_course_pitch(_struct)

    top_itinerary: list[dict[str, Any]] = []
    if plan_a_raw and plan_a_roles:
        top_itinerary = build_itinerary_for_course(
            plan_a_raw,
            plan_a_roles,
            start_local=trip_start_datetime(date_iso, trip_hour, trip_minute),
            duration_key=intent["duration"],
            transport=intent["transport"],
            meal_ctx=mc,
            meal_preference=meal_pref,
            time_basis_line=mc.basis_line,
        )

    time_banner = "현재 시간 기반으로 구성된 코스입니다"
    pool_cat = _pool_category_counts(pool)

    out: dict[str, Any] = {
        "input_summary": input_summary,
        "user_coords": {"lat": user_lat, "lng": user_lng},
        "today_course_pitch": today_pitch,
        "today_course_pitch_source": today_pitch_src,
        "weather_summary": weather_summary,
        "plan_a": {
            "title": plan_title("A", intent, indoor=False),
            "places": plan_a_places,
            "score": avg_score(plan_a_raw),
            "why": plan_why_summary(plan_a_raw, weather, intent),
            "checks": plan_a_checks,
        },
        "plan_b": {
            "title": plan_title("B", intent, indoor=b_indoor),
            "places": plan_b_places,
            "score": avg_score(plan_b_raw),
            "why": plan_why_summary(plan_b_raw, weather, intent),
            "checks": plan_b_checks,
            "trigger_conditions": trigger_conditions,
        },
        "plan_c": {
            "title": "또 다른 동선 · 둘러보기 조합",
            "places": plan_c_places,
            "score": avg_score(plan_c_raw),
            "why": plan_why_summary(plan_c_raw, weather, intent) if plan_c_raw else [],
            "checks": checks_for_plan(plan_c_raw, intent, weather, scores, "C")
            if plan_c_raw
            else [],
        },
        "meta": {
            "generated_at": generated_at,
            "confidence_notes": [],
            "not_real_time_limitations": [],
            "trip_feasibility_notice": _trip_notice,
            "time_based_banner": time_banner,
            "meal_data_insufficient": meal_insufficient,
            "pool_categories": pool_cat,
            "course_shape": {
                "plan_a_reason": plan_a_shape_reason,
                "plan_a_step_roles": list(plan_a_roles),
                "time_band": time_band_for_hour(trip_hour),
                "time_band_detail": _trip_band_detail,
                "trip_hour": trip_hour,
                "trip_minute": trip_minute,
                "meal_phase": mc.phase,
                "route_judge": route_judge_decision,
                "tourpass_priority": bool(tourpass_active),
                "plan_a_tourpass_hits": _tourpass_hits(plan_a_raw),
                "plan_a_tourpass_total": len(plan_a_raw),
            },
        },
        "itinerary": top_itinerary,
        "meal_context": {
            "phase": mc.phase,
            "basis_line": mc.basis_line,
            "clock_label": mc.clock_label,
            "requires_verified_meal_place": mc.requires_verified_meal_place,
        },
        "city": match_result["city"],
        "weather": {
            "temp": weather["temp"],
            "precip_prob": weather["precip_prob"],
            "sky": weather["sky"],
            "sky_text": weather_summary["sky_text"],
            "dust": weather["dust"],
            "hour": trip_hour,
            "minute": trip_minute,
            "current_date_iso": date_iso,
        },
        "scores": scores,
        "total_fetched": match_result["total_fetched"],
        "recommendations": _enriched,
        "main_scoring_model": {
            **(match_result.get("main_scoring_model") or {}),
            "tourpass_course_priority": bool(tourpass_active),
            "route_judge": route_judge_decision,
            "plan_a_tourpass_hits": _tourpass_hits(plan_a_raw),
            "plan_a_tourpass_total": len(plan_a_raw),
        },
    }
    out.update(build_consumer_course_view(out))
    from lib.pass_quest import attach_pass_quest_to_payload

    # Google Places 동기 호출이 많으면 SSL·재시도로 /api/recommend 가 타임아웃될 수 있어 기본은 끔.
    # 필요 시 SYNC_ENRICH_COURSE_STEPS_MAX=3~6 (로컬·네트워크 여유 있을 때만)
    try:
        sync_steps_max = max(0, min(8, int(os.getenv("SYNC_ENRICH_COURSE_STEPS_MAX", "0"))))
    except ValueError:
        sync_steps_max = 0
    if sync_steps_max > 0:
        try:
            from lib.review_features import enrich_consumer_course_steps_google_meta

            enrich_consumer_course_steps_google_meta(
                out,
                intent,
                hour=trip_hour,
                ref_lat=user_lat,
                ref_lng=user_lng,
                max_fetch=sync_steps_max,
            )
        except Exception as e:
            import sys

            from lib.config import settings

            if settings.debug:
                print(f"[daytrip] SYNC_ENRICH_COURSE_STEPS_MAX 보강 실패: {e}", file=sys.stderr)

    attach_pass_quest_to_payload(out, pass_context or {}, intent=intent, weather=weather)
    return out
