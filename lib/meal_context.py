# -*- coding: utf-8 -*-
"""
현재 시각(분 단위) 기반 식사·저녁·야간 코스 맥락.
추천 이유·동선 역할(itinerary) 생성에 사용한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MealPhase = Literal[
    "pre_lunch",
    "lunch",
    "afternoon_default",
    "dinner",
    "evening_night",
]


def _tod(h: int, m: int) -> int:
    return int(h) % 24 * 60 + int(m) % 60


@dataclass
class MealContext:
    phase: MealPhase
    clock_label: str
    """사용자에게 보여 줄 현재 시각 설명"""
    basis_line: str
    """추천 이유에 넣을 '현재 시간 반영' 한 줄"""
    lunch_window_start: tuple[int, int] | None
    """점심 슬롯 시작 (시, 분) — pre_lunch 시 이후 식사 정렬용"""
    lunch_window_end: tuple[int, int] | None
    requires_verified_meal_place: bool
    """True면 식사는 Places/공공 데이터로만 채우고 없으면 부족 안내"""


def parse_clock(hour: int, minute: int) -> tuple[int, int]:
    return int(hour) % 24, int(minute) % 60


def build_meal_context(hour: int, minute: int) -> MealContext:
    h, m = parse_clock(hour, minute)
    t = _tod(h, m)
    # 09:30~11:30 점심 전
    pre0 = _tod(9, 30)
    pre1 = _tod(11, 30)
    # 11:30~13:30 점심
    lu0 = _tod(11, 30)
    lu1 = _tod(13, 30)
    # 17:00~19:30 저녁
    di0 = _tod(16, 30)
    di1 = _tod(19, 30)
    # 20:00~ 야간
    ev0 = _tod(20, 0)

    clock_label = f"{h:02d}:{m:02d}"

    if pre0 <= t < pre1:
        return MealContext(
            phase="pre_lunch",
            clock_label=clock_label,
            basis_line=(
                f"현재 {clock_label}은 점심 전 시간대라, 먼저 관광·산책 후 "
                f"11:30~13:00 사이 점심 슬롯을 넣었어요."
            ),
            lunch_window_start=(11, 30),
            lunch_window_end=(13, 0),
            requires_verified_meal_place=True,
        )
    if lu0 <= t < lu1:
        return MealContext(
            phase="lunch",
            clock_label=clock_label,
            basis_line=(
                f"현재 {clock_label}은 점심 시간대라 첫 일정을 식사로 두었어요."
            ),
            lunch_window_start=None,
            lunch_window_end=None,
            requires_verified_meal_place=True,
        )
    if di0 <= t < di1:
        return MealContext(
            phase="dinner",
            clock_label=clock_label,
            basis_line=(
                f"현재 {clock_label}은 저녁 시간대라 저녁 식사 슬롯을 포함했어요."
            ),
            lunch_window_start=None,
            lunch_window_end=None,
            requires_verified_meal_place=True,
        )
    if t >= ev0 or t < _tod(6, 0):
        return MealContext(
            phase="evening_night",
            clock_label=clock_label,
            basis_line=(
                f"현재 {clock_label}은 늦은 시간대라 야간·휴식·산책 위주 동선을 우선했어요."
            ),
            lunch_window_start=None,
            lunch_window_end=None,
            requires_verified_meal_place=False,
        )
    return MealContext(
        phase="afternoon_default",
        clock_label=clock_label,
        basis_line=f"현재 시각 {clock_label}을 반영해 식사·휴식 순서를 맞췄어요.",
        lunch_window_start=None,
        lunch_window_end=None,
        requires_verified_meal_place=False,
    )


def step_roles_for_meal_context(
    mc: MealContext,
    duration: str,
) -> list[str] | None:
    """현재 시간대에 맞는 자연 동선. 투어패스 OFF 기본 코스가 이 순서를 따른다."""
    d = str(duration).strip().lower()
    if d not in ("2h", "half-day", "full-day"):
        d = "half-day"

    if mc.phase == "evening_night":
        if d == "2h":
            return ["night_walk", "late_night_rest"]
        if d == "half-day":
            return ["night_walk", "late_night_rest"]
        return ["night_walk", "late_night_rest", "night_walk"]

    if mc.phase == "pre_lunch":
        if d == "2h":
            return ["main_spot", "meal"]
        if d == "half-day":
            return ["main_spot", "meal", "cafe_rest"]
        return ["main_spot", "meal", "secondary_spot", "cafe_rest"]

    if mc.phase == "lunch":
        if d == "2h":
            return ["meal", "cafe_rest"]
        if d == "half-day":
            return ["meal", "main_spot", "cafe_rest"]
        return ["meal", "main_spot", "secondary_spot", "cafe_rest"]

    if mc.phase == "dinner":
        h, m = [int(x) for x in str(mc.clock_label).split(":", 1)]
        meal_first = h > 18 or (h == 18 and m >= 0)
        if meal_first:
            if d == "2h":
                return ["meal", "cafe_rest"]
            if d == "half-day":
                return ["meal", "main_spot", "cafe_rest"]
            return ["meal", "main_spot", "secondary_spot", "cafe_rest"]
        if d == "2h":
            return ["main_spot", "meal"]
        if d == "half-day":
            return ["main_spot", "meal", "cafe_rest"]
        return ["main_spot", "meal", "secondary_spot", "cafe_rest"]

    if d == "2h":
        return ["main_spot", "cafe_rest"]
    if d == "half-day":
        return ["main_spot", "meal", "cafe_rest"]
    return ["main_spot", "meal", "secondary_spot", "cafe_rest"]
