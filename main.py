# -*- coding: utf-8 -*-
"""
충남 패스퀘스트 AI — 날씨·시간 기반 동선 추천(투어패스 활용 모드 선택 시 미션형 뷰).
실행: python main.py  또는  uvicorn main:app --reload --host 127.0.0.1 --port 8000
"""
import sys
import os
import asyncio
import traceback
from datetime import datetime, timezone
from urllib.parse import quote
sys.path.insert(0, ".")

from lib.config import request_get, settings

settings.log_config_summary()
if settings.frontend_ui == "static":
    print(
        "[ui] no frontend/dist — root (/) is static/index.html (build how-to). "
        "cd frontend && npm run build, restart. Old one-page UI: /legacy. "
        "If .env has FRONTEND_UI=static, remove it (or set dist) after building.",
        file=sys.stderr,
    )

from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from lib.weather import fetch_weather
from lib.recommend import match_from_api
from lib.places import fetch_continuation_candidates, fetch_place_reviews
from lib.course_continuation import build_course_payload
from lib.daytrip_planner import build_daytrip_payload
from lib.intent_normalize import normalize_intent
from lib.scoring import calc_weather_score

app = FastAPI(title="떠나GO")

_PASS_QUEST_LOG: list[dict] = []


@app.on_event("startup")
def _log_tourpass_merged_catalog() -> None:
    try:
        from lib.tourpass_catalog import tourpass_dataset_stats

        s = tourpass_dataset_stats()
        if s.get("csv_mtime", -1) < 0 and not os.path.isfile(s["csv_path"]):
            print(
                "[tourpass] CSV 데이터셋 없음 — JSON 카탈로그만 사용합니다.",
                file=sys.stderr,
            )
            return
        print(
            "[tourpass] 가맹 메타 병합: 키 "
            f"{s['merged_key_count']}개 "
            f"(JSON {s['json_name_count']} · "
            f"CSV 별칭 {s['csv_alias_key_count']} · "
            f"{os.path.basename(s['csv_path'])})",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"[tourpass] 카탈로그 요약 실패: {e}", file=sys.stderr)

FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "frontend", "dist")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
LEGACY_APP = os.path.join(STATIC_DIR, "legacy_app.html")

# UI: lib.config.settings.frontend_ui — frontend/dist/index.html 이 있으면 기본 dist(Vite), 없으면 static.
# 강제: .env 에 FRONTEND_UI=static | dist
def _serve_vite_dist() -> bool:
    return settings.frontend_ui == "dist"


if os.path.isdir(FRONTEND_DIST) and _serve_vite_dist():
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")


# ── API 라우트 (캐치올보다 반드시 먼저) ──────────────────────────────
# NOTE: /api/place-photo, /api/weather-snapshot, /api/place-reviews are handled
# by the Node gateway (server/index.mjs) and never reach this process when
# running behind the gateway at :3080. They are intentionally removed here to
# avoid OpenAPI schema confusion.


@app.get("/hero-course-placeholder.svg")
async def hero_course_placeholder():
    for p in (
        os.path.join(FRONTEND_DIST, "hero-course-placeholder.svg"),
        os.path.join(os.path.dirname(__file__), "frontend", "public", "hero-course-placeholder.svg"),
    ):
        if os.path.isfile(p):
            return FileResponse(p, media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="placeholder not found")

