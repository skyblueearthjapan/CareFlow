"""W6-MIG1: convert v1 patient JSONB into v2 shape (dry-run capable).

設計仕様書 v0.9 §3.3 / §3.4 / §4.1 / §8 / 実装手順書 v0.2 §7 W6-MIG1。

## 背景

Wave 1 で `patients` のスキーマは v2 (§4.1) に整理済み (列の add/drop は
0009 で完了)。しかし JSONB カラム (``weekly_pattern`` / ``special_week``)
の **中身** は v1 形式のまま残っているレコードがある。本スクリプトは
これらを v2 形式へ in-place で書き換える。

### 変換 1: weekly_pattern (v1 dict → v2 entries[] + staff_count)

v1 形式 (例)::

    {
      "frequency_per_week": 2,
      "preferred_weekdays": ["Mon", "Thu"],
      "weekday_priority": "低",
      "service_minutes": 60,
      "time_type": "午前",
      "preferred_start": null,
      "preferred_end": null,
      "ng_weekdays": ["Sat", "Sun"]
    }

v2 形式 (§3.3 / §4.1)::

    {
      "frequency_per_week": 2,
      "preferred_weekdays": ["Mon", "Thu"],
      "service_minutes": 60,
      "time_type": "午前",
      "preferred_start": null,
      "preferred_end": null,
      "entries": [
        {
          "weekday": "Mon",
          "time_type": "午前",
          "service_minutes": 60,
          "staff_count": 1
        },
        {
          "weekday": "Thu",
          "time_type": "午前",
          "service_minutes": 60,
          "staff_count": 1
        }
      ]
    }

ポイント:

- ``preferred_weekdays`` の各曜日に対して 1 entry を生成。
- ``staff_count`` は v1 には存在しないため **既定 1** を付与。旧
  ``required_staff_count`` (= 患者列、0009 で drop) は使わない。
  すでに ``required_staff_count`` がバックアップ表に残っている場合の
  別軸の手当ては Wave 6 運用判断に委ねる (§3.3 では "曜日ごとの 2 名体制"
  が新方針のため、患者単位の固定値はそのまま v2 に持ち込まない)。
- ``ng_weekdays`` / ``weekday_priority`` は v2 schema (§4.1) では削除済み。
  破壊的に削除すると検証が困難になるため **トップレベルから drop** する
  (entries[] には引き継がない)。
- ``preferred_weekdays`` が空 / 不在の場合は entries=[] とし、それ以外の
  キーはそのまま温存する (フロント / Layer1Expander が想定通り扱う)。
- 既に ``entries`` キーが入っている (= 既に v2 形式に変換済み) 場合は
  no-op とする (再実行で entries が二重生成されないように)。

### 変換 2: special_week (v1 JSONB) → special_weekly_pattern + special_week_active

旧 v1 では `patients.special_week` という単一 JSONB に "特別週用パターン
(`pattern`) と適用週リスト (`weeks`)" が混在していた。v2 (§3.4 Option A)
では別カラムへ分離する::

    旧:
      special_week = {
        "pattern": { ... weekly_pattern と同じ shape ... },
        "weeks":   [{"iso_year": 2026, "iso_week": 18}, ...]
      }

    新:
      special_weekly_pattern = { ... v2 形式の weekly_pattern ... }
      special_week_active   = [{"iso_year": 2026, "iso_week": 18}, ...]

ポイント:

- 旧 ``special_week.pattern`` は **weekly_pattern と同じ変換ロジック** を
  通して v2 形式 (entries[]) に変換した上で
  ``special_weekly_pattern`` へ書き出す。
- 旧 ``special_week.weeks`` は ``[{iso_year, iso_week}]`` という同形式の
  ため、そのまま ``special_week_active`` へコピー。
- 旧 ``special_week`` は **本スクリプトでは削除しない** (列ごと drop する
  のは Wave 6 W6-MIG2 / 0016 の責務。本スクリプトはデータ転写のみ)。
- 既に ``patients.special_weekly_pattern`` が non-null の場合は、本スクリプト
  は **上書きしない** (= 後勝ち防止)。これにより複数回実行しても安全。

## 使い方

::

    # dry-run: 件数のみ報告 (DB 書き込みなし)
    docker exec carelink-backend python scripts/migrate_v1_to_v2.py
    docker exec carelink-backend python scripts/migrate_v1_to_v2.py --dry-run

    # 実適用
    docker exec carelink-backend python scripts/migrate_v1_to_v2.py --apply

## ログ出力

stderr に変換件数と内訳サマリ、エラーがあればその一覧を出力する。
冪等性を担保するため、サマリには「skipped(=already_v2)」「changed」を
それぞれ表示する。

## なぜ Alembic ではなくスクリプトか

Alembic は **DDL の世代管理**には強いが、**JSONB の中身を 1 行ずつ確認しな
がら変換**するような長時間ジョブには向かない (途中エラー時の冪等性
管理、本番 DB を巻き込んだトランザクション境界、dry-run の容易さ等)。
本作業は

1. 件数次第ではバッチ処理 (commit 単位を細かく刻む) が必要
2. dry-run で「何件変わるか」を運用上必ず確認
3. 失敗時はトランザクション巻き戻しで簡単に rerun

の 3 点から、**Python スクリプトで実装し、Alembic 0015 とは独立**に
実行する方針。0015 は backup table の物理 drop、本スクリプトは v1 → v2
データ変換、と責務を完全に分離している。

"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Ensure backend/ on sys.path so `app.*` imports resolve when the script is
# invoked via `python scripts/...`.
_BACKEND_ROOT = _SCRIPTS_DIR.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.patient import Patient  # noqa: E402

# 7 weekday short codes used in JSONB (Mon..Sun).
_WEEKDAY_CODES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEEKDAY_SET = set(_WEEKDAY_CODES)

# v2 schema (§4.1) で削除確定のトップレベルキー (entries にも引き継がない)
_DROP_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "weekday_priority",  # §4.1 削除済み
    "ng_weekdays",  # §4.1 削除済み
)


# ---------------------------------------------------------------------------
# Pure conversion helpers (DB 非依存。テストはここを直接叩く)
# ---------------------------------------------------------------------------


def _is_already_v2(pattern: dict[str, Any]) -> bool:
    """``entries`` キーが既に list として入っていれば v2 形式とみなす."""
    entries = pattern.get("entries")
    return isinstance(entries, list)


def _normalize_weekdays(value: Any) -> list[str]:
    """``preferred_weekdays`` を Mon..Sun の正規化済みリストに変換."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item in _WEEKDAY_SET and item not in out:
            out.append(item)
    return out


