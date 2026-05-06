"""W13: 既存患者の主担当拠点を一括 resolve する 1 回限り運用スクリプト.

## 目的

`patients.primary_office_id IS NULL` の既存患者に対し、`address` から
`OfficeAssigner.resolve_with_details` で拠点を判定して一括 UPDATE する。

Wave 12 で新規・編集時の自動判定が実装済みのため、本スクリプトは
過去データへの後方適用を担う。

## 使い方

::

    # 集計のみ確認 (DB 書き込みなし)
    python -m scripts.resolve_existing_patients --dry-run

    # 実適用 (1 トランザクションで全件 UPDATE → commit)
    python -m scripts.resolve_existing_patients --apply

--dry-run と --apply は排他必須。両方指定・未指定はエラー。

## 注意

- --apply は確認プロンプトなしで即実行する。
  Director が VPS 上で dry-run を確認後、ユーザー承認を得てから apply する運用を想定。
- confidence='exact' または 'fuzzy' の患者のみ UPDATE する。
- confidence='none' (エリア外) の患者は UPDATE しない。
- address が空 / NULL の患者はスキップする (no-address カウント)。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_BACKEND_ROOT = _SCRIPTS_DIR.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.patient import Patient  # noqa: E402
from app.services.office_assigner import OfficeAssigner  # noqa: E402

# UPDATE する confidence 値のセット
_RESOLVABLE = frozenset({"exact", "fuzzy"})

# サンプル表示件数
_SAMPLE_SIZE = 5


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PatientResult:
    """1 患者の resolve 結果."""

    code: str
    name: str
    address: str
    office_name: str | None
    confidence: str  # 'exact' / 'fuzzy' / 'none' / 'no-address' / 'error'
    error: str | None = None


@dataclass
class ResolveSummary:
    """全体集計."""

    total: int = 0
    exact: int = 0
    fuzzy: int = 0
    none_: int = 0
    no_address: int = 0
    errors: int = 0
    by_office: dict[str, int] = field(default_factory=dict)
    samples: list[PatientResult] = field(default_factory=list)
    all_results: list[PatientResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


async def resolve_patients(*, apply: bool) -> ResolveSummary:
    """全対象患者を resolve して集計を返す。apply=True なら UPDATE も行う."""
    factory = get_session_factory()
    summary = ResolveSummary()

    async with factory() as session:
        stmt = select(Patient).where(
            Patient.deleted_at.is_(None),
            Patient.primary_office_id.is_(None),
        )
        patients = list((await session.scalars(stmt)).all())
        summary.total = len(patients)

        for patient in patients:
            address = patient.address
            code = str(getattr(patient, "code", "?"))
            name = str(getattr(patient, "name", "?"))

            # address 空 / NULL はスキップ
            if not address or not address.strip():
                summary.no_address += 1
                result = PatientResult(
                    code=code,
                    name=name,
                    address="",
                    office_name=None,
                    confidence="no-address",
                )
                summary.all_results.append(result)
                continue

            try:
                resolution = await OfficeAssigner.resolve_with_details(session, address)
            except Exception as exc:  # noqa: BLE001
                summary.errors += 1
                result = PatientResult(
                    code=code,
                    name=name,
                    address=address,
                    office_name=None,
                    confidence="error",
                    error=repr(exc),
                )
                summary.all_results.append(result)
                continue

            confidence = resolution.confidence
            office = resolution.office
            office_name = office.name if office is not None else None

            if confidence == "exact":
                summary.exact += 1
            elif confidence == "fuzzy":
                summary.fuzzy += 1
            else:
                summary.none_ += 1

            result = PatientResult(
                code=code,
                name=name,
                address=address,
                office_name=office_name,
                confidence=confidence,
            )
            summary.all_results.append(result)

            # 拠点別カウント (resolvable のみ)
            if confidence in _RESOLVABLE and office_name:
                summary.by_office[office_name] = summary.by_office.get(office_name, 0) + 1

            # --apply: 1 TX 内で UPDATE
            if apply and confidence in _RESOLVABLE and office is not None:
                patient.primary_office_id = office.id

        # サンプル (先頭 _SAMPLE_SIZE 件)
        summary.samples = summary.all_results[:_SAMPLE_SIZE]

        if apply:
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        else:
            await session.rollback()

    return summary


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _format_address_truncated(address: str, width: int = 30) -> str:
    """住所を指定幅に切り詰めて返す."""
    if len(address) > width:
        return address[:width] + "…"
    return address


def _print_summary(summary: ResolveSummary, *, apply: bool) -> None:
    mode = "APPLY" if apply else "DRY-RUN"
    resolved_count = summary.exact + summary.fuzzy

    print(f"=== Resolve existing patients ({mode}) ===")
    print("Target: patients with primary_office_id IS NULL AND deleted_at IS NULL")
    print()
    print(f"Total candidates: {summary.total}")
    print(f"  - resolved (exact): {summary.exact}")
    print(f"  - resolved (fuzzy): {summary.fuzzy}")
    print(f"  - out of area (none): {summary.none_}")
    print(f"  - no-address skipped: {summary.no_address}")
    print(f"  - errors: {summary.errors}")
    print()

    if summary.by_office:
        verb = "assigned" if apply else "would be assigned"
        print(f"By office ({verb}):")
        for office_name, count in sorted(summary.by_office.items(), key=lambda x: -x[1]):
            print(f"  - {office_name}: {count}")
        print()

    if summary.samples:
        print(f"Sample (first {len(summary.samples)}):")
        for r in summary.samples:
            addr_display = _format_address_truncated(r.address) if r.address else "(no address)"
            office_display = r.office_name if r.office_name else "-"
            print(f"  {r.code} {r.name} ({addr_display}) → {office_display} ({r.confidence})")
        print()

    if apply:
        still_null = summary.total - resolved_count
        print(f"Applied: {resolved_count} rows updated.")
        print(
            f"Verification: {still_null} patients still have primary_office_id IS NULL"
            " (out of area / no-address / errors)."
        )
    else:
        print("Run with --apply to commit changes.")

    # エラー詳細
    if summary.errors > 0:
        print("\nErrors:", file=sys.stderr)
        for r in summary.all_results:
            if r.confidence == "error":
                print(f"  {r.code} {r.name}: {r.error}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point / CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "既存患者 (primary_office_id IS NULL) の address を "
            "OfficeAssigner で resolve して primary_office_id を一括 UPDATE する。\n"
            "--dry-run か --apply のどちらか一方を必ず指定すること。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="集計のみ表示。DB は書き換えない。",
    )
    grp.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="confidence=exact|fuzzy の患者の primary_office_id を 1 TX で UPDATE して commit する。",
    )
    return p


async def _main(apply: bool) -> int:
    try:
        summary = await resolve_patients(apply=apply)
    finally:
        await dispose_engine()

    _print_summary(summary, apply=apply)
    return 1 if summary.errors else 0


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(_main(apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
