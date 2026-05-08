"""W18 Phase 0-a: course.code が template.label と不整合な行を修正する.

## 背景 (本番障害 2026-W18 / Wave 16 残骸)

Wave 16 (W16) で本店の course_templates が label A-E (5 ラベル) を持つ運用に
拡張された。当時の ``schedule.py::_get_or_create_course_for_template_week``
は ``courses.code`` の CHECK 制約 (旧: ``('A','B','C','D','M')``) と整合させる
ために **label='E' を code='M' へ丸める** フォールバックを暫定で入れていた。

W16 codex-fix で migration 0023 が CHECK 制約に 'E' を追加し、helper の
"E→M 丸め" は削除されたが、**migration が本番に当たる前に旧 helper で
INSERT されたデータが残っている**:

    template_id = 本店-E (label='E')
    code        = 'M'         <-- label と不整合
    office_id   = 本店

このまま本店-M template (label='M') の同 (year, week, weekday) に対して
``_get_or_create_course_for_template_week`` が呼ばれると、helper は
``(office_id, code='M', year, week, weekday)`` で UNIQUE 制約
``uq_courses_year_week_weekday_code_office`` に衝突して 500 を返す
(本番症状: place-and-fix 500 ``UniqueViolationError``)。

## このスクリプトの責務

``courses`` テーブルから ``c.code != ct.label`` (template が生きている範囲) の
行を抽出し、``c.code = ct.label`` に正規化する。1 文字単位 (label の先頭 1
文字) で揃えるのは helper の挙動と一致させるため。

* ``--dry-run`` (default): 対象行を表示するのみ
* ``--apply``           : 実 UPDATE を発行

冪等。再実行しても何も起こらない (差分が無くなる)。

## 注意

- 本スクリプトは **本番 VPS 上で 1 回だけ手動実行** する想定 (Phase C
  デプロイ手順に組み込む)。
- ``code='M'`` のうち本来 label='E' から派生したものを 'E' に書き戻すのが
  主目的だが、他の不整合パターン (例: helper bug 等で発生した取りこぼし)
  も同じロジックで救済される。
- UNIQUE 衝突を防ぐため UPDATE は 1 件ずつ実行する。新コードが既に同
  (office_id, code, year, week, weekday) で存在する場合は **skip** し、
  warning として出力する (運用者が手動で判断する)。

Usage:
    python scripts/cleanup_w16_course_code_mismatch.py            # dry-run (default)
    python scripts/cleanup_w16_course_code_mismatch.py --apply    # actually update
"""

# ruff: noqa: I001
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

# Ensure backend/ is importable when invoked as `python scripts/cleanup_w16_course_code_mismatch.py`.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.course import Course  # noqa: E402
from app.models.course_template import CourseTemplate  # noqa: E402


# courses.code CHECK で許される値 (W16 codex fix / migration 0023 後).
_ALLOWED_CODES: frozenset[str] = frozenset({"A", "B", "C", "D", "E", "M"})


@dataclass
class _Mismatch:
    course_id: str
    office_id: str
    iso_year: int
    iso_week: int
    weekday: int
    current_code: str
    template_id: str
    template_label: str
    desired_code: str


@dataclass
class _Report:
    scanned: int = 0
    mismatches: int = 0
    fixed: int = 0
    skipped_collision: int = 0
    skipped_invalid_label: int = 0


def _label_to_code(label: str | None) -> str | None:
    """``CourseTemplate.label`` から ``Course.code`` を 1 文字 (大文字) に正規化.

    ``schedule.py::_get_or_create_course_for_template_week`` と同じロジックで、
    1 文字目を大文字化し、CHECK 制約 (``A/B/C/D/E/M``) のいずれかであることを
    確認する。それ以外は ``None`` を返し、呼出側で skip する。
    """
    if label is None:
        return None
    first = label.strip()[:1].upper()
    if first not in _ALLOWED_CODES:
        return None
    return first


async def _scan_mismatches() -> list[_Mismatch]:
    """``courses`` x ``course_templates`` を JOIN して不整合行を抽出する.

    ``deleted_at IS NULL`` (生きている template) の course のみを対象にする。
    template 削除済みの course は孤児として別途運用判断するためここでは
    触れない。
    """
    factory = get_session_factory()
    found: list[_Mismatch] = []
    async with factory() as session:
        rows = (
            await session.execute(
                select(Course, CourseTemplate)
                .join(CourseTemplate, Course.template_id == CourseTemplate.id)
                .where(
                    Course.deleted_at.is_(None),
                    CourseTemplate.deleted_at.is_(None),
                )
            )
        ).all()

        for course, template in rows:
            desired = _label_to_code(template.label)
            if desired is None:
                # label が CHECK 値域外 (本来あり得ないが防御的に skip)
                continue
            if course.code == desired:
                continue
            found.append(
                _Mismatch(
                    course_id=str(course.id),
                    office_id=str(course.office_id),
                    iso_year=int(course.iso_year),
                    iso_week=int(course.iso_week),
                    weekday=int(course.weekday),
                    current_code=course.code,
                    template_id=str(template.id),
                    template_label=template.label or "",
                    desired_code=desired,
                )
            )
    return found


