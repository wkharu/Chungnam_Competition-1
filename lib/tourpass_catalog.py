# -*- coding: utf-8 -*-
"""투어패스 후보 메타(JSON + output/chungnam_tourpass_merchants.csv 병합)."""

from __future__ import annotations

import csv
import difflib
import json
import os
import re
from functools import lru_cache
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CANDIDATE_PATH = os.path.join(ROOT, "data", "tourpass_candidates.json")
_RULES_PATH = os.path.join(ROOT, "data", "pass_quest_rules.json")
_MERCHANT_CSV_PATH = os.path.join(ROOT, "output", "chungnam_tourpass_merchants.csv")

_DEFAULT_ROW: dict[str, Any] = {
    "tourpass_available": None,
    "tourpass_confidence": 0.35,
    "pass_benefit_type": "unknown",
    "pass_value_level": "unknown",
    "pass_category": "unknown",
    "time_ticket_fit": "normal",
    "pass_notice": "투어패스 활용 가능성이 있는 후보입니다. 혜택 여부는 방문 전 확인이 필요합니다.",
    "official_verified": False,
    "tourpass_source": None,
    "tourpass_city": None,
}


def _normalize_name_key(value: str | None) -> str:
    s = str(value or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", s)
    return "".join(ch for ch in s if ch.isalnum())


def _safe_mtime(path: str) -> float:
    try:
        return float(os.path.getmtime(path))
    except OSError:
        return -1.0


def _alias_name_keys(display_name: str) -> list[str]:
    n = str(display_name).strip()
    if not n or n.startswith("["):
        return []
    keys = {n}
    without_brackets = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", n).strip()
    if without_brackets:
        keys.add(without_brackets)
    collapsed = "".join(n.split())
    if collapsed and collapsed != n:
        keys.add(collapsed)
    normalized = _normalize_name_key(n)
    if normalized:
        keys.add(normalized)
    return list(keys)


def _with_match_meta(row: dict[str, Any], match_name: str | None, match_type: str) -> dict[str, Any]:
    out = dict(row)
    if match_name:
        out.setdefault("tourpass_catalog_name", match_name)
    out["tourpass_match_type"] = match_type
    return out


def _relaxed_catalog_lookup(name: str, catalog: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    target = _normalize_name_key(name)
    if len(target) < 5:
        return None

    best: tuple[float, dict[str, Any], str | None, str] | None = None
    seen: set[str] = set()
    for key, row in catalog.items():
        catalog_name = str(row.get("tourpass_catalog_name") or key or "").strip()
        seen_key = _normalize_name_key(catalog_name)
        if seen_key in seen:
            continue
        seen.add(seen_key)
        candidate = _normalize_name_key(catalog_name)
        if len(candidate) < 5:
            continue

        score = 0.0
        match_type = ""
        if target == candidate:
            score = 1.0
            match_type = "normalized"
        elif target in candidate or candidate in target:
            shorter = min(len(target), len(candidate))
            score = 0.86 + min(0.08, shorter / max(len(target), len(candidate)) * 0.08)
            match_type = "contains"
        else:
            ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
            if ratio >= 0.88:
                score = ratio
                match_type = "fuzzy"

        if score and (best is None or score > best[0]):
            best = (score, row, catalog_name, match_type)

    if not best:
        return None
    return _with_match_meta(best[1], best[2], best[3])


def _map_csv_category(raw: str | None) -> str:
    s = (raw or "").strip()
    m = {
        "관광지": "attraction",
        "체험": "experience",
        "카페": "cafe",
        "로컬시설": "local",
        "로컬": "local",
        "음식점": "restaurant",
        "숙박": "accommodation",
        "확인필요": "unknown",
    }
    return m.get(s, "unknown")


def _map_csv_benefit(raw: str | None) -> str:
    s = (raw or "").strip()
    if "무료" in s and "입장" in s:
        return "free"
    if "할인" in s:
        return "discount"
    if not s or s == "확인필요":
        return "unknown"
    return "unknown"


def _csv_rows_to_catalog_entries() -> dict[str, dict[str, Any]]:
    """CSV 한 행 → 카탈로그 row. 표기 별칭(공백 제거) 키도 동시 등록."""
    if not os.path.isfile(_MERCHANT_CSV_PATH):
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        with open(_MERCHANT_CSV_PATH, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row:
                    continue
                nm = str(row.get("merchant_name") or "").strip()
                if not nm or nm.startswith("["):
                    continue
                city = str(row.get("city") or "").strip() or None
                try:
                    conf = float(row.get("confidence") or 0.55)
                except (TypeError, ValueError):
                    conf = 0.55
                conf = max(0.38, min(0.88, conf))
                if str(row.get("needs_review", "")).lower() in ("true", "1", "yes"):
                    conf *= 0.9
                cat = _map_csv_category(row.get("category"))
                ben = _map_csv_benefit(row.get("benefit_type"))
                entry: dict[str, Any] = {
                    "tourpass_available": True,
                    "tourpass_confidence": round(conf, 4),
                    "pass_benefit_type": ben,
                    "pass_value_level": "medium",
                    "pass_category": cat,
                    "time_ticket_fit": "good",
                    "pass_notice": (
                        "충남 투어패스 지도 데이터셋 기준 후보입니다. "
                        "혜택·영업 여부는 방문 전 확인이 필요합니다."
                    ),
                    "official_verified": False,
                    "tourpass_source": "csv_map_dataset",
                    "tourpass_city": city,
                    "tourpass_catalog_name": nm,
                }
                for key in _alias_name_keys(nm):
                    out[key] = {**entry}
    except OSError:
        return {}
    return out


@lru_cache(maxsize=1)
def load_tourpass_rules() -> dict[str, Any]:
    try:
        with open(_RULES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {
            "quest_type_map": {},
            "pass_quest_disclaimer": _DEFAULT_ROW["pass_notice"],
            "pass_signal_default": "투어패스 활용 가능성을 참고해 동선을 구성했습니다",
            "risk_notice_default": "혜택 여부는 방문 전 확인이 필요합니다",
        }


@lru_cache(maxsize=32)
def _load_tourpass_by_name_cached(json_mtime: float, csv_mtime: float) -> dict[str, dict[str, Any]]:
    del json_mtime, csv_mtime  # 캐시 키로만 사용
    out: dict[str, dict[str, Any]] = {}
    try:
        with open(_CANDIDATE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        by_name = raw.get("by_name") or {}
        for k, v in by_name.items():
            if isinstance(v, dict):
                nm = str(k).strip()
                if nm:
                    entry = {**_DEFAULT_ROW, **v, "tourpass_catalog_name": nm}
                    for key in _alias_name_keys(nm):
                        out[key] = dict(entry)
    except OSError:
        pass

    csv_entries = _csv_rows_to_catalog_entries()
    for key, csv_row in csv_entries.items():
        base = out.get(key, dict(_DEFAULT_ROW))
        if not isinstance(base, dict):
            base = dict(_DEFAULT_ROW)
        merged = {**_DEFAULT_ROW, **base, **csv_row}
        out[key] = merged

    return out


def load_tourpass_by_name() -> dict[str, dict[str, Any]]:
    jm = _safe_mtime(_CANDIDATE_PATH)
    cm = _safe_mtime(_MERCHANT_CSV_PATH)
    return _load_tourpass_by_name_cached(jm, cm)


def catalog_row_for_place(name: str | None) -> dict[str, Any]:
    if not name:
        return dict(_DEFAULT_ROW)
    catalog = load_tourpass_by_name()
    nm = str(name).strip()
    row = catalog.get(nm)
    if row:
        return _with_match_meta(row, str(row.get("tourpass_catalog_name") or nm), "exact")
    collapsed = "".join(nm.split())
    row = catalog.get(collapsed)
    if row:
        return _with_match_meta(row, str(row.get("tourpass_catalog_name") or collapsed), "collapsed")
    normalized = _normalize_name_key(nm)
    row = catalog.get(normalized)
    if row:
        return _with_match_meta(row, str(row.get("tourpass_catalog_name") or normalized), "normalized")
    row = _relaxed_catalog_lookup(nm, catalog)
    if row:
        return row
    return dict(_DEFAULT_ROW)


def tourpass_dataset_stats() -> dict[str, Any]:
    """main 시작 로그·관리용."""
    jm = _safe_mtime(_CANDIDATE_PATH)
    cm = _safe_mtime(_MERCHANT_CSV_PATH)
    n_csv = len(_csv_rows_to_catalog_entries()) if cm >= 0 else 0
    n_json = 0
    try:
        with open(_CANDIDATE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        n_json = len(raw.get("by_name") or {})
    except OSError:
        pass
    merged = load_tourpass_by_name()
    return {
        "json_path": _CANDIDATE_PATH,
        "csv_path": _MERCHANT_CSV_PATH,
        "json_mtime": jm,
        "csv_mtime": cm,
        "json_name_count": n_json,
        "csv_alias_key_count": n_csv,
        "merged_key_count": len(merged),
    }


def merge_pass_fields_into_place(place: dict[str, Any]) -> dict[str, Any]:
    nm = str(place.get("name") or "").strip()
    row = catalog_row_for_place(nm)
    out = {**place}
    for k in (
        "tourpass_available",
        "tourpass_confidence",
        "pass_benefit_type",
        "pass_value_level",
        "pass_category",
        "time_ticket_fit",
        "pass_notice",
        "official_verified",
        "tourpass_source",
        "tourpass_city",
        "tourpass_catalog_name",
        "tourpass_match_type",
    ):
        out[k] = row.get(k, _DEFAULT_ROW.get(k))
    return out