def convert_weekly_pattern(raw: Any) -> dict[str, Any] | None:
    """v1 形式の weekly_pattern dict を v2 形式へ変換して返す.

    Args:
        raw: 元 JSONB (dict / None / その他不正値)

    Returns:
        変換後 dict、または変換不要 / 不可能なら None。

    変換規則:
        * ``raw`` が dict でない、または None → None (no-op)
        * 既に ``entries`` を持つ (=v2 済み) → None (no-op)
        * ``preferred_weekdays`` の各曜日に entry を生成 (staff_count=1)
        * v2 で削除確定のキー (weekday_priority / ng_weekdays) を drop
        * その他のキー (frequency_per_week, time_type, preferred_start/end,
          service_minutes, visit_frequency, visit_weeks 等) は温存
    """
    if not isinstance(raw, dict):
        return None
    if _is_already_v2(raw):
        return None

    new = deepcopy(raw)

    # v2 schema で消す (§4.1 / §7.1) 上位キーを drop。
    for key in _DROP_TOP_LEVEL_KEYS:
        new.pop(key, None)

    # entries[] を生成
    weekdays = _normalize_weekdays(raw.get("preferred_weekdays"))
    time_type = raw.get("time_type") if isinstance(raw.get("time_type"), str) else None
    preferred_start = (
        raw.get("preferred_start") if isinstance(raw.get("preferred_start"), str) else None
    )
    preferred_end = raw.get("preferred_end") if isinstance(raw.get("preferred_end"), str) else None
    raw_service_minutes = raw.get("service_minutes")
    service_minutes: int | None
    if isinstance(raw_service_minutes, bool):
        # bool は int の subclass のため明示弾く
        service_minutes = None
    elif isinstance(raw_service_minutes, int):
        service_minutes = raw_service_minutes
    else:
        service_minutes = None

    entries: list[dict[str, Any]] = []
    for weekday in weekdays:
        entry: dict[str, Any] = {
            "weekday": weekday,
            "staff_count": 1,
        }
        if time_type is not None:
            entry["time_type"] = time_type
        if preferred_start is not None:
            entry["preferred_start"] = preferred_start
        if preferred_end is not None:
            entry["preferred_end"] = preferred_end
        if service_minutes is not None:
            entry["service_minutes"] = service_minutes
        entries.append(entry)

    new["entries"] = entries
    return new


