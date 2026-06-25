"""Flexible text-based worker attendance parsing and shift variance detection."""
from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any

from services.project_memory_service import (
    SUBSISTENCE_DEFAULT_NIS,
    SUBSISTENCE_MAX_NIS,
    SUBSISTENCE_MIN_NIS,
)

# Operational metadata flags for time-card entries.
FLAG_LATE_ARRIVAL = "LATE_ARRIVAL"
FLAG_EARLY_DEPARTURE = "EARLY_DEPARTURE"
FLAG_PARTIAL_SHIFT = "PARTIAL_SHIFT"

_TIME_COLON = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")
_TIME_AMPM = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_HOURS_DECIMAL = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:h(?:ours?)?|hrs?)\b", re.I)
_ARRIVAL_HINT = re.compile(
    r"(?:arriv(?:ed|al)|start(?:ed)?|clock(?:ed)?\s*in|in\s+at|from)\s*(?:at\s*)?(\d{1,2}(?::\.\d{2})?\s*(?:am|pm)?|\d{1,2}[:.]\d{2})",
    re.I,
)
_DEPARTURE_HINT = re.compile(
    r"(?:left|depart(?:ed|ure)?|clock(?:ed)?\s*out|out\s+at|until|to)\s*(?:at\s*)?(\d{1,2}(?::\.\d{2})?\s*(?:am|pm)?|\d{1,2}[:.]\d{2})",
    re.I,
)
_RANGE = re.compile(
    r"\b(\d{1,2}[:.]\d{2}|\d{1,2}\s*(?:am|pm))\s*[-–—to]+\s*(\d{1,2}[:.]\d{2}|\d{1,2}\s*(?:am|pm))\b",
    re.I,
)
_REASON = re.compile(
    r"(?:doctor|traffic|partial|appointment|family|emergency|stuck\s+in\s+traffic)",
    re.I,
)


def _time_to_minutes(raw: str) -> int | None:
    s = (raw or "").strip().lower().replace(".", ":")
    m = _TIME_COLON.search(s)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return h * 60 + mn
    m = _TIME_AMPM.search(s)
    if m:
        h = int(m.group(1))
        mn = int(m.group(2) or 0)
        mer = m.group(3).lower()
        if mer == "pm" and h < 12:
            h += 12
        if mer == "am" and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return h * 60 + mn
    return None


def _shift_bounds(shift_start: str, shift_end: str) -> tuple[int, int, float]:
    start_m = _time_to_minutes(shift_start)
    end_m = _time_to_minutes(shift_end)
    if start_m is None or end_m is None or end_m <= start_m:
        start_m = 7 * 60
        end_m = 17 * 60
    standard_hours = round((end_m - start_m) / 60, 2)
    return start_m, end_m, standard_hours


def _extract_times(text: str) -> tuple[int | None, int | None, float | None]:
    """Return (arrival_minutes, departure_minutes, explicit_decimal_hours)."""
    explicit_hours: float | None = None
    hm = _HOURS_DECIMAL.search(text)
    if hm:
        explicit_hours = float(hm.group(1))

    arrival: int | None = None
    departure: int | None = None

    rm = _RANGE.search(text)
    if rm:
        arrival = _time_to_minutes(rm.group(1))
        departure = _time_to_minutes(rm.group(2))

    if arrival is None:
        am = _ARRIVAL_HINT.search(text)
        if am:
            arrival = _time_to_minutes(am.group(1))

    if departure is None:
        dm = _DEPARTURE_HINT.search(text)
        if dm:
            departure = _time_to_minutes(dm.group(1))

    if arrival is None or departure is None:
        times = [_time_to_minutes(m.group(0)) for m in _TIME_COLON.finditer(text)]
        times += [_time_to_minutes(m.group(0)) for m in _TIME_AMPM.finditer(text)]
        times = [t for t in times if t is not None]
        if len(times) >= 2:
            times.sort()
            arrival = arrival or times[0]
            departure = departure or times[-1]
        elif len(times) == 1:
            if arrival is None and departure is None:
                arrival = times[0]

    return arrival, departure, explicit_hours


