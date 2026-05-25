# -*- coding: utf-8 -*-
"""
Google Places API (New) 연동
- 특정 좌표 근처 식당/카페 검색 (코스 추천)
- 장소명으로 리뷰 검색 (메인 장소 리뷰)

참고: 신규 장소는 rating/userRatingCount가 비어 있는 경우가 많아,
과도한 품질 필터는 빈 목록만 만들 수 있음 → 완화함.
"""
from __future__ import annotations

# `python lib/places.py` 직접 실행 시에도 프로젝트 루트가 path에 오도록 함
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import math
from typing import Any
import time
from urllib.parse import quote

from lib.citytour_restaurant_client import fetch_citytour_restaurant_candidates
from lib.config import request_get, request_post, settings
from lib.intent_normalize import normalize_intent
from lib.next_course_scoring import rank_next_places
from lib.restaurant_candidates import merge_restaurant_candidate_lists
from lib.scoring import calc_weather_score

FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.rating",
    "places.userRatingCount",
    "places.formattedAddress",
    "places.location",
    "places.currentOpeningHours",
    "places.photos",
    "places.types",
    "places.priceLevel",
    "places.googleMapsUri",
    # 근처 검색에서도 리뷰 스니펫이 오면 다음 코스 카드 등에 활용 가능
    "places.reviews",
])

REVIEW_MASK = ",".join([
    "places.id",
    "places.name",
    "places.displayName",
    "places.rating",
    "places.userRatingCount",
    "places.reviews",
    "places.photos",
    "places.websiteUri",
    "places.googleMapsUri",
    "places.currentOpeningHours",
])

# Place Details (GET /v1/places/{id}) 필드마스크 — 접두사 없음
PLACE_DETAIL_REVIEW_MASK = ",".join([
    "id",
    "displayName",
    "formattedAddress",
    "location",
    "rating",
    "userRatingCount",
    "reviews",
    "photos",
    "websiteUri",
    "googleMapsUri",
    "currentOpeningHours",
])

_cache: dict = {}
CACHE_TTL = 1800
_last_text_search_error: str | None = None


def _cache_get(key: str):
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def _cache_set(key, data):
    _cache[key] = (time.time(), data)


def _mostly_korean_text(text: str) -> bool:
    """리뷰 노출 시 한국어 위주로 보이도록 할 때 사용(간단 휴리스틱)."""
    if not text or len(text) < 4:
        return False
    hangul = sum(1 for c in text if "\uac00" <= c <= "\ud7a3")
    return hangul >= max(4, int(len(text) * 0.22))


def _parse_reviews(raw: list, *, limit: int | None = None) -> list[dict]:
    """
    리뷰 파싱 — 본문 없는 항목 제외.
    정렬: 최신(publishTime) 우선, 동일 시점대에서는 한국어 비중이 높은 글을 앞에.
    Google Places는 상세 조회 시 reviews 최대 5개까지 돌려줄 수 있음 → limit 으로 상위 N만 노출.
    """
    rows: list[dict[str, Any]] = []
    for r in raw or []:
        text = r.get("text", {}).get("text", "").strip()
        if not text:
            continue
        pt = str(r.get("publishTime", "") or "")
        rows.append({
            "author": r.get("authorAttribution", {}).get("displayName", "익명"),
            "rating": r.get("rating", 0),
            "text": text,
            "relative": r.get("relativePublishTimeDescription", ""),
            "_pt": pt,
            "_ko": _mostly_korean_text(text),
        })

    # 최신(publishTime) 우선, 동일 시각대에서는 한국어 리뷰를 앞에
    rows.sort(key=lambda x: (x["_pt"], x["_ko"]), reverse=True)

    result: list[dict[str, Any]] = []
    cap = limit if limit is not None else None
    for item in rows:
        out = {k: v for k, v in item.items() if not str(k).startswith("_")}
        result.append(out)
        if cap is not None and len(result) >= cap:
            break
    return result