@app.get("/api/recommend")
async def recommend(
    city: str = Query(default="아산"),
    top_n: int = Query(default=40),
    user_lat: float = Query(default=None),
    user_lng: float = Query(default=None),
    current_time: str | None = Query(
        default=None,
        description="현재 시각 HH:MM (코스·랭킹 시간축)",
    ),
    current_date: str | None = Query(
        default=None,
        description="방문 기준일 YYYY-MM-DD (시간표 시작일)",
    ),
    meal_preference: str | None = Query(
        default=None,
        description="식사 선호(자유 텍스트·짧게). 없으면 none",
    ),
    companion: str | None = Query(default=None),
    trip_goal: str | None = Query(default=None),
    duration: str | None = Query(default=None),
    transport: str | None = Query(default=None),
    adult_count: int | None = Query(default=None, ge=1, le=10),
    child_count: int | None = Query(default=None, ge=0, le=8),
    tourpass_mode: bool = Query(default=False),
    tourpass_ticket_type: str = Query(default="none"),
    benefit_priority: str = Query(default="none"),
    pass_goal: str | None = Query(default=None),
    purchased_status: str = Query(default="not_planned"),
):
    try:
        loop = asyncio.get_event_loop()

        city_weather_arg = city if city != "전체" else "아산"
        weather = await loop.run_in_executor(
            None,
            lambda: fetch_weather(city_weather_arg, user_lat=user_lat, user_lng=user_lng),
        )
        weather.setdefault("minute", 0)
        if current_time:
            parts = str(current_time).strip().split(":")
            try:
                weather["hour"] = int(parts[0]) % 24
            except (TypeError, ValueError):
                pass
            if len(parts) >= 2:
                try:
                    weather["minute"] = int(parts[1]) % 60
                except (TypeError, ValueError):
                    weather["minute"] = 0
        if current_date:
            ds = str(current_date).strip()
            if ds:
                weather["current_date_iso"] = ds
        intent = normalize_intent(
            companion,
            trip_goal,
            duration,
            transport,
            adult_count=adult_count,
            child_count=child_count,
            meal_preference=meal_preference,
        )
        tt = (tourpass_ticket_type or "none").lower().strip()
        if tt not in ("none", "24h", "36h", "48h", "single", "theme", "undecided"):
            tt = "none"
        bp = (benefit_priority or "none").lower().strip()
        if bp not in ("none", "balanced", "high"):
            bp = "none"
        pg_raw = (pass_goal or "").strip()
        ps = (purchased_status or "not_planned").lower().strip()
        if ps not in ("already_have", "considering", "not_planned"):
            ps = "not_planned"
        pass_context = {
            "tourpass_mode": tourpass_mode,
            "tourpass_ticket_type": tt,
            "benefit_priority": bp,
            "pass_goal": pg_raw or None,
            "purchased_status": ps,
        }
        pp0 = float(weather.get("precip_prob", 0) or 0)
        # 강수 48% 이상: 상위 N에 실내가 안 들어오는 경우가 있어 후보 깊이 확장
        match_top_n = max(int(top_n), 72) if pp0 >= 48 else int(top_n)
        match_top_n = min(match_top_n, 120)
        result = await loop.run_in_executor(
            None,
            lambda: match_from_api(
                weather,
                city,
                top_n=match_top_n,
                user_lat=user_lat,
                user_lng=user_lng,
                intent=intent,
                tourpass_mode=tourpass_mode,
            ),
        )
        trip_context = {
            "clock_hour": weather.get("hour"),
            "clock_minute": int(weather.get("minute", 0) or 0),
            "current_date_iso": weather.get("current_date_iso"),
            "meal_preference": intent.get("meal_preference"),
            "user_lat": user_lat,
            "user_lng": user_lng,
        }
        payload = await loop.run_in_executor(
            None,
            lambda: build_daytrip_payload(
                weather=weather,
                match_result=result,
                intent=intent,
                trip_context=trip_context,
                pass_context=pass_context,
            ),
        )
        # 단기예보·에어코리아 확장 필드 (Vite 앱·static 공통)
        w = payload["weather"]
        for k in (
            "pm25", "pm10", "air_source",
            "weather_fallback", "weather_fallback_note",
            "weather_source", "fcst_time_slot",
            "forecast_anchor_city", "forecast_anchor_reason",
        ):
            if k in weather:
                w[k] = weather[k]

        return payload

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# /api/weather-snapshot — handled by Node gateway (server/index.mjs)
# Removed from FastAPI to avoid route-ownership confusion.


