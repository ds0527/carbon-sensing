"""기간 처리: 시간대 경계, 역순 입력, 미래 날짜, 일자 미확인."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from carbon_sensing.daterange import (
    KST,
    DateRangeError,
    build_range,
    parse_date,
    preset_range,
)

TODAY = date(2026, 9, 18)


def test_range_covers_full_kst_days():
    period = build_range("2026-09-01", "2026-09-15", today=TODAY)
    assert period.start == datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)
    assert period.end.astimezone(KST).hour == 23
    assert period.days == 15


def test_boundary_document_in_kst_is_included():
    period = build_range("2026-09-01", "2026-09-01", today=TODAY)
    # KST 0시 5분 기사 = UTC 전날 15시 5분. 기간에 들어와야 한다.
    early = datetime(2026, 9, 1, 0, 5, tzinfo=KST)
    late = datetime(2026, 9, 1, 23, 55, tzinfo=KST)
    assert period.contains(early)
    assert period.contains(late)


def test_utc_document_outside_kst_day_is_excluded():
    period = build_range("2026-09-01", "2026-09-01", today=TODAY)
    # UTC 2026-09-01 16:00 = KST 09-02 01:00 → 기간 밖
    assert not period.contains(datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc))


def test_naive_datetime_is_treated_as_kst():
    period = build_range("2026-09-01", "2026-09-01", today=TODAY)
    assert period.contains(datetime(2026, 9, 1, 8, 0))


def test_other_timezone_is_converted():
    period = build_range("2026-09-10", "2026-09-10", today=TODAY)
    # 뉴욕 2026-09-09 12:00 = KST 09-10 01:00 → 기간 안
    ny = datetime(2026, 9, 9, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    assert period.contains(ny)


def test_none_is_never_contained():
    period = build_range("2026-09-01", "2026-09-15", today=TODAY)
    assert period.contains(None) is False


def test_reversed_range_raises():
    with pytest.raises(DateRangeError):
        build_range("2026-09-15", "2026-09-01", today=TODAY)


def test_future_start_raises():
    with pytest.raises(DateRangeError):
        build_range("2026-10-01", "2026-10-05", today=TODAY)


def test_future_end_is_clamped_to_today():
    period = build_range("2026-09-15", "2026-12-31", today=TODAY)
    assert period.end.astimezone(KST).date() == TODAY


def test_bad_format_raises():
    with pytest.raises(DateRangeError):
        parse_date("2026/09/01")
    with pytest.raises(DateRangeError):
        parse_date("")


@pytest.mark.parametrize("name,expected_days", [("7d", 7), ("30d", 30), ("90d", 90)])
def test_relative_presets(name, expected_days):
    period = preset_range(name, today=TODAY)
    assert period.days == expected_days
    assert period.end.astimezone(KST).date() == TODAY


def test_last_month_preset():
    period = preset_range("last-month", today=TODAY)
    assert period.start.astimezone(KST).date() == date(2026, 8, 1)
    assert period.end.astimezone(KST).date() == date(2026, 8, 31)


def test_this_quarter_preset():
    period = preset_range("this-quarter", today=TODAY)
    assert period.start.astimezone(KST).date() == date(2026, 7, 1)


def test_unknown_preset_raises():
    with pytest.raises(DateRangeError):
        preset_range("어제", today=TODAY)
