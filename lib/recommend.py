# -*- coding: utf-8 -*-
"""
통합 추천 파이프라인 v2
- 충남 14개 시군 전체 병렬 수집
- destinations.json 수동 태그로 자동 태그 덮어씀 (하이브리드)
- 30분 인메모리 캐시 (API 과호출 방지)
"""
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from lib.config import request_get, settings
from lib.intent_normalize import normalize_intent
from lib.distance import calc_distance_score, get_user_coords
from lib.main_scoring import (
    adjust_components_for_precip_prob,
    adjust_main_score_for_party_duration,
    compute_main_components,
    contribution_points,
    explain_main_destination,
    weighted_main_score,
)
from lib.scoring import calc_weather_score
from lib.scoring_config import MAIN_WEIGHTS
from lib.auto_tag import auto_tag
from lib.storytelling_loader import load_storytelling_records
from lib.storytelling_match import match_storytelling_for_destination, storytelling_fields_for_api
from lib.review_features import enrich_main_recommendations_shortlist
from lib.venue_hours_policy import (
    build_opening_feasibility_meta,
    should_exclude_primary_recommendation,
    trip_detail_band,
)
from lib.tourpass_catalog import catalog_row_for_place, merge_pass_fields_into_place


SIGUNGU_CODES = {
    "공주": 1, "금산": 2, "논산": 3, "당진": 4,
    "보령": 5, "부여": 6, "서산": 7, "서천": 8,
    "아산": 9, "예산": 11, "천안": 12, "청양": 13,
    "태안": 14, "홍성": 15,
}

DESTINATIONS_PATH = Path(__file__).parent.parent / "data" / "destinations.json"

# ── 캐시 ──────────────────────────────────────────────
_cache: dict = {}          # { cache_key: (timestamp, data) }
CACHE_TTL = 1800           # 30분


def _cache_get(key: str):
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def _cache_set(key: str, data):
    _cache[key] = (time.time(), data)


# ── 수동 태그 로드 ─────────────────────────────────────
def _load_manual() -> dict:
    """destinations.json을 {name: dest} 딕셔너리로 반환"""
    with open(DESTINATIONS_PATH, encoding="utf-8") as f:
        items = json.load(f)
    return {item["name"]: item for item in items}


def _parse_tour_api_items(data: dict) -> list:
    response = data.get("response") or {}
    header = response.get("header") or {}
    if header.get("resultCode") != "0000":
        return []
    body = response.get("body") or {}
    items = body.get("items") or {}
    if not items:
        return []
    result = items.get("item") or []
    if isinstance(result, dict):
        result = [result]
    return [it for it in result if str(it.get("contenttypeid")) not in ("32", "38")]


def _fetch_city_via_bridge(sigungu_code: int, num: int) -> list | None:
    base_url = os.getenv("TOUR_FETCH_URL", "").strip().rstrip("/")
    if not base_url:
        return None

    try:
        r = request_get(
            f"{base_url}/__tour_area__",
            params={"sigunguCode": sigungu_code, "num": num},
            timeout=max(8.0, settings.tour_api_timeout_seconds + 5.0),
            verify=settings.requests_ssl_verify,
        )
        r.raise_for_status()
        return _parse_tour_api_items(r.json())
    except Exception:
        return None


# ── 단일 시군 수집 ─────────────────────────────────────
def _fetch_city(sigungu_code: int, num: int = 100) -> list:
    cache_key = f"city_{sigungu_code}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    bridged = _fetch_city_via_bridge(sigungu_code, num)
    if bridged is not None:
        _cache_set(cache_key, bridged)
        return bridged

    if not settings.tour_api_key:
        return []

    params = {
        "serviceKey":  settings.tour_api_key,
        "numOfRows":   num,
        "pageNo":      1,
        "MobileOS":    "ETC",
        "MobileApp":   "ChungnamTour",
        "_type":       "json",
        "areaCode":    34,
        "sigunguCode": sigungu_code,
        "arrange":     "C",
    }
    try:
        r = request_get(
            f"{settings.tour_base_url}/areaBasedList2",
            params=params,
            timeout=settings.tour_api_timeout_seconds,
            verify=settings.requests_ssl_verify,
        )
        result = _parse_tour_api_items(r.json())
    except Exception:
        result = []

    _cache_set(cache_key, result)
    return result


