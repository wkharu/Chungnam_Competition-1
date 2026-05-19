# -*- coding: utf-8 -*-
"""
당일 코스 동선 템플릿: 관광지 나열이 아니라 '나들이 흐름'(장소→식사→카페/마무리) 위주로 슬롯을 채운다.
"""
from __future__ import annotations

from typing import Any, Literal

from lib.distance import haversine
from lib.venue_hours_policy import is_night_walk_friendly_dest, time_band_compat

VenueKind = Literal["meal", "cafe", "spot"]
StepRole = Literal[
    "main_spot",
    "meal",
    "cafe_rest",
    "secondary_spot",
    "finish",
    "night_walk",
    "late_night_rest",
]

_MEAL_TAGS = frozenset(
    {"맛집", "음식", "한식", "중식", "일식", "분식", "요리", "식당", "횟집", "고기"}
)
_CAFE_TAGS = frozenset({"카페", "디저트", "브런치", "커피", "베이커리", "티"})
_FESTIVAL_TAGS = frozenset({"축제", "행사", "이벤트"})
_CULTURE_TAGS = frozenset(
    {"전시", "문화", "박물관", "미술", "역사", "유적", "사찰", "성곽", "고건축"}
)


def _norm_tags(d: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for t in d.get("tags") or []:
        s = str(t).strip().lower()
        if s:
            out.add(s)
    return out


def infer_venue_kind(d: dict[str, Any]) -> VenueKind:
    """Places/공공/투어패스 데이터를 식당·카페·관광지로 안정 분류."""
    tags = _norm_tags(d)
    name = str(d.get("name") or "").lower()
    blob = " ".join([name, *tags])

    cafe_words = ("카페", "커피", "coffee", "cafe", "bakery", "베이커리", "디저트", "차")
    meal_words = (
        "식당",
        "한식",
        "중식",
        "일식",
        "분식",
        "레스토랑",
        "restaurant",
        "갈비",
        "국수",
        "곱창",
        "설렁탕",
        "보쌈",
        "횟집",
        "피자",
        "맥주",
        "브루어리",
    )
    bar_words = ("술집", "펍", "pub", "bar", "wine", "와인", "호프")

    if any(w in blob for w in bar_words):
        return "cafe"
    if tags & _CAFE_TAGS or any(w in blob for w in cafe_words):
        if (tags & _MEAL_TAGS or any(w in blob for w in meal_words)) and "카페" not in blob and "cafe" not in blob:
            return "meal"
        return "cafe"
    if tags & _MEAL_TAGS or any(w in blob for w in meal_words):
        return "meal"
    return "spot"

def meal_placeholder_dict(lat: float, lng: float) -> dict[str, Any]:
    """식사 후보를 채우지 못했을 때만 쓰는 자리 표시자(임의 장소명 생성 금지)."""
    return {
        "name": "식사 장소 데이터 부족",
        "category": "indoor",
        "tags": ["식사"],
        "coords": {"lat": float(lat), "lng": float(lng)},
        "address": "",
        "image": "",
        "score": 0.0,
        "weather_score": 0.5,
        "meal_data_insufficient": True,
        "copy": "주변에서 검증된 식당 목록을 찾지 못했어요. 지도 앱으로 직접 검색해 주세요.",
        "weather_weights": {"sunny": 0.5, "rainy": 0.5, "fine_dust_limit": "bad"},
        "golden_hour_bonus": False,
        "temp_range": {"min": -20, "max": 40},
    }


def cafe_placeholder_dict(lat: float, lng: float) -> dict[str, Any]:
    """카페 후보를 채우지 못했을 때 슬롯 역할을 보존하는 안내용 장소."""
    return {
        "name": "카페 장소 데이터 부족",
        "category": "indoor",
        "tags": ["카페", "휴식"],
        "coords": {"lat": float(lat), "lng": float(lng)},
        "address": "",
        "image": "",
        "score": 0.0,
        "weather_score": 0.5,
        "meal_data_insufficient": True,
        "copy": "주변에서 검증된 카페 목록을 찾지 못했어요. 지도 앱으로 직접 검색해 주세요.",
        "weather_weights": {"sunny": 0.5, "rainy": 0.7, "fine_dust_limit": "bad"},
        "golden_hour_bonus": False,
        "temp_range": {"min": -20, "max": 40},
    }


def _is_festival_heavy(d: dict[str, Any]) -> bool:
    return bool(_norm_tags(d) & _FESTIVAL_TAGS)


def _is_culture_heavy_dest(d: dict[str, Any]) -> bool:
    return bool(_norm_tags(d) & _CULTURE_TAGS)


def _is_late_night_rest_spot(d: dict[str, Any]) -> bool:
    """TourAPI 풀에 카페가 거의 없을 때 쓰는 늦은 시간·24시 휴식 휴리스틱."""
    if infer_venue_kind(d) == "cafe":
        return True
    if d.get("twenty_four_hour") or d.get("late_night_ok"):
        return True
    name = str(d.get("name") or "").lower()
    return "24" in name or "스터디" in name or "편의점" in name


def default_step_roles(duration: str) -> list[StepRole]:
    if duration == "2h":
        return ["main_spot", "meal"]  # meal 슬롯은 카페로 대체 가능
    if duration == "half-day":
        return ["main_spot", "meal", "cafe_rest"]
    return ["main_spot", "meal", "secondary_spot", "cafe_rest"]


def time_band_for_hour(hour: int) -> str:
    """API·로그용 시간대 구간(심야·새벽은 통합 night)."""
    return time_band_compat(int(hour))


def step_roles_for_clock_hour(
    hour: int,
    duration: str,
    intent: dict[str, Any],
    weather: dict[str, Any],
) -> list[StepRole]:
    """
    현재 시각·일정 길이·강수를 반영한 동선 슬롯 순서.
    반나절 템플릿을 만든 뒤 full-day는 한 단계 확장한다.
    """
    h = int(hour) % 24
    d = str(duration).strip().lower()
    if d not in ("2h", "half-day", "full-day"):
        d = "half-day"
    pp = float(weather.get("precip_prob", 0) or 0)

    if h >= 20 or h < 6:
        # 심야(20~02)·새벽(02~06): 산책·야외 자유 접근 → 늦게까지/24시 휴식형
        if d == "2h":
            base = ["night_walk", "late_night_rest"]
        elif d == "half-day":
            base = ["night_walk", "late_night_rest", "finish"]
        else:
            base = ["night_walk", "late_night_rest", "finish", "late_night_rest"]
    elif 9 <= h < 11:
        base = ["main_spot", "cafe_rest", "secondary_spot"]
    elif 11 <= h < 13:
        flip = (hash(str(intent.get("trip_goal", "healing"))) + h + int(pp)) % 2
        if flip:
            base = ["meal", "main_spot", "cafe_rest"]
        else:
            base = ["main_spot", "meal", "cafe_rest"]
    elif 13 <= h < 17:
        base = ["main_spot", "meal", "cafe_rest"]
    elif 17 <= h < 20:
        base = ["main_spot", "meal", "cafe_rest"]
    else:
        base = ["main_spot", "cafe_rest", "secondary_spot"]

    if d == "2h":
        return base[:2]
    if d == "half-day":
        return base[:3]
    if h >= 20 or h < 6:
        return base[:4]
    three = base[:3]
    if len(three) < 3:
        three = ["main_spot", "meal", "cafe_rest"]
    a, b, c = three[0], three[1], three[2]
    return [a, b, "secondary_spot", c]


def apply_template_exceptions(
    roles: list[StepRole],
    *,
    intent: dict[str, str],
    weather: dict[str, Any],
    pool: list[dict[str, Any]],
) -> tuple[list[StepRole], str | None]:
    """
    예외 시 템플릿·reason 코드 조정.
    reason은 디버그·메타용 (프론트는 소비자 문구 별도).
    """
    goal = intent.get("trip_goal", "healing")
    duration = intent.get("duration", "half-day")
    pp = float(weather.get("precip_prob", 0) or 0)

    kinds = [infer_venue_kind(p) for p in pool[: min(40, len(pool))]]
    meal_n = sum(1 for k in kinds if k == "meal")
    cafe_n = sum(1 for k in kinds if k == "cafe")

    r = list(roles)
    reason: str | None = None
    night_flow = any(x in ("night_walk", "late_night_rest") for x in r)

    if not night_flow and goal == "culture" and duration == "half-day":
        r = ["main_spot", "secondary_spot", "cafe_rest"]
        reason = "culture-heavy"
    elif not night_flow and goal == "culture" and duration == "2h":
        r = ["main_spot", "secondary_spot"]
        reason = "culture-heavy"
    elif not night_flow and goal == "culture" and duration == "full-day":
        r = ["main_spot", "secondary_spot", "meal", "cafe_rest"]
        reason = "culture-heavy"

    if any(_is_festival_heavy(p) for p in pool[:15]) and reason is None:
        reason = "festival_focus"

    if (
        not night_flow
        and duration == "2h"
        and pp >= 55
        and goal != "culture"
    ):
        r = ["main_spot", "cafe_rest"]
        reason = "rain_short_indoor_cafe"

    if (
        not night_flow
        and meal_n == 0
        and cafe_n == 0
        and reason not in ("culture-heavy",)
    ):
        if duration == "2h":
            r = ["main_spot", "secondary_spot"]
        elif duration == "half-day":
            r = ["main_spot", "secondary_spot", "finish"]
        else:
            r = ["main_spot", "meal", "secondary_spot", "finish"]
            for i, role in enumerate(r):
                if role == "meal":
                    r[i] = "secondary_spot"
        reason = reason or "no_nearby_meal_candidates"
    elif not night_flow and meal_n == 0 and cafe_n > 0:
        for i, role in enumerate(r):
            if role == "meal":
                r[i] = "cafe_rest"
        reason = reason or "meal_substituted_by_cafe"

    return r, reason


def _split_pool(pool: list[dict[str, Any]]) -> tuple[list[dict], list[dict], list[dict]]:
    spots: list[dict] = []
    meals: list[dict] = []
    cafes: list[dict] = []
    for d in pool:
        k = infer_venue_kind(d)
        if k == "meal":
            meals.append(d)
        elif k == "cafe":
            cafes.append(d)
        else:
            spots.append(d)
    for group in (spots, meals, cafes):
        group.sort(key=lambda x: float(x.get("score", 0) or 0), reverse=True)
    return spots, meals, cafes


def _pick_nearest(
    candidates: list[dict[str, Any]],
    cur_lat: float,
    cur_lng: float,
    used: set[str],
) -> dict[str, Any] | None:
    return _pick_nearest_role_aware(
        candidates, cur_lat, cur_lng, used, role="main_spot", night_mode=False
    )


def _pick_nearest_role_aware(
    candidates: list[dict[str, Any]],
    cur_lat: float,
    cur_lng: float,
    used: set[str],
    *,
    role: StepRole,
    night_mode: bool,
) -> dict[str, Any] | None:
    """야간에는 일반 관광(야외) 후보에 거리 페널티를 줘 카페·짧은 마무리 위주로 붙는다."""
    best: dict[str, Any] | None = None
    best_key: tuple[float, float] | None = None
    for d in candidates:
        nm = str(d.get("name") or "")
        if not nm or nm in used:
            continue
        lat = float(d.get("coords", {}).get("lat") or 0)
        lng = float(d.get("coords", {}).get("lng") or 0)
        if lat == 0 and lng == 0:
            continue
        dist = haversine(cur_lat, cur_lng, lat, lng)
        sc = float(d.get("score", 0) or 0)
        night_pen = 0.0
        if night_mode and role == "night_walk":
            cat = str(d.get("category") or "").lower()
            if cat == "outdoor" or is_night_walk_friendly_dest(d):
                night_pen = -2.8
            elif cat == "indoor":
                night_pen = 2.4
        elif night_mode and role in ("main_spot", "secondary_spot", "finish"):
            cat = str(d.get("category") or "").lower()
            if cat == "outdoor":
                night_pen = 3.8
        key = (dist + night_pen, -sc)
        if best is None or key < best_key:  # type: ignore[operator]
            best = d
            best_key = key
    return best


def build_outing_plan_places(
    pool: list[dict[str, Any]],
    *,
    intent: dict[str, Any],
    weather: dict[str, Any],
    user_lat: float,
    user_lng: float,
    exclude_names: set[str],
    hour: int | None = None,
    roles_override: list[StepRole] | None = None,
    skip_template_exceptions: bool = False,
    meal_substitution_mode: Literal["default", "strict"] = "default",
) -> tuple[list[dict[str, Any]], list[StepRole], str | None]:
    """
    점수 정렬된 pool에서 동선 슬롯을 채운다.
    반환: (places, step_roles, course_shape_reason)
    """
    hour_use = int(hour if hour is not None else weather.get("hour") or 12)
    minute_use = int(weather.get("minute", 0) or 0)
    weather_use = {**weather, "hour": hour_use, "minute": minute_use}
    duration = str(intent.get("duration", "half-day"))
    if roles_override:
        base_roles: list[StepRole] = list(roles_override)
        if skip_template_exceptions:
            roles, shape_reason = base_roles, "meal_time_driven"
        else:
            roles, shape_reason = apply_template_exceptions(
                base_roles,
                intent=intent,
                weather=weather_use,
                pool=pool,
            )
    else:
        roles, shape_reason = apply_template_exceptions(
            step_roles_for_clock_hour(hour_use, duration, intent, weather_use),
            intent=intent,
            weather=weather_use,
            pool=pool,
        )
    tod = hour_use * 60 + minute_use
    night_mode = tod >= 20 * 60 or tod < 6 * 60
    spots, meals, cafes = _split_pool([p for p in pool if p.get("name") not in exclude_names])

    used: set[str] = set(exclude_names)
    out_places: list[dict] = []
    out_roles: list[StepRole] = []
    cur_lat, cur_lng = user_lat, user_lng

    def candidates_for(role: StepRole) -> list[dict]:
        if role == "meal":
            return meals
        if role in ("cafe_rest", "finish"):
            return cafes
        if role == "night_walk":
            w = [
                s
                for s in spots
                if is_night_walk_friendly_dest(s)
                and str(s.get("name") or "") not in used
            ]
            return w if w else [s for s in spots if str(s.get("name") or "") not in used]
        if role == "late_night_rest":
            rests = [c for c in cafes if str(c.get("name") or "") not in used]
            if rests:
                return rests
            w = [
                s
                for s in spots
                if _is_late_night_rest_spot(s) and str(s.get("name") or "") not in used
            ]
            return w if w else [s for s in spots if str(s.get("name") or "") not in used]
        if role == "secondary_spot":
            return [s for s in spots if str(s.get("name") or "") not in used]
        return [s for s in spots if str(s.get("name") or "") not in used]

    for role in roles:
        cand = candidates_for(role)
        pick = _pick_nearest_role_aware(
            cand, cur_lat, cur_lng, used, role=role, night_mode=night_mode
        )
        role_use: StepRole = role

        if pick is None:
            if role == "meal":
                if meal_substitution_mode != "strict":
                    pick = _pick_nearest_role_aware(
                        cafes, cur_lat, cur_lng, used, role="cafe_rest", night_mode=night_mode
                    )
                    if pick is not None:
                        role_use = "cafe_rest"
                        shape_reason = shape_reason or "meal_substituted_by_cafe"
                if pick is None:
                    pick = meal_placeholder_dict(cur_lat, cur_lng)
                    role_use = "meal"
                    shape_reason = shape_reason or "meal_slot_placeholder"
            elif role == "cafe_rest":
                pick = _pick_nearest_role_aware(
                    cafes, cur_lat, cur_lng, used, role="cafe_rest", night_mode=night_mode
                )
                if pick is None:
                    pick = cafe_placeholder_dict(cur_lat, cur_lng)
                    role_use = "cafe_rest"
                    shape_reason = shape_reason or "cafe_slot_placeholder"
            elif role == "finish":
                pick = _pick_nearest_role_aware(
                    spots, cur_lat, cur_lng, used, role="finish", night_mode=night_mode
                )
                if pick is not None:
                    role_use = "finish"
            elif role in ("main_spot", "secondary_spot"):
                pick = _pick_nearest_role_aware(
                    spots, cur_lat, cur_lng, used, role=role, night_mode=night_mode
                )
            elif role == "night_walk":
                pick = _pick_nearest_role_aware(
                    spots, cur_lat, cur_lng, used, role="secondary_spot", night_mode=night_mode
                )
            elif role == "late_night_rest":
                pick = _pick_nearest_role_aware(
                    cafes + spots,
                    cur_lat,
                    cur_lng,
                    used,
                    role="cafe_rest",
                    night_mode=night_mode,
                )
                if pick is None:
                    pick = _pick_nearest_role_aware(
                        spots, cur_lat, cur_lng, used, role="finish", night_mode=night_mode
                    )

        if pick is None:
            continue

        nm = str(pick.get("name") or "")
        used.add(nm)
        out_places.append(pick)
        out_roles.append(role_use)
        cur_lat = float(pick.get("coords", {}).get("lat") or cur_lat)
        cur_lng = float(pick.get("coords", {}).get("lng") or cur_lng)

    if not out_places and pool:
        raw = [p for p in pool if p.get("name") not in exclude_names][:4]
        out_places = raw
        out_roles = ["main_spot"] + ["secondary_spot"] * (len(raw) - 1)
        shape_reason = shape_reason or "fallback_ranking_only"

    if len(out_places) >= 3:
        kinds = [infer_venue_kind(p) for p in out_places]
        if (
            all(k == "spot" for k in kinds)
            and shape_reason
            not in (
                "culture-heavy",
                "festival_focus",
                "no_nearby_meal_candidates",
                "fallback_ranking_only",
                "night_time_shape",
            )
        ):
            shape_reason = shape_reason or "spot_chain_exception"

    if any(r in ("night_walk", "late_night_rest") for r in out_roles):
        shape_reason = shape_reason or "night_time_shape"

    return out_places, out_roles, shape_reason


def consumer_label_for_role(role: str) -> str:
    return {
        "main_spot": "메인 장소",
        "meal": "식사",
        "cafe_rest": "카페/마무리",
        "secondary_spot": "보조 장소",
        "finish": "마무리",
        "night_walk": "야간 산책",
        "late_night_rest": "쉬어가기",
    }.get(role, "둘러보기")


def flow_pitch_reasons(
    duration: str,
    step_roles: list[str],
    shape_reason: str | None,
    *,
    trip_band_detail: str | None = None,
) -> tuple[str, list[str]]:
    """코스 흐름 중심 한 줄 요약 + 불릿 3개."""
    dko = {"2h": "짧은 외출", "half-day": "반나절", "full-day": "하루 일정"}.get(
        duration, "오늘 일정"
    )
    has_meal = "meal" in step_roles
    has_cafe = (
        "cafe_rest" in step_roles
        or "finish" in step_roles
        or "late_night_rest" in step_roles
    )
    n_spot = sum(1 for r in step_roles if r in ("main_spot", "secondary_spot"))
    tbd = str(trip_band_detail or "").strip()

    if shape_reason == "night_time_shape" or (
        tbd in ("night_late", "dawn")
        and any(r in ("night_walk", "late_night_rest") for r in step_roles)
    ):
        if tbd == "dawn":
            headline = "새벽 시간대라 짧게 다녀올 수 있는 야외·휴식 동선으로 맞췄어요."
            bullets = [
                "일반 관람·식사 장소는 영업 전일 수 있어 산책·전망 위주로 골랐어요.",
                "안전·조명·날씨를 고려해 가까운 구간 위주로 이어졌어요.",
                "휴식 장소는 실제 영업 여부를 방문 전에 꼭 확인해 주세요.",
            ]
        else:
            headline = "늦은 시간대라 야경·산책과 늦게까지 가능한 휴식 장소 위주로 묶었어요."
            bullets = [
                "실내 관람지·일반 식당은 운영이 끝났을 수 있어 보수적으로 뺐어요.",
                "야외는 안전·시야가 확보된 동선을 우선했어요.",
                "카페·휴식은 24시간 여부를 지도에서 한 번 더 확인해 주세요.",
            ]
    elif shape_reason == "culture-heavy":
        headline = "문화·역사 둘러보기에 맞춰 장소 위주 동선을 길게 잡았어요."
        bullets = [
            "전시·유적 등을 이어 보기 좋게 순서를 맞췄어요.",
            "이동 부담을 줄이려 가까운 후보를 우선했어요.",
            "마지막은 여유 있게 마무리할 수 있는 곳으로 배치했어요.",
        ]
    elif shape_reason == "spot_chain_exception":
        headline = "주변 식사·카페 후보가 제한적이라 둘러보기 위주로 이어졌어요."
        bullets = [
            "가능한 한 짧은 동선으로 묶었어요.",
            "중간에 식사나 카페를 직접 끼워 넣기 좋아요.",
            "지도 앱으로 근처 맛집을 찾아 보셔도 자연스러워요.",
        ]
    elif shape_reason == "no_nearby_meal_candidates" or shape_reason == "meal_substituted_by_cafe":
        headline = f"{dko}에 맞춰 둘러보기와 쉬어갈 곳을 한 흐름으로 묶었어요."
        bullets = [
            "근처 식당 후보가 드물어 카페·가벼운 휴식 위주로 이어졌어요.",
            "실제 영업·메뉴는 방문 전에 한 번 더 확인해 주세요.",
            "주변 다른 식당을 직접 골라 끼워 넣어도 자연스러워요.",
        ]
    elif shape_reason == "festival_focus":
        headline = "행사·축제 일정을 중심으로 동선을 짰어요."
        bullets = [
            "사람·교통이 몰릴 수 있어 여유 있게 보는 순서를 추천해요.",
            "식사·카페는 행사장 근처에서 짧게 이어가기 좋아요.",
            "날씨가 바뀌면 실내 대안을 함께 확인해 보세요.",
        ]
    elif duration == "2h":
        headline = "짧은 외출에 맞게 메인 장소와 쉬어갈 곳을 함께 묶었어요."
        bullets = [
            "이동 시간을 줄이려 가까운 순으로 이어졌어요.",
            "식사 대신 가벼운 카페·디저트로 마무리해도 좋아요." if not has_meal else "둘러본 뒤 바로 식사로 이어지기 좋은 흐름이에요.",
            "체력·동선에 맞게 한 곳만 줄여도 괜찮아요.",
        ]
    elif duration == "half-day":
        headline = "반나절 일정에 맞게 보고, 먹고, 쉬는 동선으로 구성했어요."
        bullets = [
            "대표 장소를 먼저 보고 식사·카페로 자연스럽게 이어지게 했어요.",
            "비슷한 성격의 장소만 연속되지 않게 섞었어요.",
            "현장 혼잡도에 따라 순서를 바꿔도 무방해요.",
        ]
    else:
        headline = "하루 일정에 맞게 핵심 장소와 식사, 여유 마무리까지 동선을 나눴어요."
        bullets = [
            "오전·오후를 나눠 체력이 떨어지기 전에 식사를 넣었어요.",
            "마지막은 짧게 쉬며 정리하기 좋은 곳으로 배치했어요.",
            "차량·대중교통에 맞춰 순서를 바꿔도 돼요.",
        ]

    if shape_reason == "rain_short_indoor_cafe":
        bullets[0] = "비 가능성이 있어 짧은 실내·카페 흐름을 우선했어요."

    _ = n_spot  # reserved for future tuning
    return headline, bullets[:3]
