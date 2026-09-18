"""기간 처리. 모든 비교는 KST 기준 시각을 UTC로 바꿔서 한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


class DateRangeError(ValueError):
    """사용자 입력이 기간으로 성립하지 않을 때."""


@dataclass(frozen=True)
class DateRange:
    """[start, end] 폐구간. 둘 다 UTC aware datetime."""

    start: datetime
    end: datetime

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, moment: datetime | None) -> bool:
        if moment is None:
            return False
        return self.start <= to_utc(moment) <= self.end

    def label(self) -> str:
        s = self.start.astimezone(KST).date().isoformat()
        e = self.end.astimezone(KST).date().isoformat()
        return f"{s} ~ {e}"


def to_utc(moment: datetime) -> datetime:
    """naive datetime은 KST로 간주한다(국내 매체가 대부분 무표기 KST)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=KST).astimezone(timezone.utc)
    return moment.astimezone(timezone.utc)


def parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        raise DateRangeError("날짜가 비어 있습니다. YYYY-MM-DD 형식으로 넣으세요.")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise DateRangeError(
            f"날짜 형식이 잘못되었습니다: {value!r} (YYYY-MM-DD)"
        ) from exc


def build_range(
    date_from: str | date | datetime,
    date_to: str | date | datetime,
    *,
    today: date | None = None,
) -> DateRange:
    """시작일 00:00:00 KST부터 종료일 23:59:59.999999 KST까지."""
    start_d = parse_date(date_from)
    end_d = parse_date(date_to)
    if start_d > end_d:
        raise DateRangeError(
            f"시작일({start_d})이 종료일({end_d})보다 뒤입니다."
        )
    reference = today or datetime.now(KST).date()
    if start_d > reference:
        raise DateRangeError(f"시작일({start_d})이 미래입니다.")
    if end_d > reference:
        end_d = reference
    start = datetime.combine(start_d, time.min, tzinfo=KST).astimezone(timezone.utc)
    end = datetime.combine(end_d, time.max, tzinfo=KST).astimezone(timezone.utc)
    return DateRange(start=start, end=end)


PRESETS = {
    "7d": "지난 7일",
    "30d": "지난 30일",
    "90d": "지난 90일",
    "last-month": "지난달",
    "this-quarter": "이번 분기",
}


def preset_range(name: str, *, today: date | None = None) -> DateRange:
    """CLI·UI가 공유하는 프리셋."""
    ref = today or datetime.now(KST).date()
    key = name.strip().lower()
    if key in ("7d", "30d", "90d"):
        span = int(key.rstrip("d"))
        return build_range(ref - timedelta(days=span - 1), ref, today=ref)
    if key == "last-month":
        first_this = ref.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return build_range(last_prev.replace(day=1), last_prev, today=ref)
    if key == "this-quarter":
        q_start_month = 3 * ((ref.month - 1) // 3) + 1
        return build_range(ref.replace(month=q_start_month, day=1), ref, today=ref)
    raise DateRangeError(
        f"알 수 없는 프리셋: {name} (가능한 값: {', '.join(PRESETS)})"
    )