def split_special_week(
    raw: Any,
) -> tuple[dict[str, Any] | None, list[dict[str, int]]]:
    """旧 ``patients.special_week`` JSONB を v2 形式へ分離する.

    Args:
        raw: 旧 JSONB。 ``{"pattern": {...}, "weeks": [...]}`` を想定するが、
             alternative shapes (``{"items": [...]}`` 等) は best-effort で扱う。

    Returns:
        ``(special_weekly_pattern, special_week_active)``
        - special_weekly_pattern: v2 形式に変換済みの dict、または None
        - special_week_active: ``[{iso_year, iso_week}, ...]`` リスト (空可)

    変換規則:
        * raw が dict でない → ``(None, [])``
        * raw["pattern"] を ``convert_weekly_pattern`` で v2 化。
          dict ではあるが既に v2 (entries 持ち) ならそのまま採用。
        * raw["weeks"] / raw.get("active_weeks") などから
          ``[{iso_year, iso_week}]`` リストを抽出 (重複は除去)。
    """
    if not isinstance(raw, dict):
        return None, []

    # ---- pattern ----
    pat = raw.get("pattern")
    converted_pattern: dict[str, Any] | None
    if isinstance(pat, dict):
        if _is_already_v2(pat):
            converted_pattern = deepcopy(pat)
            # トップレベル不要キーは念のため除去
            for key in _DROP_TOP_LEVEL_KEYS:
                converted_pattern.pop(key, None)
        else:
            converted_pattern = convert_weekly_pattern(pat)
            if converted_pattern is None:
                # v1 dict だったが convert 結果 None = 入力が dict でない場合のみ
                # 起こりうる。フォールバックとして deepcopy + 不要キー drop。
                converted_pattern = deepcopy(pat)
                for key in _DROP_TOP_LEVEL_KEYS:
                    converted_pattern.pop(key, None)
                converted_pattern.setdefault("entries", [])
    else:
        converted_pattern = None

    # ---- active weeks ----
    raw_weeks: Any = raw.get("weeks")
    if raw_weeks is None:
        raw_weeks = raw.get("active_weeks")
    active: list[dict[str, int]] = []
    if isinstance(raw_weeks, list):
        seen: set[tuple[int, int]] = set()
        for item in raw_weeks:
            if not isinstance(item, dict):
                continue
            try:
                iy = int(item["iso_year"])
                iw = int(item["iso_week"])
            except (KeyError, TypeError, ValueError):
                continue
            if iw < 1 or iw > 53 or iy < 2000 or iy > 2100:
                continue
            week_key: tuple[int, int] = (iy, iw)
            if week_key in seen:
                continue
            seen.add(week_key)
            active.append({"iso_year": iy, "iso_week": iw})

    return converted_pattern, active


# ---------------------------------------------------------------------------
# DB driver
# ---------------------------------------------------------------------------


@dataclass
class MigrateSummary:
    scanned: int = 0
    weekly_pattern_converted: int = 0
    weekly_pattern_already_v2: int = 0
    special_weekly_pattern_set: int = 0
    special_week_active_set: int = 0
    special_weekly_pattern_kept_existing: int = 0
    rows_changed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "weekly_pattern_converted": self.weekly_pattern_converted,
            "weekly_pattern_already_v2": self.weekly_pattern_already_v2,
            "special_weekly_pattern_set": self.special_weekly_pattern_set,
            "special_week_active_set": self.special_week_active_set,
            "special_weekly_pattern_kept_existing": (self.special_weekly_pattern_kept_existing),
            "rows_changed": self.rows_changed,
            "error_count": len(self.errors),
        }