def _place_id_for_details(place: dict) -> str:
    """searchText/주변검색 응답에서 Place Details URL 용 ID 추출."""
    pid = (place.get("id") or "").strip()
    if pid:
        return pid
    name = (place.get("name") or "").strip()
    if name.startswith("places/"):
        return name[len("places/") :].strip()
    return ""


def _photo_proxy_url(photo_name: str | None, *, max_height_px: int = 520) -> str | None:
    ref = str(photo_name or "").strip()
    if not ref or not ref.startswith("places/") or "/photos/" not in ref:
        return None
    h = max(160, min(1200, int(max_height_px or 520)))
    return f"/api/place-photo?name={quote(ref, safe='')}&maxHeightPx={h}"


def _first_photo_url(place: dict, *, max_height_px: int = 520) -> str | None:
    photos = place.get("photos") or []
    if not isinstance(photos, list) or not photos:
        return None
    return _photo_proxy_url((photos[0] or {}).get("name"), max_height_px=max_height_px)


def _fetch_place_details_for_reviews(place_id: str) -> dict | None:
    """GET Place Details — searchText보다 reviews 리스트가 채워지는 경우가 많음."""
    if not settings.google_places_key or not place_id:
        return None
    url = f"{settings.google_places_v1_root}/places/{quote(place_id, safe='')}"
    headers = {
        "X-Goog-Api-Key": settings.google_places_key,
        "X-Goog-FieldMask": PLACE_DETAIL_REVIEW_MASK,
    }
    try:
        r = request_get(url, headers=headers, timeout=12, verify=settings.requests_ssl_verify)
        if not r.ok:
            if settings.debug:
                print(
                    f"[places] place details HTTP {r.status_code} id={place_id[:24]}…",
                    file=sys.stderr,
                )
            return None
    except Exception as e:
        if settings.debug:
            print(f"[places] place details error: {e}", file=sys.stderr)
        return None
    try:
        return dict(r.json())
    except Exception:
        return None


def _places_text_search_url() -> str:
    return f"{settings.google_places_v1_root}/places:searchText"


def next_types(target_category: str, hour: int) -> list[str]:
    if target_category == "restaurant":
        if 17 < hour <= 22:
            return ["restaurant", "korean_restaurant", "japanese_restaurant"]
        return ["restaurant", "korean_restaurant", "chinese_restaurant"]
    if target_category == "cafe":
        return ["cafe", "coffee_shop", "bakery"]
    if target_category == "attraction":
        return ["tourist_attraction", "museum", "park", "art_gallery"]
    return ["restaurant", "korean_restaurant"]


def _cache_key(lat: float, lng: float, types: list, trip_goal: str) -> str:
    return f"{round(lat, 3)}_{round(lng, 3)}_{trip_goal}_{'_'.join(types[:2])}"


def _expected_meal_from_types(types: list[str]) -> bool:
    """검색 타입이 식사 위주면 True, 카페·베이커리 위주면 False."""
    if not types:
        return True
    if types[0] in ("cafe", "coffee_shop", "bakery"):
        return False
    return True


def _display_name(p: dict) -> str:
    dn = p.get("displayName") or {}
    if isinstance(dn, dict):
        return str(dn.get("text") or "").strip()
    return str(dn).strip()


def _haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    """두 좌표 간 대략 거리(미터)."""
    r = 6371000.0
    p1 = math.radians(a_lat)
    p2 = math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def _pick_closest_place_with_distance(
    places: list[dict], lat: float, lng: float
) -> tuple[dict | None, float | None]:
    """좌표가 있는 후보 중 기준점과 가장 가까운 장소와 거리(미터). 없으면 (None, None)."""
    best: dict | None = None
    best_d: float | None = None
    for p in places:
        loc = p.get("location") or {}
        try:
            plat = float(loc.get("latitude"))
            plng = float(loc.get("longitude"))
        except (TypeError, ValueError):
            continue
        d = _haversine_m(lat, lng, plat, plng)
        if best_d is None or d < best_d:
            best_d = d
            best = p
    return best, best_d