# ── 충남 전체 or 특정 도시 수집 ────────────────────────
def fetch_and_tag(city: str = "전체", num: int = 100) -> list:
    """
    city="전체"  → 14개 시군 병렬 수집 후 합산
    city="아산"  → 해당 도시만
    """
    manual = _load_manual()

    if city == "전체":
        codes = list(SIGUNGU_CODES.values())
    else:
        code = SIGUNGU_CODES.get(city)
        if code is None:
            raise ValueError(f"지원하지 않는 도시: {city}")
        codes = [code]

    # 병렬 수집
    raw_items = []
    with ThreadPoolExecutor(max_workers=min(14, max(1, len(codes)))) as pool:
        futures = {pool.submit(_fetch_city, code, num): code for code in codes}
        for future in as_completed(futures):
            raw_items.extend(future.result())

    # 자동 태깅 + 수동 태그 덮어쓰기 (하이브리드)
    result = []
    for item in raw_items:
        tagged = auto_tag(item)
        name   = tagged["name"]

        if name in manual:
            # 수동 태그로 교체 (날씨 가중치, 태그, 카피 등)
            manual_data = manual[name].copy()
            # TourAPI에서만 얻을 수 있는 정보는 유지
            manual_data.setdefault("image",   tagged.get("image", ""))
            manual_data.setdefault("address", tagged.get("address", ""))
            manual_data["source"] = "manual"
            result.append(manual_data)
        else:
            result.append(tagged)

    # 수동 데이터 중 TourAPI에 없는 것도 추가 (도시 필터 적용)
    api_names = {d["name"] for d in result}
    for name, dest in manual.items():
        if name not in api_names:
            if city != "전체":
                dc = str(dest.get("city") or "").strip()
                if dc != city:
                    continue
            dest = dest.copy()
            dest["source"] = "manual"
            result.append(dest)

    # TourAPI 실패(SSL 등)로 풀이 비면 수동 목록으로 폴백
    # 특정 시군 선택 시: destinations.json 의 city 가 선택과 일치하는 것만.
    # city=\"전체\" 일 때만 충남 전체 수동 데이터를 섞는다.
    if not result:
        for name, dest in manual.items():
            if city != "전체":
                dc = str(dest.get("city") or "").strip()
                if dc != city:
                    continue
            d = dest.copy()
            d["source"] = (
                "manual_fallback_city"
                if city != "전체"
                else "manual_fallback_any_city"
            )
            result.append(d)

    return result


def _is_tourpass_merchant_place(name: str | None) -> bool:
    """카탈로그(JSON·CSV)에서 가맹으로 표시된 관광 후보만."""
    row = catalog_row_for_place(str(name or "").strip())
    return row.get("tourpass_available") is True


def _tourpass_fit_score(row: dict) -> float:
    """투어패스 앱 모드의 설명 가능한 보정 점수."""
    if row.get("tourpass_available") is not True:
        return 0.18
    base = float(row.get("tourpass_confidence") or 0.45)
    cat = str(row.get("pass_category") or "unknown")
    cat_bonus = {
        "experience": 0.12,
        "cafe": 0.10,
        "local": 0.10,
        "restaurant": 0.10,
        "attraction": 0.08,
        "accommodation": 0.04,
    }.get(cat, 0.02)
    benefit = str(row.get("pass_benefit_type") or "unknown")
    benefit_bonus = {"free": 0.10, "discount": 0.07, "unknown": 0.03}.get(benefit, 0.0)
    return max(0.0, min(1.0, base + cat_bonus + benefit_bonus))


def _append_tourpass_tag(dest: dict, row: dict) -> dict:
    out = merge_pass_fields_into_place(dest)
    if row.get("tourpass_available") is True:
        tags = [str(t) for t in (out.get("tags") or [])]
        if "투어패스" not in tags:
            tags = ["투어패스"] + tags
        cat = str(row.get("pass_category") or "")
        if cat == "local" and "로컬" not in tags:
            tags.append("로컬")
        out["tags"] = tags[:8]
    return out