async def migrate(*, apply: bool) -> MigrateSummary:
    """Walk all (non-deleted) patients and convert v1 JSONB into v2 shape."""
    factory = get_session_factory()
    summary = MigrateSummary()

    async with factory() as session:
        rows = (await session.scalars(select(Patient).where(Patient.deleted_at.is_(None)))).all()
        for patient in rows:
            summary.scanned += 1
            try:
                row_changed = _process_one(patient, summary)
            except Exception as exc:  # noqa: BLE001
                summary.errors.append(
                    f"patient id={getattr(patient, 'id', '?')} code="
                    f"{getattr(patient, 'code', '?')}: {exc!r}"
                )
                continue
            if row_changed:
                summary.rows_changed += 1
        if apply:
            await session.commit()
        else:
            await session.rollback()

    return summary


def _process_one(patient: Patient, summary: MigrateSummary) -> bool:
    """Mutate one Patient row in-memory. Return True if anything changed."""
    changed = False

    # ---- weekly_pattern ----
    wp_new = convert_weekly_pattern(patient.weekly_pattern)
    if wp_new is not None:
        patient.weekly_pattern = wp_new
        summary.weekly_pattern_converted += 1
        changed = True
    elif isinstance(patient.weekly_pattern, dict) and _is_already_v2(patient.weekly_pattern):
        summary.weekly_pattern_already_v2 += 1

    # ---- special_week → special_weekly_pattern + special_week_active ----
    legacy = getattr(patient, "special_week", None)
    if isinstance(legacy, dict) and legacy:
        sp_pattern, active = split_special_week(legacy)
        # 既に新カラムが non-null の場合は上書きしない (= 冪等性).
        if sp_pattern is not None and not patient.special_weekly_pattern:
            patient.special_weekly_pattern = sp_pattern
            summary.special_weekly_pattern_set += 1
            changed = True
        elif sp_pattern is not None and patient.special_weekly_pattern:
            summary.special_weekly_pattern_kept_existing += 1
        if active and not patient.special_week_active:
            patient.special_week_active = active
            summary.special_week_active_set += 1
            changed = True

    return changed


# ---------------------------------------------------------------------------
# Entry point / CLI
# ---------------------------------------------------------------------------


def _format_summary(summary: MigrateSummary, *, mode: str) -> str:
    parts = [
        f"[migrate_v1_to_v2:{mode}]",
        f"scanned={summary.scanned}",
        f"rows_changed={summary.rows_changed}",
        f"weekly_pattern_converted={summary.weekly_pattern_converted}",
        f"weekly_pattern_already_v2={summary.weekly_pattern_already_v2}",
        f"special_weekly_pattern_set={summary.special_weekly_pattern_set}",
        f"special_week_active_set={summary.special_week_active_set}",
        f"special_weekly_pattern_kept_existing={summary.special_weekly_pattern_kept_existing}",
        f"errors={len(summary.errors)}",
    ]
    return " ".join(parts)


def _print_errors(errors: Iterable[str]) -> None:
    for err in errors:
        print(f"  ERR: {err}", file=sys.stderr)


async def _main(apply: bool) -> int:
    try:
        summary = await migrate(apply=apply)
    finally:
        if apply:
            await dispose_engine()

    mode = "APPLIED" if apply else "DRY-RUN"
    print(_format_summary(summary, mode=mode))
    if summary.errors:
        _print_errors(summary.errors)
    if not apply and summary.rows_changed:
        print("  (re-run with --apply to commit)")
    # 失敗があれば exit code 1 で返す (CI / 運用検証で察知できるように)
    return 1 if summary.errors else 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Convert v1 patient JSONB (weekly_pattern / special_week) into the "
            "v2 shape (weekly_pattern.entries[] + staff_count, "
            "special_weekly_pattern + special_week_active). Idempotent."
        )
    )
    grp = p.add_mutually_exclusive_group()
    grp.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="report counts without writing (default)",
    )
    grp.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        default=False,
        help="commit the conversion to the DB",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(_main(apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