def _search_text_places(payload: dict) -> list[dict]:
    """Places searchText 호출 — 실패 시 빈 리스트."""
    global _last_text_search_error
    if not settings.google_places_key:
        _last_text_search_error = "GOOGLE_PLACES_KEY missing"
        return []
    _last_text_search_error = None
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings.google_places_key,
        "X-Goog-FieldMask": REVIEW_MASK,
    }
    try:
        r = request_post(
            _places_text_search_url(),
            json=payload,
            headers=headers,
            timeout=10,
            verify=settings.requests_ssl_verify,
        )
        if not r.ok:
            _last_text_search_error = f"HTTP {r.status_code}: {r.text[:180]}"
            if settings.debug:
                print(
                    f"[places] searchText HTTP {r.status_code} body={r.text[:200]!r}",
                    file=sys.stderr,
                )
            return []
        return list(r.json().get("places") or [])
    except Exception as e:
        _last_text_search_error = str(e)
        if settings.debug:
            print(f"[places] searchText error: {e}", file=sys.stderr)
        return []


def _should_skip_low_quality(rating: float | None, reviews: int) -> bool:
    """평점·리뷰가 충분히 있을 때만 매우 낮은 곳 제외."""
    if rating is None:
        return False
    if rating < 2.0:
        return True
    if reviews >= 5 and rating < 2.5:
        return True
    return False


def _search_nearby_raw(
    included_types: list[str],
    lat: float,
    lng: float,
    radius_m: float,
    max_results: int,
) -> list[dict]:
    if not settings.google_places_key:
        return []

    payload = {
        "includedTypes": included_types[:10],
        "maxResultCount": max_results,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": float(radius_m),
            }
        },
        "rankPreference": "POPULARITY",
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings.google_places_key,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    r = request_post(
        settings.google_places_search_url,
        json=payload,
        headers=headers,
        timeout=12,
        verify=settings.requests_ssl_verify,
    )
    if not r.ok:
        if settings.debug:
            print(
                f"[places] HTTP {r.status_code} (types={included_types[:2]})",
                file=sys.stderr,
            )
        r.raise_for_status()
    return list(r.json().get("places") or [])


def _raw_to_results(raw: list[dict]) -> list[dict]:
    results: list[dict] = []
    for p in raw:
        rating_v = p.get("rating")
        try:
            rating = float(rating_v) if rating_v is not None else None
        except (TypeError, ValueError):
            rating = None
        try:
            reviews = int(p.get("userRatingCount") or 0)
        except (TypeError, ValueError):
            reviews = 0

        if _should_skip_low_quality(rating, reviews):
            continue

        name = _display_name(p)
        if not name:
            continue

        loc = p.get("location") or {}
        try:
            plat = float(loc.get("latitude"))
            plng = float(loc.get("longitude"))
        except (TypeError, ValueError):
            continue

        photo_url = _first_photo_url(p, max_height_px=420)

        open_now = None
        oh = p.get("currentOpeningHours") or {}
        if "openNow" in oh:
            open_now = oh["openNow"]

        r_show = float(rating) if rating is not None else 0.0
        pid = p.get("id")
        rev_raw = p.get("reviews") or []
        rev_parsed = _parse_reviews(list(rev_raw), limit=3) if rev_raw else []
        results.append({
            "place_id": str(pid) if pid is not None else "",
            "name": name,
            "address": p.get("formattedAddress") or "",
            "rating": r_show,
            "review_count": reviews,
            "open_now": open_now,
            "photo_url": photo_url,
            "google_maps": p.get("googleMapsUri", ""),
            "types": p.get("types") or [],
            "lat": plat,
            "lng": plng,
            "reviews": rev_parsed,
            "source_type": "google_places",
            "source_mix": "google_places",
            "merged_candidate_flag": False,
            "public_data_match": False,
        })

    results.sort(
        key=lambda x: (x["rating"], x["review_count"]),
        reverse=True,
    )
    return results


