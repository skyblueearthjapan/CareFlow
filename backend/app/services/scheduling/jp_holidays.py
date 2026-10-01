"""日本の祝日 (国民の祝日に関する法律) — 週のコピーで「祝日のある週」を警告するための判定.

アプリには祝日を扱う仕組みが無かった (copy-week-design-2026-09-30.md §6-4)。
警告に使うだけなので、2020 年以降の現行ルールだけを実装する
(東京五輪の特例年 2020/2021 の移動は扱わない)。

- 固定日 / ハッピーマンデー / 春分・秋分 (近似式・2099 年まで有効)
- 振替休日 (祝日が日曜 → 次の平日)
- 国民の休日 (祝日に挟まれた平日)
"""

from __future__ import annotations

from datetime import date, timedelta

_FIXED: dict[tuple[int, int], str] = {
    (1, 1): "元日",
    (2, 11): "建国記念の日",
    (2, 23): "天皇誕生日",
    (4, 29): "昭和の日",
    (5, 3): "憲法記念日",
    (5, 4): "みどりの日",
    (5, 5): "こどもの日",
    (8, 11): "山の日",
    (11, 3): "文化の日",
    (11, 23): "勤労感謝の日",
}

# (月, 第 n 月曜) → 名前
_HAPPY_MONDAY: dict[tuple[int, int], str] = {
    (1, 2): "成人の日",
    (7, 3): "海の日",
    (9, 3): "敬老の日",
    (10, 2): "スポーツの日",
}


def _nth_monday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (7 - first.weekday()) % 7  # 最初の月曜まで
    return first + timedelta(days=offset + 7 * (n - 1))


def _equinox_days(year: int) -> tuple[int, int]:
    """(春分の日の 3 月の日, 秋分の日の 9 月の日)。1980〜2099 年の近似式."""
    y = year - 1980
    spring = int(20.8431 + 0.242194 * y - y // 4)
    autumn = int(23.2488 + 0.242194 * y - y // 4)
    return spring, autumn


def _base_holidays(year: int) -> dict[date, str]:
    days: dict[date, str] = {date(year, m, d): name for (m, d), name in _FIXED.items()}
    for (m, n), name in _HAPPY_MONDAY.items():
        days[_nth_monday(year, m, n)] = name
    spring, autumn = _equinox_days(year)
    days[date(year, 3, spring)] = "春分の日"
    days[date(year, 9, autumn)] = "秋分の日"
    return days


def holidays_of_year(year: int) -> dict[date, str]:
    """その年の祝日 (振替休日・国民の休日を含む) を {日付: 名前} で返す."""
    days = _base_holidays(year)
    # 国民の休日: 前後が祝日の平日 (日曜は除く)。
    for d in sorted(days):
        mid = d + timedelta(days=1)
        if mid not in days and (d + timedelta(days=2)) in days and mid.weekday() != 6:
            days[mid] = "国民の休日"
    # 振替休日: 祝日が日曜なら、その後の最初の祝日でない日。
    for d in sorted(days):
        if d.weekday() == 6:
            sub = d + timedelta(days=1)
            while sub in days:
                sub += timedelta(days=1)
            days[sub] = "振替休日"
    return days


def holidays_between(start: date, end: date) -> list[tuple[date, str]]:
    """``start``〜``end`` (両端含む) の祝日を日付順に返す."""
    out: dict[date, str] = {}
    for year in range(start.year, end.year + 1):
        for d, name in holidays_of_year(year).items():
            if start <= d <= end:
                out[d] = name
    return sorted(out.items())