def parse_worker_hours_text(
    response_text: str,
    *,
    shift_start: str = "07:00",
    shift_end: str = "17:00",
    late_grace_minutes: int = 5,
) -> dict[str, Any]:
    """
    Parse flexible natural-language worker hour reports and detect shift variances.
    """
    text = (response_text or "").strip()
    if not text:
        raise ValueError("response_text is required")

    start_m, end_m, standard_hours = _shift_bounds(shift_start, shift_end)
    arrival, departure, explicit_hours = _extract_times(text)

    flags: list[str] = []
    notes: list[str] = []

    notes = [m.group(0).lower() for m in _REASON.finditer(text)]

    if arrival is not None and arrival > start_m + late_grace_minutes:
        flags.append(FLAG_LATE_ARRIVAL)
    if departure is not None and departure < end_m:
        flags.append(FLAG_EARLY_DEPARTURE)

    if explicit_hours is not None and explicit_hours > 0:
        hours_worked = round(explicit_hours, 2)
        arrival_str = None
        departure_str = None
    elif arrival is not None and departure is not None and departure > arrival:
        hours_worked = round((departure - arrival) / 60, 2)
        arrival_str = f"{arrival // 60:02d}:{arrival % 60:02d}"
        departure_str = f"{departure // 60:02d}:{departure % 60:02d}"
    elif arrival is not None:
        hours_worked = round(max(0, (end_m - arrival) / 60), 2)
        arrival_str = f"{arrival // 60:02d}:{arrival % 60:02d}"
        departure_str = None
        flags.append(FLAG_PARTIAL_SHIFT)
    else:
        raise ValueError("Could not parse work hours from response text")

    if hours_worked < standard_hours - 0.05:
        if FLAG_PARTIAL_SHIFT not in flags:
            flags.append(FLAG_PARTIAL_SHIFT)

    variance_minutes = round((hours_worked - standard_hours) * 60)
    partial_ratio = round(min(1.0, hours_worked / standard_hours), 4) if standard_hours else 1.0

    return {
        "parsed_from": text,
        "shift_start": shift_start,
        "shift_end": shift_end,
        "standard_hours": standard_hours,
        "arrival_time": arrival_str,
        "departure_time": departure_str,
        "hours_worked": hours_worked,
        "variance_minutes": variance_minutes,
        "operational_flags": flags,
        "partial_shift_ratio": partial_ratio,
        "reason_hints": notes,
    }


def compute_daily_food_allowance_nis(
    *,
    operational_flags: list[str] | None = None,
    partial_shift_ratio: float = 1.0,
    base_allowance_nis: float = SUBSISTENCE_DEFAULT_NIS,
) -> float:
    """Dynamic worker food allowance within 65–100 NIS based on delay/variance flags."""
    allowance = float(base_allowance_nis or SUBSISTENCE_DEFAULT_NIS)
    flags = operational_flags or []
    if FLAG_LATE_ARRIVAL in flags:
        allowance -= 10
    if FLAG_EARLY_DEPARTURE in flags:
        allowance -= 15
    if FLAG_PARTIAL_SHIFT in flags:
        span = SUBSISTENCE_MAX_NIS - SUBSISTENCE_MIN_NIS
        allowance = SUBSISTENCE_MIN_NIS + span * max(0.0, min(1.0, partial_shift_ratio))
    return round(max(SUBSISTENCE_MIN_NIS, min(SUBSISTENCE_MAX_NIS, allowance)), 2)


def calculate_attendance_pay(
    *,
    hours_worked: float,
    standard_hours: float,
    hourly_rate_nis: float,
    daily_subsistence_nis: float = SUBSISTENCE_DEFAULT_NIS,
    operational_flags: list[str] | None = None,
    partial_shift_ratio: float = 1.0,
) -> dict[str, float]:
    adjusted_allowance = compute_daily_food_allowance_nis(
        operational_flags=operational_flags,
        partial_shift_ratio=partial_shift_ratio,
        base_allowance_nis=daily_subsistence_nis,
    )
    ratio = min(1.0, hours_worked / standard_hours) if standard_hours > 0 else 1.0
    wage_nis = round(hourly_rate_nis * hours_worked, 2)
    subsistence_nis = round(adjusted_allowance * ratio, 2)
    return {
        "hourly_rate_nis": hourly_rate_nis,
        "wage_nis": wage_nis,
        "subsistence_nis": subsistence_nis,
        "subsistence_full_day_nis": adjusted_allowance,
        "subsistence_range_nis": {"min": SUBSISTENCE_MIN_NIS, "max": SUBSISTENCE_MAX_NIS},
        "proration_ratio": ratio,
    }