def _merge_public_restaurants(
    google_results: list[dict],
    lat: float,
    lng: float,
    max_results: int,
) -> list[dict]:
    ct = fetch_citytour_restaurant_candidates(lat, lng, max_results=max_results)
    merged = merge_restaurant_candidate_lists(google_results, ct, lat, lng)
    out: list[dict] = []
    for r in merged:
        row = dict(r)
        row.setdefault("source_type", "google_places")
        row.setdefault("source_mix", "google_places")
        row.setdefault("merged_candidate_flag", False)
        row.setdefault("public_data_match", False)
        out.append(row)
    return out


def fetch_continuation_candidates(
    lat: float,
    lng: float,
    included_types: list[str],
    max_results: int = 14,
) -> tuple[list[dict], float, bool, str | None]:
    """
    코스 이어가기용 Places 후보만 수집. 반경·타입을 단계적으로 완화한다.
    반환: (결과, 사용한 반경 m, 완화 여부, 메모)
    """
    radii = (8000.0, 14000.0, 22000.0)
    wants_restaurant = any(
        t in {"restaurant", "korean_restaurant", "chinese_restaurant", "meal_takeaway"}
        for t in included_types
    )

    if not settings.google_places_key:
        if not wants_restaurant:
            return [], 0.0, True, "GOOGLE_PLACES_KEY 미설정"
        merged = _merge_public_restaurants([], lat, lng, max_results)
        if merged:
            return merged, 0.0, True, "GOOGLE_PLACES_KEY 미설정 · 공공 식당 데이터 보강"
        return [], 0.0, True, "GOOGLE_PLACES_KEY 미설정"

    for radius_m in radii:
        try:
            raw = _search_nearby_raw(included_types[:10], lat, lng, radius_m, max_results)
        except Exception:
            raw = []
        results = _raw_to_results(raw)
        if wants_restaurant:
            results = _merge_public_restaurants(results, lat, lng, max_results)
        if results:
            extras: list[str] = []
            if any(p.get("source_type") == "citytour_api" for p in results):
                extras.append("공공 식당 데이터 병합")
            note_fin = " · ".join(extras) if extras else None
            return results, radius_m, False, note_fin

    for relaxed in (
        ["restaurant", "cafe", "coffee_shop"],
        ["tourist_attraction", "park"],
        ["meal_takeaway", "bakery"],
    ):
        for radius_m in radii:
            try:
                raw = _search_nearby_raw(relaxed, lat, lng, radius_m, max_results)
            except Exception:
                raw = []
            results = _raw_to_results(raw)
            if any(t in {"restaurant", "meal_takeaway"} for t in relaxed):
                results = _merge_public_restaurants(results, lat, lng, max_results)
            if results:
                note = "후보 부족 시 검색 타입·반경 완화"
                if any(p.get("source_type") == "citytour_api" for p in results):
                    note += " · 공공 식당 데이터 병합"
                return results, radius_m, True, note

    merged_only = _merge_public_restaurants([], lat, lng, max_results)
    if merged_only:
        return merged_only, radii[-1], True, "주변 Places 결과 없음 · 공공 식당 데이터"

    return [], radii[-1], True, "주변 Places 결과 없음"


def fetch_next_places(
    lat: float,
    lng: float,
    current_category: str = "outdoor",
    hour: int = 12,
    radius_m: int = 10000,
    max_results: int = 12,
    intent: dict | None = None,
    scores: dict[str, Any] | None = None,
) -> list[dict]:
    """
    주어진 좌표 근처에서 다음 코스 장소 반환.
    거리·단계·품질·목적·날씨 보조 점수로 정렬하며 breakdown을 붙인다.
    """
    if not settings.google_places_key:
        return []

    intent_use = intent if intent is not None else normalize_intent(
        None, None, None, None
    )
    goal_key = intent_use.get("trip_goal", "healing")

    types = next_types(current_category, hour)
    expected_meal = _expected_meal_from_types(types)
    key = _cache_key(lat, lng, types, goal_key)

    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data

    raw: list[dict] = []
    try:
        raw = _search_nearby_raw(types[:3], lat, lng, radius_m, max_results)
    except Exception:
        raw = []

    if not raw:
        for fallback_types in (
            ["restaurant"],
            ["meal_takeaway", "restaurant"],
            ["cafe", "coffee_shop"],
        ):
            try:
                raw = _search_nearby_raw(fallback_types, lat, lng, radius_m, max_results)
            except Exception:
                raw = []
            if raw:
                expected_meal = _expected_meal_from_types(fallback_types)
                break

    results = _raw_to_results(raw)
    scores_use = scores
    if scores_use is None:
        scores_use = calc_weather_score(
            {
                "temp": 20.0,
                "precip_prob": 0.0,
                "sky": 1,
                "dust": 1,
                "hour": hour,
            }
        )

    ranked = rank_next_places(
        results,
        ref_lat=lat,
        ref_lng=lng,
        hour=hour,
        intent=intent_use,
        scores=scores_use,
        expected_meal=expected_meal,
    )
    _cache[key] = (time.time(), ranked)
    return ranked