async def _apply_all_fixes(mismatches: list[_Mismatch], report: _Report) -> None:
    """全不整合を 1 トランザクションで UPDATE する (W18 Codex-fix 中-3).

    旧実装は 1 行ごとに ``await session.commit()`` を発行しており、
        - 連続 commit のオーバーヘッド
        - 事前 SELECT (collision check) と UPDATE の間で TOCTOU で
          UNIQUE 衝突 → script crash → 残りが未処理
    という運用課題があった。

    新実装:
        - 全 fix を 1 セッション (= 1 TX) で savepoint nested に walk
        - 各 UPDATE 前に collision SELECT で事前検出 → skip
        - SELECT で抜け落ちた race も savepoint 内 IntegrityError で catch
          → savepoint rollback + skip (継続)
        - 最後に 1 commit

    報告 (``report``):
        - ``fixed``: UPDATE 成功件数
        - ``skipped_collision``: UNIQUE 衝突 (事前 SELECT or IntegrityError) で
          スキップした件数
    """
    factory = get_session_factory()
    async with factory() as session:
        for mismatch in mismatches:
            course_uuid = uuid.UUID(mismatch.course_id)
            office_uuid = uuid.UUID(mismatch.office_id)

            # 事前 collision SELECT (同一 TX 内の最新ビュー).
            collision = await session.scalar(
                select(Course.id).where(
                    Course.iso_year == mismatch.iso_year,
                    Course.iso_week == mismatch.iso_week,
                    Course.weekday == mismatch.weekday,
                    Course.code == mismatch.desired_code,
                    Course.office_id == office_uuid,
                    Course.deleted_at.is_(None),
                )
            )
            if collision is not None and str(collision) != mismatch.course_id:
                report.skipped_collision += 1
                print(
                    f"  skipped course_id={mismatch.course_id} "
                    f"({mismatch.current_code!r} -> {mismatch.desired_code!r}): "
                    f"collision: another course already exists at "
                    f"(office={mismatch.office_id}, code={mismatch.desired_code}, "
                    f"y={mismatch.iso_year}, w={mismatch.iso_week}, "
                    f"wd={mismatch.weekday}) id={collision}"
                )
                continue

            course = await session.get(Course, course_uuid)
            if course is None:
                report.skipped_collision += 1
                print(
                    f"  skipped course_id={mismatch.course_id}: "
                    f"row disappeared between scan and apply"
                )
                continue

            # savepoint で UPDATE 試行 → IntegrityError は catch して継続.
            try:
                async with session.begin_nested():
                    course.code = mismatch.desired_code
                    await session.flush()
            except IntegrityError as exc:
                report.skipped_collision += 1
                print(
                    f"  skipped course_id={mismatch.course_id} "
                    f"({mismatch.current_code!r} -> {mismatch.desired_code!r}): "
                    f"IntegrityError on UPDATE (likely race): {exc.__class__.__name__}"
                )
                continue

            report.fixed += 1
            print(f"  fixed course_id={mismatch.course_id}: ok")

        # 最後に 1 commit (失敗 savepoint は既に rollback 済み).
        await session.commit()


async def _main(*, apply: bool) -> int:
    report = _Report()
    try:
        mismatches = await _scan_mismatches()
        report.scanned = len(mismatches)  # 厳密には rows 数だが運用的に十分

        if not mismatches:
            print("no mismatches found — nothing to do.")
            return 0

        print(f"detected {len(mismatches)} course(s) with code != template.label:")
        print()
        for m in mismatches:
            print(
                f"  course_id={m.course_id} office={m.office_id} "
                f"y={m.iso_year} w={m.iso_week} wd={m.weekday} "
                f"code={m.current_code!r} -> {m.desired_code!r} "
                f"(template={m.template_id} label={m.template_label!r})"
            )
        report.mismatches = len(mismatches)

        if not apply:
            print()
            print("[dry-run] no changes applied. re-run with --apply to update.")
            return 0

        print()
        print("applying fixes (single transaction, savepoint per row)...")
        await _apply_all_fixes(mismatches, report)
    finally:
        await dispose_engine()

    print()
    print("---- summary ----")
    print(
        f"mismatches: {report.mismatches} / fixed: {report.fixed} / "
        f"skipped (collision): {report.skipped_collision}"
    )
    # collision が残っている場合は exit 1 (運用者が手動判断する必要)
    return 0 if report.skipped_collision == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "W18 Phase 0-a: courses.code != course_templates.label の行を "
            "label に合わせて正規化する (place-and-fix 500 バグの恒久対策)."
        )
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="(default) print mismatches only; no UPDATE",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="actually issue UPDATE statements",
    )
    args = parser.parse_args()
    return asyncio.run(_main(apply=bool(args.apply)))


if __name__ == "__main__":
    raise SystemExit(main())
