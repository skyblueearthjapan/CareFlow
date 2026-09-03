"""連携結果レポート (sync-report) のテスト。

* 純関数 `render_sync_report_html`: 章の有無・改ページクラス・15 行打ち切り・
  用語集が「出た理由コードだけ」・明細なし版の注意書き・エスケープ
* API `GET /api/v1/integrations/kaipoke/jobs/{id}/report`: 200 json / html /
  対象外 op 422 / 不在 404 / 改修前ジョブ (result.details のみ) = summary_only /
  非 admin 403 / 未確定 outcome を緑にしない / 月跨ぎ週の日番号解決
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.security import create_access_token, hash_password
from app.models.kaipoke_job import KaipokeJob, KaipokeJobItem
from app.models.user import User
from app.services.kaipoke.sync_report import (
    AttentionRow,
    DaySection,
    EventRow,
    ExclusionGroup,
    JobInfo,
    ReplaceDay,
    ReplaceSkip,
    RowChange,
    Summary,
    SyncReport,
    TraineeSolo,
    Verification,
    change_text,
)
from app.services.kaipoke.sync_report_html import render_sync_report_html

WEEK_START = date(2026, 9, 7)  # 月曜


# ---------------------------------------------------------------------------
# 純関数 (render)
# ---------------------------------------------------------------------------


def _row(
    start: str = "16:00",
    user: str = "熊澤 花子",
    outcome: str = "success",
    reason: str | None = None,
) -> RowChange:
    label = {"success": "成功", "failed": "失敗", "skipped": "スキップ"}.get(outcome, outcome)
    tag = {"success": "ok", "failed": "ng", "skipped": "warn"}.get(outcome, "muted")
    return RowChange(
        date=WEEK_START.isoformat(),
        start=start,
        end="16:35",
        user_name=user,
        action="edit",
        action_label="変更",
        outcome=outcome,
        outcome_label=label,
        outcome_tag=tag,
        change_text="16:00 熊澤 → 17:15 髙梨",
        reason=reason,
        reason_label=("旧行が残ったまま新行が追加された（二重登録の疑い）" if reason else None),
    )


def _report(
    *,
    direction: str = "outbound",
    day_rows: int = 3,
    attention_count: int = 0,
    detail_level: str = "full",
    reason_codes: list[tuple[str, str]] | None = None,
    patient: str = "熊澤 花子",
) -> SyncReport:
    rows = [_row(start=f"{9 + i % 9:02d}:00", user=patient) for i in range(day_rows)]
    day = DaySection(date=WEEK_START.isoformat(), weekday="月", label="9/7（月）", rows=rows)
    attention = [
        AttentionRow(
            date=(WEEK_START + timedelta(days=i % 7)).isoformat(),
            time=f"{9 + i % 9:02d}:00",
            subject=f"利用者{i}",
            what="変更",
            outcome_label="失敗",
            outcome_tag="ng",
            reason_label="対象が見つからない",
        )
        for i in range(attention_count)
    ]
    return SyncReport(
        job=JobInfo(
            id="0123456789abcdef0123456789abcdef",
            op="apply" if direction == "outbound" else "smart-apply",
            op_label="カイポケへ反映（送信）" if direction == "outbound" else "取り込み",
            direction=direction,
            status="completed",
            week_start=WEEK_START.isoformat(),
            week_end=(WEEK_START + timedelta(days=6)).isoformat(),
            started_at=datetime(2026, 9, 7, 8, 0, tzinfo=UTC),
            completed_at=datetime(2026, 9, 7, 8, 12, 30, tzinfo=UTC),
            duration_sec=750,
            executor_name="今泉",
        ),
        summary=Summary(total=day_rows, success=day_rows, failed=0, attention=len(attention)),
        conclusion_tone="amber" if attention else "green",
        conclusion_text="テスト結論",
        exclusions=[ExclusionGroup(reason="unassigned", label="担当なし", count=2)],
        attention=attention,
        days=[day],
        excluded_rows=[],
        replace_days=[ReplaceDay(date=WEEK_START.isoformat(), weekday="月", wiped=3, inserted=4)],
        skips=[
            ReplaceSkip(
                date=WEEK_START.isoformat(),
                start="10:00",
                user_name="山岡",
                staff_name="看護A",
                reason="patient_not_found",
                reason_label="カイポケ側に利用者が見つからない",
            )
        ],
        trainee_solo=[TraineeSolo(staff_name="新人B", count=2)],
        events=[
            EventRow(
                date=WEEK_START.isoformat(),
                start="08:30",
                end="09:00",
                staff_name="看護A",
                title="朝会",
                action="add",
                action_label="追加",
                outcome="success",
                outcome_label="成功",
                outcome_tag="ok",
                change_text="",
            )
        ],
        verification=Verification(available=False, note="スナップショットがありません"),
        detail_level=detail_level,
        reason_codes=reason_codes if reason_codes is not None else [("unassigned", "担当なし")],
        generated_at=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
    )


def test_render_titles_by_direction():
    out = render_sync_report_html(_report(direction="outbound"))
    assert "らく助 → カイポケ 送信結果報告" in out
    assert "送信後の確認" in out

    inb = render_sync_report_html(_report(direction="inbound"))
    assert "カイポケ → らく助 取込結果報告" in inb
    assert "取込後の確認" in inb


def test_render_times_are_japan_standard_time():
    """コンテナが UTC でも報告書の時刻は JST (started 08:00Z → 17:00, generated 09:00Z → 18:00)。"""
    out = render_sync_report_html(_report(direction="outbound"))
    assert "2026-09-07 17:00 〜 17:12" in out
    assert "作成 2026-09-07 18:00" in out
    assert "08:00" not in out.split("実行:")[1][:40]


def test_render_print_rules_cover_break_and_thead():
    out = render_sync_report_html(_report())
    # 表紙は単独ページ (break-after) + 明細の先頭で改ページ
    assert '<section class="cover">' in out
    assert ".cover{break-after:page}" in out
    assert '<div class="pb">' in out
    assert "@page{size:A4 portrait;margin:12mm 13mm}" in out
    # 割れた表の見出し行を各ページに繰り返す
    assert "thead{display:table-header-group}" in out
    assert "<thead>" in out
    assert "section.day.compact{break-inside:avoid}" in out
    # 補足は改ページを強制しない (数行のための白紙ページを作らない)
    assert '<section class="appendix">' in out
    assert "section.appendix{break-inside:avoid}" in out
    # フッタは静的 (position:fixed は Chrome で最終行に重なるため使わない)。
    # 表紙末尾と文書末尾の 2 箇所に出す。
    assert "position:fixed" not in out
    assert out.count('class="pfoot"') == 2
    assert "らく助 × カイポケ 連携結果報告" in out


def test_change_text_wraps_only_at_the_arrow():
    """「16:00 熊澤 → 17:15 髙梨」の各辺は nowrap (狭い列で全行が 2 行になるのを防ぐ)。"""
    out = render_sync_report_html(_report())
    assert '<span class="nw">16:00 熊澤</span> → <span class="nw">17:15 髙梨</span>' in out
    assert "td.chg .nw{white-space:nowrap}" in out
    # 変更内容だけ可変幅、他は固定幅
    assert 'style="min-width:52mm">変更内容' in out
    assert 'style="width:22mm">時刻' in out


def test_day_section_compact_only_when_14_rows_or_fewer():
    small = render_sync_report_html(_report(day_rows=14))
    assert '<section class="day compact">' in small

    big = render_sync_report_html(_report(day_rows=15))
    assert '<section class="day compact">' not in big
    assert '<section class="day">' in big


def test_attention_capped_at_15_with_overflow_note():
    out = render_sync_report_html(_report(attention_count=18))
    assert "他 3 件は明細参照。" in out
    # 15 行だけ出ている (16 番目の subject は表紙に無い)
    assert "利用者14" in out
    assert "利用者15" not in out

    exact = render_sync_report_html(_report(attention_count=15))
    assert "件は明細参照" not in exact


def test_reason_glossary_lists_only_used_codes():
    out = render_sync_report_html(
        _report(reason_codes=[("delete_not_verified", "削除の成否を確認できなかった")])
    )
    assert "delete_not_verified" in out
    assert "削除の成否を確認できなかった" in out
    assert "add_failed_row_lost" not in out


def test_summary_only_notice_and_sections():
    out = render_sync_report_html(_report(detail_level="summary_only"))
    assert "改修前のジョブのため行単位の明細はありません" in out
    # 置換 / 新人単独 / イベントの章
    assert "置換した日" in out
    assert "取り込めなかった行" in out
    assert "新人単独の警告" in out
    assert "イベント" in out


def test_render_escapes_patient_names():
    out = render_sync_report_html(_report(patient="<script>x</script>"))
    assert "<script>x</script>" not in out
    assert "&lt;script&gt;" in out


def test_change_text_shows_only_changed_fields():
    txt = change_text(
        {"start": "16:00", "end": "16:35", "staff1": "熊澤", "service": "精神"},
        {"start": "17:15", "end": "16:35", "staff1": "髙梨", "service": "精神"},
    )
    assert txt == "16:00 熊澤 → 17:15 髙梨"
    # 変化なし → 空
    assert change_text({"start": "16:00"}, {"start": "16:00"}) == ""
    # サービス内容が変わったときは出す
    assert "精神" in change_text({"service": "看護"}, {"service": "精神"})


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


async def _admin_headers(db) -> tuple[dict[str, str], User]:
    admin = User(
        email="sync-report-admin@example.com",
        username="いまいずみ",
        password_hash=hash_password("x"),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    return {
        "Authorization": f"Bearer {create_access_token(subject=str(admin.id), role=admin.role)}"
    }, admin


def _row_content(day: date, start: str, user: str, outcome: str, **extra) -> dict:
    content = {
        "kind": "row",
        "direction": "outbound",
        "date": day.isoformat(),
        "start": start,
        "end": "16:35",
        "user_name": user,
        "action": "edit",
        "before": {"start": start, "staff1": "熊澤"},
        "after": {"start": "17:15", "staff1": "髙梨"},
        "outcome": outcome,
        "ref": {},
    }
    content.update(extra)
    return content


@pytest.mark.asyncio
async def test_report_endpoint_json_counts_and_html(client, db):
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="push",
        week_start=WEEK_START,
        status="completed",
        params={"op": "apply", "dry_run": False, "week_start": WEEK_START.isoformat()},
        result_summary={"result": {"success": 2, "failed": 1}},
        started_at=datetime(2026, 9, 3, 8, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 3, 8, 10, tzinfo=UTC),
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.flush()
    db.add_all(
        [
            KaipokeJobItem(
                job_id=job.id,
                seq=1,
                status="success",
                content=_row_content(WEEK_START, "09:00", "山岡 太郎", "success"),
            ),
            KaipokeJobItem(
                job_id=job.id,
                seq=2,
                status="failed",
                content=_row_content(
                    WEEK_START, "10:00", "清水 花子", "failed", reason="delete_not_verified"
                ),
                error_msg="削除の成否を確認できなかった",
            ),
            KaipokeJobItem(
                job_id=job.id,
                seq=3,
                status="skipped",
                content=_row_content(
                    WEEK_START + timedelta(days=1),
                    "11:00",
                    "林 一郎",
                    "excluded",
                    reason="unassigned",
                ),
                error_msg="担当なし",
            ),
        ]
    )
    await db.commit()

    res = await client.get(f"/api/v1/integrations/kaipoke/jobs/{job.id}/report", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["job"]["direction"] == "outbound"
    assert body["job"]["op"] == "apply"
    assert body["job"]["executorName"] == "いまいずみ"
    assert body["job"]["durationSec"] == 600
    assert body["detailLevel"] == "full"
    assert body["summary"]["total"] == 2  # excluded は対象外
    assert body["summary"]["success"] == 1
    assert body["summary"]["failed"] == 1
    assert body["summary"]["excluded"] == 1
    assert body["summary"]["attention"] == 1
    assert body["conclusionTone"] == "red"
    assert body["exclusions"][0]["reason"] == "unassigned"
    assert len(body["days"]) == 1  # 除外行は日セクションに出さない
    assert body["days"][0]["rows"][0]["changeText"].startswith("09:00 熊澤 → 17:15 髙梨")
    assert body["html"] and "送信結果報告" in body["html"]

    res_html = await client.get(
        f"/api/v1/integrations/kaipoke/jobs/{job.id}/report",
        params={"format": "html"},
        headers=headers,
    )
    assert res_html.status_code == 200
    assert res_html.headers["content-type"].startswith("text/html")
    assert "らく助 → カイポケ 送信結果報告" in res_html.text


@pytest.mark.asyncio
async def test_report_endpoint_inbound_replace_items(client, db):
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="fetch",
        week_start=WEEK_START,
        status="completed",
        params={"op": "replace-inbound", "week_start": WEEK_START.isoformat()},
        result_summary={},
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.flush()
    db.add_all(
        [
            KaipokeJobItem(
                job_id=job.id,
                seq=1,
                status="completed",
                content={
                    "kind": "day",
                    "direction": "inbound",
                    "date": WEEK_START.isoformat(),
                    "wiped": 3,
                    "inserted": 4,
                },
            ),
            KaipokeJobItem(
                job_id=job.id,
                seq=2,
                status="skipped",
                content={
                    "kind": "skip",
                    "direction": "inbound",
                    "date": WEEK_START.isoformat(),
                    "start": "10:00",
                    "user_name": "麻生 真里奈",
                    "staff_name": "看護A",
                    "reason": "患者を名寄せできません（らく助未登録の可能性）",
                },
            ),
            KaipokeJobItem(
                job_id=job.id,
                seq=3,
                status="completed",
                content={
                    "kind": "trainee_solo",
                    "direction": "inbound",
                    "staff_name": "新人B",
                    "count": 2,
                },
            ),
        ]
    )
    await db.commit()

    res = await client.get(f"/api/v1/integrations/kaipoke/jobs/{job.id}/report", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["job"]["direction"] == "inbound"
    assert body["replaceDays"][0]["wiped"] == 3
    assert body["skips"][0]["userName"] == "麻生 真里奈"
    assert body["traineeSolo"][0]["count"] == 2
    # スキップ + 新人単独 = 要対応 2 件、失敗ゼロなので黄
    assert body["summary"]["attention"] == 2
    assert body["conclusionTone"] == "amber"
    assert "取込後の確認" in body["html"]


@pytest.mark.asyncio
async def test_report_endpoint_summary_only_from_result_details(client, db):
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="push",
        week_start=WEEK_START,
        status="completed",
        params={"op": "apply"},
        result_summary={
            "result": {
                "success": 1,
                "failed": 1,
                "details": [
                    {"date": "7", "user": "山岡 太郎", "action": "add", "status": "success"},
                    {
                        "date": 8,
                        "user": "清水 花子",
                        "action": "delete",
                        "status": "failed",
                        "reason": "delete_not_verified",
                    },
                ],
            }
        },
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.commit()

    res = await client.get(f"/api/v1/integrations/kaipoke/jobs/{job.id}/report", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["detailLevel"] == "summary_only"
    assert body["summary"]["total"] == 2
    assert body["summary"]["failed"] == 1
    assert body["days"][0]["date"] == WEEK_START.isoformat()
    assert "改修前のジョブのため行単位の明細はありません" in body["html"]


@pytest.mark.asyncio
async def test_report_endpoint_rejects_unsupported_op_and_missing_job(client, db):
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="fetch",
        week_start=WEEK_START,
        status="completed",
        params={"op": "expand", "month": "2026-09"},
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.commit()

    res = await client.get(f"/api/v1/integrations/kaipoke/jobs/{job.id}/report", headers=headers)
    assert res.status_code == 422, res.text

    res404 = await client.get(
        f"/api/v1/integrations/kaipoke/jobs/{uuid.uuid4()}/report", headers=headers
    )
    assert res404.status_code == 404


@pytest.mark.asyncio
async def test_report_endpoint_rejects_running_job(client, db):
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="push",
        week_start=WEEK_START,
        status="running",
        params={"op": "apply"},
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.commit()

    res = await client.get(f"/api/v1/integrations/kaipoke/jobs/{job.id}/report", headers=headers)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_pending_rows_never_report_all_success(client, db):
    """RPA が結果を返さなかった行 (outcome=unknown / pending) を成功扱いにしない。

    24 行のうち 12 行が未確定 (lane ① が書く "unknown" が 11 行 + 旧経路の
    "pending" が 1 行)。件数だけ見ると失敗 0 なので、素朴に集計すると
    「全件成功」の緑になってしまう — それを踏まないことの回帰テスト。
    """
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="push",
        week_start=WEEK_START,
        status="completed",
        params={"op": "apply", "week_start": WEEK_START.isoformat()},
        result_summary={"result": {"success": 24, "failed": 0}},
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.flush()
    items = []
    for i in range(24):
        unsettled = i % 2 == 1
        # 大半は lane ① の "unknown"、1 行だけ旧経路の "pending" を混ぜる。
        outcome = ("pending" if i == 1 else "unknown") if unsettled else "success"
        items.append(
            KaipokeJobItem(
                job_id=job.id,
                seq=i + 1,
                status="completed",
                content=_row_content(
                    WEEK_START,
                    f"{9 + i // 3:02d}:{(i % 3) * 20:02d}",
                    f"利用者{i:02d}",
                    outcome,
                    **({"reason": "no_rpa_result"} if unsettled else {}),
                ),
            )
        )
    db.add_all(items)
    await db.commit()

    res = await client.get(
        f"/api/v1/integrations/kaipoke/jobs/{job.id}/report",
        params={"verify": "false"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["total"] == 24
    assert body["summary"]["success"] == 12
    assert body["summary"]["failed"] == 0
    assert body["summary"]["unresolved"] == 12
    assert body["summary"]["attention"] == 12
    assert body["conclusionTone"] == "amber"  # 緑にしない
    assert "すべてが成功" not in body["conclusionText"]
    assert "結果不明 12 件" in body["conclusionText"]
    assert '結果不明 <b class="warn">12</b>' in body["html"]
    # 要対応一覧に載る (15 行打ち切りの中に入る)
    assert "利用者01" in body["html"]


@pytest.mark.asyncio
async def test_items_with_unknown_or_empty_kind_are_ignored(client, db):
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="push",
        week_start=WEEK_START,
        status="completed",
        params={"op": "apply", "week_start": WEEK_START.isoformat()},
        result_summary={},
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.flush()
    db.add_all(
        [
            KaipokeJobItem(
                job_id=job.id,
                seq=1,
                status="completed",
                content=_row_content(WEEK_START, "09:00", "山岡 太郎", "success"),
            ),
            # 空 content / 未知の kind は明細にも件数にも混ぜない
            KaipokeJobItem(job_id=job.id, seq=2, status="completed", content={}),
            KaipokeJobItem(
                job_id=job.id, seq=3, status="completed", content={"kind": "mystery", "x": 1}
            ),
        ]
    )
    await db.commit()

    res = await client.get(
        f"/api/v1/integrations/kaipoke/jobs/{job.id}/report",
        params={"verify": "false"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["detailLevel"] == "full"
    assert body["summary"]["total"] == 1
    assert len(body["days"][0]["rows"]) == 1
    assert body["conclusionTone"] == "green"


@pytest.mark.asyncio
async def test_event_ops_skip_the_visit_reconcile(client, db):
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="push",
        week_start=WEEK_START,
        status="completed",
        params={"op": "events-outbound", "week_start": WEEK_START.isoformat()},
        result_summary={"summary": {"ok": 2, "total": 2}},
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.commit()

    res = await client.get(f"/api/v1/integrations/kaipoke/jobs/{job.id}/report", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["verification"]["available"] is False
    assert body["verification"]["note"] == "訪問の突合は対象外（イベント送信）"
    assert body["summary"]["success"] == 2
    assert body["conclusionTone"] == "green"


@pytest.mark.asyncio
async def test_summary_only_inbound_seeds_counts_from_result_summary(client, db):
    """items も details も無い取込ジョブで「対象 0 件すべてが成功」と言わない。"""
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="fetch",
        week_start=WEEK_START,
        status="completed",
        params={"op": "apply-inbound", "week_start": WEEK_START.isoformat()},
        result_summary={"cancelled": 3, "updated": 4, "added": 2, "skipped": 5, "failed": 0},
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.commit()

    res = await client.get(
        f"/api/v1/integrations/kaipoke/jobs/{job.id}/report",
        params={"verify": "false"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["detailLevel"] == "summary_only"
    assert body["summary"]["success"] == 9
    assert body["summary"]["skipped"] == 5
    assert body["summary"]["total"] == 14
    assert body["conclusionTone"] == "amber"
    assert "すべてが成功" not in body["conclusionText"]
    assert "対象 0 件" not in body["conclusionText"]


@pytest.mark.asyncio
async def test_summary_only_replace_shows_a_total_row(client, db):
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="fetch",
        week_start=WEEK_START,
        status="completed",
        params={"op": "replace-inbound", "week_start": WEEK_START.isoformat()},
        result_summary={"wiped": 30, "inserted": 28, "skipped": 2},
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.commit()

    res = await client.get(
        f"/api/v1/integrations/kaipoke/jobs/{job.id}/report",
        params={"verify": "false"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["success"] == 28
    assert body["replaceDays"] == [
        {"date": "", "weekday": "", "wiped": 30, "inserted": 28, "sundaySkipped": False}
    ]
    assert "合計" in body["html"]


@pytest.mark.asyncio
async def test_summary_only_resolves_day_numbers_across_a_month_boundary(client, db):
    """8/31〜9/6 の週: 日番号 31 → 8/31、1 → 9/1。週外の数字 (20) は捨てる。"""
    headers, admin = await _admin_headers(db)
    week = date(2026, 8, 31)  # 月曜
    job = KaipokeJob(
        job_type="push",
        week_start=week,
        status="completed",
        params={"op": "apply", "week_start": week.isoformat()},
        result_summary={
            "result": {
                "details": [
                    {"date": "31", "user": "山岡 太郎", "action": "add", "status": "success"},
                    {"date": 1, "user": "清水 花子", "action": "add", "status": "success"},
                    {"date": 20, "user": "圏外 次郎", "action": "add", "status": "success"},
                ]
            }
        },
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.commit()

    res = await client.get(
        f"/api/v1/integrations/kaipoke/jobs/{job.id}/report",
        params={"verify": "false"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    dates = [d["date"] for d in body["days"]]
    assert "2026-08-31" in dates
    assert "2026-09-01" in dates
    # 週外の日番号は日付をでっち上げず「日付なし」へ寄せる
    assert "2026-08-20" not in dates
    assert "" in dates


@pytest.mark.asyncio
async def test_include_html_false_omits_the_html(client, db):
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="push",
        week_start=WEEK_START,
        status="completed",
        params={"op": "apply"},
        result_summary={},
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.commit()

    res = await client.get(
        f"/api/v1/integrations/kaipoke/jobs/{job.id}/report",
        params={"includeHtml": "false", "verify": "false"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["html"] is None


@pytest.mark.asyncio
async def test_report_endpoint_requires_admin(client, db):
    staff_user = User(
        email="sync-report-staff@example.com",
        password_hash=hash_password("x"),
        role="staff",
    )
    db.add(staff_user)
    await db.commit()
    job = KaipokeJob(
        job_type="push",
        week_start=WEEK_START,
        status="completed",
        params={"op": "apply"},
        result_summary={},
    )
    db.add(job)
    await db.commit()

    token = create_access_token(subject=str(staff_user.id), role=staff_user.role)
    res = await client.get(
        f"/api/v1/integrations/kaipoke/jobs/{job.id}/report",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_summary_only_apply_reads_counts_from_result_block(client, db):
    """apply は件数を result_summary['result'] に持つ (details 無しの古いジョブ)。"""
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="push",
        week_start=WEEK_START,
        status="completed",
        params={"op": "apply", "week_start": WEEK_START.isoformat()},
        result_summary={"result": {"success": 22, "failed": 2}},
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.commit()

    res = await client.get(
        f"/api/v1/integrations/kaipoke/jobs/{job.id}/report",
        params={"verify": "false"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["detailLevel"] == "summary_only"
    assert body["summary"]["total"] == 24
    assert body["summary"]["success"] == 22
    assert body["summary"]["failed"] == 2
    assert body["conclusionTone"] == "red"
    assert "すべてが成功" not in body["conclusionText"]


@pytest.mark.asyncio
async def test_conclusion_points_to_details_only_when_attention_list_is_empty(client, db):
    """送信のスキップは緑を外すが要対応一覧には載らない → 空の表へ誘導しない。"""
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="push",
        week_start=WEEK_START,
        status="completed",
        params={"op": "apply", "week_start": WEEK_START.isoformat()},
        result_summary={},
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.flush()
    db.add_all(
        [
            KaipokeJobItem(
                job_id=job.id,
                seq=1,
                status="completed",
                content=_row_content(WEEK_START, "09:00", "山岡 太郎", "success"),
            ),
            KaipokeJobItem(
                job_id=job.id,
                seq=2,
                status="skipped",
                content=_row_content(WEEK_START, "10:00", "清水 花子", "skipped"),
            ),
        ]
    )
    await db.commit()

    res = await client.get(
        f"/api/v1/integrations/kaipoke/jobs/{job.id}/report",
        params={"verify": "false"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["skipped"] == 1
    assert body["summary"]["attention"] == 0
    assert body["conclusionTone"] == "amber"
    assert "「要対応一覧」" not in body["conclusionText"]
    assert "明細で対象を確認してください" in body["conclusionText"]


@pytest.mark.asyncio
async def test_all_rows_excluded_says_excluded_not_all_success(client, db):
    headers, admin = await _admin_headers(db)
    job = KaipokeJob(
        job_type="push",
        week_start=WEEK_START,
        status="completed",
        params={"op": "apply", "week_start": WEEK_START.isoformat()},
        result_summary={},
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.flush()
    db.add_all(
        [
            KaipokeJobItem(
                job_id=job.id,
                seq=i + 1,
                status="skipped",
                content=_row_content(
                    WEEK_START, f"{9 + i:02d}:00", f"利用者{i}", "excluded", reason="unassigned"
                ),
            )
            for i in range(2)
        ]
    )
    await db.commit()

    res = await client.get(
        f"/api/v1/integrations/kaipoke/jobs/{job.id}/report",
        params={"verify": "false"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["total"] == 0
    assert body["summary"]["excluded"] == 2
    assert body["conclusionText"] == "送信対象の行はなく、2 件は除外されました。"
    assert "すべてが成功" not in body["conclusionText"]