def fetch_place_reviews(
    name: str,
    lat: float,
    lng: float,
    address: str = "",
    *,
    top_review_count: int = 3,
) -> dict:
    """
    장소명으로 Google Places searchText → 후보가 여러 개면 기준 좌표에 가장 가까운 곳 선택,
    이어서 Place Details(GET)로 reviews를 보강한다.

    searchText만으로 결과가 비는 경우가 있어,
    (1) 5km locationRestriction (2) 5km locationBias (3) 12km locationBias (4) 지역 제한 없음
    순으로 완화한다.

    UI용 최신 N개(기본 3, 최대 5 — API 상한에 맞춤).
    """
    if not settings.google_places_key:
        return {
            "rating": 0,
            "review_count": 0,
            "reviews": [],
            "reviews_shown": 0,
            "photo_url": None,
            "website": "",
            "google_maps": "",
            "place_id": "",
            "place_name": "",
            "place_address": "",
            "open_now": None,
            "places_status": "missing_key",
            "places_status_message": "GOOGLE_PLACES_KEY가 설정되지 않았어요.",
        }

    n_show = max(1, min(5, int(top_review_count or 3)))

    key = f"review_{name}_{round(lat, 3)}_{round(lng, 3)}_{n_show}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    city_hint = ""
    if address:
        parts = address.replace("충청남도 ", "").split()
        if parts:
            city_hint = f" {parts[0]}"

    text_query = f"{name}{city_hint}"
    lat_f, lng_f = float(lat), float(lng)

    def _circle(radius_m: float) -> dict[str, Any]:
        """locationBias에만 circle 사용 가능. locationRestriction에는 rectangle."""
        return {
            "circle": {
                "center": {"latitude": lat_f, "longitude": lng_f},
                "radius": float(radius_m),
            }
        }

    def _rect_from_radius(radius_m: float) -> dict[str, Any]:
        """searchText의 locationRestriction은 rectangle만 지원 — circle → bounding box 변환."""
        delta_lat = radius_m / 111_000.0
        delta_lng = radius_m / (111_000.0 * max(0.01, __import__("math").cos(__import__("math").radians(lat_f))))
        return {
            "rectangle": {
                "low": {"latitude": lat_f - delta_lat, "longitude": lng_f - delta_lng},
                "high": {"latitude": lat_f + delta_lat, "longitude": lng_f + delta_lng},
            }
        }

    strategies: list[tuple[str, float, int]] = [
        ("restriction", 5000.0, 3),
        ("bias", 5000.0, 5),
        ("bias", 12000.0, 5),
    ]

    def _search_with_query(tq: str) -> list[dict]:
        found: list[dict] = []
        for mode, radius_m, max_cnt in strategies:
            payload_sq: dict[str, Any] = {
                "textQuery": tq,
                "languageCode": "ko",
                "regionCode": "KR",
                "maxResultCount": max_cnt,
            }
            circ = _circle(radius_m)
            if mode == "restriction":
                payload_sq["locationRestriction"] = _rect_from_radius(radius_m)
            else:
                payload_sq["locationBias"] = circ
            found = _search_text_places(payload_sq)
            if found:
                break
        if not found:
            found = _search_text_places(
                {
                    "textQuery": tq,
                    "languageCode": "ko",
                    "regionCode": "KR",
                    "maxResultCount": 5,
                }
            )
        return found

    places = _search_with_query(text_query)

    if not places:
        msg = _last_text_search_error or "Google Places 검색 결과가 비어 있어요."
        status = "request_failed" if _last_text_search_error else "no_match"
        return {
            "rating": 0,
            "review_count": 0,
            "reviews": [],
            "reviews_shown": n_show,
            "photo_url": None,
            "website": "",
            "google_maps": "",
            "place_id": "",
            "place_name": "",
            "place_address": "",
            "open_now": None,
            "places_status": status,
            "places_status_message": msg,
        }

    def _best_pair(place_list: list[dict]) -> tuple[dict | None, float | None]:
        return _pick_closest_place_with_distance(place_list, lat_f, lng_f)

    p, dist_m = _best_pair(places)
    addr_clean = (address or "").replace("충청남도", "").strip()

    # 동명이인 완화: 첫 매칭이 멀면 장소명+주소로 재검색 후 더 가까운 쪽 채택
    _far_threshold_m = 4200.0
    if addr_clean and len(addr_clean) >= 4 and (dist_m is None or dist_m > _far_threshold_m):
        places_addr = _search_with_query(f"{name} {addr_clean}")
        if places_addr:
            p2, d2 = _best_pair(places_addr)
            if p2 and d2 is not None:
                if dist_m is None or d2 < dist_m:
                    p, dist_m = p2, d2

    if not p:
        return {}

    _max_accept_m = 5200.0
    if dist_m is not None and dist_m > _max_accept_m:
        return {
            "rating": 0,
            "review_count": 0,
            "reviews": [],
            "reviews_shown": n_show,
            "photo_url": None,
            "website": "",
            "google_maps": "",
            "place_id": pid if "pid" in locals() else "",
            "place_name": _display_name(p) if p else "",
            "place_address": (p or {}).get("formattedAddress", "") if p else "",
            "open_now": None,
            "place_match_distance_m": round(dist_m, 1),
            "places_status": "match_too_far",
            "places_status_message": "검색된 Google 장소가 코스 좌표와 너무 멀어요.",
        }

    pid = _place_id_for_details(p)
    detail = _fetch_place_details_for_reviews(pid) if pid else None

    raw_s = list(p.get("reviews") or [])
    raw_d = list((detail or {}).get("reviews") or [])
    pr_s = _parse_reviews(raw_s, limit=None)
    pr_d = _parse_reviews(raw_d, limit=None)

    if detail and raw_d and (len(pr_d) > len(pr_s) or (not pr_s and pr_d)):
        p_meta: dict[str, Any] = detail
        raw_use = raw_d
    else:
        p_meta = p
        raw_use = raw_s

    open_now = None
    oh = (p_meta.get("currentOpeningHours") or {})
    if "openNow" in oh:
        open_now = oh["openNow"]

    result = {
        "rating": p_meta.get("rating", 0),
        "review_count": p_meta.get("userRatingCount", 0),
        "reviews": _parse_reviews(list(raw_use), limit=n_show),
        "reviews_shown": n_show,
        "photo_url": _first_photo_url(p_meta, max_height_px=720) or _first_photo_url(p, max_height_px=720),
        "website": p_meta.get("websiteUri", ""),
        "google_maps": p_meta.get("googleMapsUri", ""),
        "place_id": p_meta.get("id") or pid,
        "place_name": _display_name(p_meta) or _display_name(p) or name,
        "place_address": p_meta.get("formattedAddress") or p.get("formattedAddress") or address,
        "place_lat": (p_meta.get("location") or p.get("location") or {}).get("latitude"),
        "place_lng": (p_meta.get("location") or p.get("location") or {}).get("longitude"),
        "open_now": open_now,
        "place_match_distance_m": round(dist_m, 1) if dist_m is not None else None,
        "places_status": "ok",
        "places_status_message": "",
    }
    _cache_set(key, result)
    return result