@app.post("/api/pass-quest/sync")
async def pass_quest_sync(body: dict = Body(default_factory=dict)):
    """단계 교체 등으로 top_course만 바뀐 뒤 패스퀘스트 메타를 재계산."""
    try:
        from lib.pass_quest import RERANK_FEATURES, pass_context_active, sync_top_pass_quest_only

        course = body.get("course") or {}
        weather = body.get("weather") or {}
        intent = body.get("intent") or {}
        pass_ctx = body.get("pass_context") or {}
        recs = body.get("recommendations") or []
        city = str(body.get("city") or "전체")
        if not pass_context_active(pass_ctx):
            return {"top_pass_quest": None, "pass_quest_rerank": None}
        tq = sync_top_pass_quest_only(
            course,
            weather=weather,
            intent=intent,
            pass_context=pass_ctx,
            recommendations=recs,
            city=city,
        )
        return {
            "top_pass_quest": tq,
            "pass_quest_rerank": {
                "model_used": False,
                "mode": "rule-based-fallback",
                "confidence": None,
                "features_used": list(RERANK_FEATURES),
                "explanation": "단계 교체 뒤 규칙으로 패스퀘스트를 다시 맞췄습니다.",
            },
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/log/pass-quest-event")
async def pass_quest_log_event(body: dict = Body(default_factory=dict)):
    try:
        _PASS_QUEST_LOG.append({"received": body, "ts": datetime.now(timezone.utc).isoformat()})
        if len(_PASS_QUEST_LOG) > 400:
            del _PASS_QUEST_LOG[:-300]
        return {"ok": True}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/pass-quest-mock-stats")
async def pass_quest_mock_stats():
    events = list(_PASS_QUEST_LOG)
    by_type: dict[str, int] = {}
    for row in events:
        ev = (row.get("received") or {}).get("event")
        if ev:
            by_type[str(ev)] = by_type.get(str(ev), 0) + 1
    return {
        "total_events": len(events),
        "by_event_type": by_type,
        "sample_tail": events[-12:],
        "note": "메모리 보관(재시작 시 초기화). 향후 운영 대시보드·저장소로 확장 가능합니다.",
    }


@app.get("/api/place-reviews")
async def place_reviews(
    name: str = Query(...),
    lat: float = Query(...),
    lng: float = Query(...),
    address: str = Query(default=""),
    top_reviews: int = Query(
        default=5,
        ge=1,
        le=5,
        description="Google 최신 리뷰 노출 개수(기본 3, 최대 5)",
    ),
):
    """메인 추천 장소의 Google 리뷰 조회(searchText → Place Details 보강, 최신 N개)."""
    try:
        loop = asyncio.get_event_loop()
        tr = int(top_reviews)
        result = await loop.run_in_executor(
            None,
            lambda: fetch_place_reviews(
                name, lat, lng, address, top_review_count=tr
            ),
        )
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/course")
async def course(
    lat: float = Query(...),
    lng: float = Query(...),
    category: str = Query(default="outdoor"),
    hour: int = Query(default=12),
    companion: str | None = Query(default=None),
    trip_goal: str | None = Query(default=None),
    duration: str | None = Query(default=None),
    transport: str | None = Query(default=None),
    adult_count: int | None = Query(default=None, ge=1, le=10),
    child_count: int | None = Query(default=None, ge=0, le=8),
    precip_prob: float | None = Query(default=None),
    dust: int | None = Query(default=None),
    temp: float | None = Query(default=None),
    sky: int | None = Query(default=None),
    spot_id: str | None = Query(default=None),
    spot_name: str | None = Query(default=None),
    course_path: str | None = Query(default=None, description="ai | guided"),
    user_next_hint: str | None = Query(default=None, description="meal|cafe|quiet|photo|indoor|kids|custom"),
    user_custom_note: str | None = Query(default=None),
    ml_next_scene_assist: bool = Query(
        default=False,
        description="True일 때만 시나리오 next_scene 모델 보조(합성 학습). 홈페이지 기본값은 False.",
    ),
    desired_next_scene: str | None = Query(
        default=None,
        description="코스 재구성: meal|cafe_rest|indoor_backup|sunset_finish 등(시간대보다 우선).",
    ),
    desired_course_style: str | None = Query(default=None, description="향후 스타일 가중용(선택)"),
    family_bias: float = Query(default=0.0, ge=0.0, le=1.0),
    scenic_bias: float = Query(default=0.0, ge=0.0, le=1.0),
    indoor_bias: float = Query(default=0.0, ge=0.0, le=1.0),
    meal_bias: float = Query(default=0.0, ge=0.0, le=1.0),
    cafe_bias: float = Query(default=0.0, ge=0.0, le=1.0),
    reconfigure_target: str | None = Query(
        default=None,
        description="재구성 UX: 수정 대상 식별(top|active:main|active:alt:…|alt:…). 향후 분석·분기용.",
    ),
    selected_course_type: str | None = Query(
        default=None,
        description="재구성 UX: active|top|alternative",
    ),
    course_id: str | None = Query(
        default=None,
        description="재구성 UX: 편집 기준 코스 id(있을 때)",
    ),
    replace_step: bool = Query(default=False, description="True면 단계 교체 후보만(리뷰 메타 재랭킹 포함)"),
    step_index: int | None = Query(default=None, ge=0, le=20, description="교체 대상 단계 인덱스(0부터)"),
    step_role: str | None = Query(
        default=None,
        description="교체 대상 단계 역할(main_spot|meal|cafe_rest|secondary_spot|finish)",
    ),
    time_band: str | None = Query(
        default=None,
        description="morning|lunch|afternoon|evening|night|early — 비우면 서버가 hour로 산출",
    ),
):
    """코스 이어가기 — 다음 장면(단계) 결정 후 Places 후보를 재랭킹."""
    try:
        loop = asyncio.get_event_loop()
        intent = normalize_intent(
            companion,
            trip_goal,
            duration,
            transport,
            adult_count=adult_count,
            child_count=child_count,
        )
        wmin = {
            "temp": float(temp) if temp is not None else 20.0,
            "precip_prob": float(precip_prob) if precip_prob is not None else 0.0,
            "sky": int(sky) if sky is not None else 1,
            "dust": int(dust) if dust is not None else 1,
            "hour": hour,
        }
        scores_ctx = calc_weather_score(wmin)

        def _run():
            return build_course_payload(
                lat=lat,
                lng=lng,
                category=category,
                hour=hour,
                intent=intent,
                scores=scores_ctx,
                precip_prob=wmin["precip_prob"],
                dust=wmin["dust"],
                temp=wmin["temp"],
                spot_id=spot_id,
                spot_name=spot_name,
                fetch_places_fn=fetch_continuation_candidates,
                course_path=course_path,
                user_next_hint=user_next_hint,
                user_custom_note=user_custom_note,
                use_ml_next_scene_assist=ml_next_scene_assist,
                desired_next_scene=desired_next_scene,
                desired_course_style=desired_course_style,
                family_bias=family_bias,
                scenic_bias=scenic_bias,
                indoor_bias=indoor_bias,
                meal_bias=meal_bias,
                cafe_bias=cafe_bias,
                replace_step=replace_step,
                replace_step_index=step_index,
                replace_step_role=step_role,
                time_band=time_band,
            )

        payload = await loop.run_in_executor(None, _run)
        return payload
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ── 프론트엔드 서빙 (항상 마지막에) ─────────────────────────────────

def _index():
    if os.path.isdir(FRONTEND_DIST) and _serve_vite_dist():
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/")
async def root():
    return _index()


@app.get("/legacy")
async def legacy_spa():
    """옛 단일 HTML 프로토(날씨·필터가 한 화면에 있는 UI). / 는 dist 없을 때 빌드 안내."""
    if not os.path.isfile(LEGACY_APP):
        raise HTTPException(status_code=404, detail="legacy_app.html not found")
    return FileResponse(LEGACY_APP)


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    return _index()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