# ── 최종 매칭 ──────────────────────────────────────────
def match_from_api(
    weather: dict,
    city: str = "전체",
    top_n: int = 6,
    user_lat: float = None,
    user_lng: float = None,
    intent: dict | None = None,
    tourpass_mode: bool = False,
) -> dict:
    """
    가중치(설명 가능): weather_fit, goal_fit, distance_fit, time_fit, season_event_bonus.
    intent가 없으면 기본 의도(healing, half-day 등)로 처리한다.

    홈페이지 `/api/recommend`의 **장소 후보 랭킹**은 항상 이 규칙 파이프라인만 사용한다.
    tourpass_mode=True 이면 우선 카탈로그상 가맹(tourpass_available=True)만 쓰되,
    가맹이 한 건도 매칭되지 않으면(TourAPI 명칭·별칭 불일치 등) 전체 풀로 폴백한다.
    완성 코스 묶음·순서는 daytrip_planner·course_view에서 규칙으로 만들고,
    코스 단위 재정렬 ML은 lib.course_rerank(번들 있을 때만)에서 선택 적용한다.
    next_scene(/api/course)와 혼동하지 않는다.
    """
    scores = calc_weather_score(weather)
    destinations_all = fetch_and_tag(city)
    destination_source_counts = dict(
        Counter(str(d.get("source") or "tourapi") for d in destinations_all)
    )
    intent_use = intent if intent is not None else normalize_intent(
        None, None, None, None
    )

    tourpass_merchant_filter_relaxed = False
    destinations = destinations_all
    if tourpass_mode:
        merchants = [
            d for d in destinations_all if _is_tourpass_merchant_place(d.get("name"))
        ]
        if merchants:
            destinations = merchants
        else:
            # 카탈로그 키와 수집 장소명이 어긋나면 여기서 빈 풀 → 코스·Places 보강 전부 실패함
            destinations = destinations_all
            tourpass_merchant_filter_relaxed = True

    ref_city = city if city != "전체" else "아산"
    if user_lat is None or user_lng is None:
        user_lat, user_lng = get_user_coords(ref_city)

    hour = int(weather.get("hour", datetime.now().hour))
    _trip_band = trip_detail_band(hour)

    story_records = load_storytelling_records()

    results = []
    for dest in destinations:
        w = dest["weather_weights"]

        # Hard Filter (규칙 우선 — 홈페이지는 항상 이 경로만 사용)
        if scores["is_raining"] and dest["category"] == "outdoor":
            continue
        if scores["is_dust_bad"] and w.get("fine_dust_limit") == "good":
            continue

        excl, _excl_code = should_exclude_primary_recommendation(dest, _trip_band)
        if excl:
            continue

        lat = dest["coords"]["lat"]
        lng = dest["coords"]["lng"]
        dist_score, dist_km = calc_distance_score(lat, lng, user_lat, user_lng)

        components = compute_main_components(
            dest,
            weather,
            scores,
            intent_use,
            dist_score,
            hour=hour,
        )
        components = adjust_main_score_for_party_duration(
            dest, components, intent_use, dist_km
        )
        components = adjust_components_for_precip_prob(
            components, dest, float(weather.get("precip_prob", 0))
        )
        pass_row = catalog_row_for_place(dest.get("name"))
        tourpass_fit = _tourpass_fit_score(pass_row)
        if tourpass_mode:
            components["tourpass_fit"] = round(tourpass_fit, 4)
            # 기존 날씨·거리 설명력을 유지하되, 투어패스 앱 모드에서는 가맹 활용도를 눈에 띄게 반영한다.
            total = round(min(1.0, weighted_main_score(components) * 0.78 + tourpass_fit * 0.22), 4)
        else:
            total = weighted_main_score(components)
        contrib = contribution_points({k: v for k, v in components.items() if k in MAIN_WEIGHTS})
        if tourpass_mode:
            contrib["tourpass_fit"] = round(tourpass_fit * 0.22, 4)
        sm = (
            match_storytelling_for_destination(dest, story_records)
            if story_records
            else None
        )
        explain = explain_main_destination(
            dest, components, contrib, intent_use, weather, scores
        )
        if sm and sm.get("narrative_enrichment_line"):
            if float(sm.get("storytelling_match_confidence") or 0) >= 0.42:
                lines = list(explain.get("lines") or [])
                lines.append(str(sm["narrative_enrichment_line"]))
                explain = {**explain, "lines": lines[:5]}

        # 하위 호환: weather_score = 날씨 축 원시 적합도
        raw_w = components["weather_fit"]

        opening = build_opening_feasibility_meta(dest, _trip_band)
        dest_for_row = _append_tourpass_tag(dest, pass_row)
        row = {
            **dest_for_row,
            "score": total,
            "weather_score": round(raw_w, 3),
            "distance_score": dist_score,
            "distance_km": dist_km,
            "address": dest.get("address") or "",
            "score_breakdown": components,
            "score_contributions": contrib,
            "recommendation_explain": explain,
            "recommendation_summary": explain.get("summary", ""),
            "opening_feasibility": opening,
        }
        row.update(storytelling_fields_for_api(sm))
        results.append(row)

    results.sort(key=lambda x: x["score"], reverse=True)
    places_review_enrichment_limit = int(settings.main_places_review_max_fetch)
    try:
        enrich_main_recommendations_shortlist(
            results,
            intent_use,
            hour=hour,
            ref_lat=user_lat,
            ref_lng=user_lng,
            precip_prob=float(weather.get("precip_prob", 0) or 0),
            max_fetch=places_review_enrichment_limit,
        )
    except Exception:
        if settings.debug:
            import traceback

            print("[recommend] enrich_main_recommendations_shortlist failed:", file=sys.stderr)
            traceback.print_exc()
    results.sort(key=lambda x: x["score"], reverse=True)

    return {
        "city": city,
        "user_coords": {"lat": user_lat, "lng": user_lng},
        "weather": scores,
        "total_fetched": len(destinations),
        "recommendations": results[:top_n],
        "main_scoring_model": {
            "weights": ({**dict(MAIN_WEIGHTS), **({"tourpass_fit": 22} if tourpass_mode else {})}),
            "intent_applied": intent_use,
            "note": "휴리스틱 가중 합; 각 항목은 0~1 부분점수. 투어패스 모드에서는 가맹 활용도 보정(tourpass_fit)을 추가 반영합니다.",
            "tourpass_mode": bool(tourpass_mode),
            "tourpass_merchant_pool_only": bool(
                tourpass_mode and not tourpass_merchant_filter_relaxed
            ),
            "tourpass_merchant_filter_fallback": tourpass_merchant_filter_relaxed,
            "places_review_enrichment": "top_shortlist",
            "places_review_enrichment_limit": places_review_enrichment_limit,
            "destination_pool_count": len(destinations_all),
            "destination_source_counts": destination_source_counts,
        },
    }
