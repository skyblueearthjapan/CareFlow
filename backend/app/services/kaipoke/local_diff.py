"""CareFlow 内で完結するローカル差分 (K-2b・心臓部の統合).

従来の ``/api/diff`` は kaipoke-api 側で「現況 vs 旧GAS由来の最適化CSV」を比較して
いた。K-2b では最適化CSVを CareFlow の確定 visits から生成 (csv_builder) し、差分も
CareFlow 内 (diff/engine.compare_schedules_from_content) で取る。これにより差分の
「正」が CareFlow に一本化される (設計書 kaipoke-csv-generation-design.md §3/§5)。

フロー:
  1. kaipoke から現況CSVを同期エクスポート (csv_content を取得)
  2. CareFlow visits から最適化CSVを生成 (build_month_csv)
  3. compare_schedules_from_content(現況, 最適化) → Correction リスト
     (= 現況をCareFlow確定形へ寄せるための修正 = apply でカイポケへ押す内容)

``Correction.staff1/staff2`` は最適化CSV の職員名1/2 をそのまま写した値であり、
**誰が staff2 になるかは csv_builder の配分順序 1 箇所だけで決まる**
(secondary → 同行[support優先→スタッフ名昇順→id] → mentor・一般化 決定#6)。
1 訪問に複数の同行者が居ても (決定#5) 順序が決定的なので、週次反映が実行のたびに
別人をカイポケへ押すことはない。週次は 2 枠 (staff1/staff2) までのため職員名3 相当は
Correction に載らない — これは既存制限 (設計 §9 / 一般化 §6) で本 Phase でも据え置き。
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.office import Office
from app.services.diff.engine import Correction, compare_schedules_from_content
from app.services.kaipoke.csv_builder import HEADER, BuildOptions, build_month_csv
from app.services.kaipoke.export_guard import ensure_export_ok
from app.services.kaipoke.rpa_capability import service_branch_enabled

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.kaipoke_client import KaipokeClient

# 同期 export のブロック時間 (~50s) に耐える timeout。
_SYNC_EXPORT_TIMEOUT = 90.0

# カイポケ18列CSV の「事業所名」列インデックス。
_OFFICE_COL = 8

# カイポケ18列CSV の「日付」列インデックス (値は「日」1-31 のみ・年月を持たない)。
# csv_builder.HEADER / diff/engine._parse_kaipoke_rows と同じ列位置に依存する。
_DATE_COL = 9


def _rows_to_csv(header: list[str], rows: list[list[str]]) -> str:
    """ヘッダー1行 + 明細行を CSV テキストへ (カイポケ互換の CRLF)。"""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def _keep_week_rows(
    csv_text: str, allowed_days: set[int]
) -> tuple[list[str] | None, list[list[str]]]:
    """CSVから「日付」列が ``allowed_days`` に含まれる行だけを残す (ヘッダーは別返し)。

    カイポケCSVの日付列は「日」(1-31) しか持たないため、**月をまたぐ週では
    月ごとに許可日集合で絞らないと 10/1 の週に 9/1 の行が混ざる**。
    この 1 箇所を ``export_current_week_csv`` (カイポケ側) と
    ``build_local_diff`` の らく助側生成で共有する (2026-09-11)。

    Returns:
        ``(header, kept_rows)``。空CSVなら ``(None, [])``。
    """
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        return None, []
    header, body = rows[0], rows[1:]
    kept: list[list[str]] = []
    for r in body:
        if len(r) <= _DATE_COL:
            continue
        try:
            day = int(r[_DATE_COL].strip())
        except ValueError:
            continue
        if day in allowed_days:
            kept.append(r)
    return header, kept


def _week_days(week_start: date, week_end: date) -> list[date]:
    """週の日付リスト (両端含む)。"""
    span = (week_end - week_start).days
    if span < 0:
        return [week_start]
    return [week_start + timedelta(days=i) for i in range(span + 1)]


def _months_of(days: list[date]) -> list[tuple[int, int]]:
    """日付リストが跨ぐ (year, month) を出現順で返す。"""
    months: list[tuple[int, int]] = []
    for d in days:
        key = (d.year, d.month)
        if key not in months:
            months.append(key)
    return months


def _filter_current_by_office(current_csv: str, office_name: str) -> str:
    """現況CSVを対象事業所名の行だけに絞る (ヘッダーは保持)。

    optimized 側 (build_month_csv) は office_id で絞られるため、current 側も
    同じ事業所に揃えないと、他拠点の全エントリが誤って delete 差分になる。
    """
    rows = list(csv.reader(io.StringIO(current_csv)))
    if not rows:
        return current_csv
    header, body = rows[0], rows[1:]
    kept = [r for r in body if len(r) > _OFFICE_COL and r[_OFFICE_COL].strip() == office_name]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(kept)
    return buf.getvalue()


async def build_local_diff(
    db: AsyncSession,
    *,
    month: str,
    kaipoke: KaipokeClient | None = None,
    office_id: uuid.UUID | None = None,
    week_start: date | None = None,
    week_end: date | None = None,
    direction: str = "outbound",
    credentials: dict[str, str] | None = None,
    current_csv: str | None = None,
    source_op: str = "diff-local",
) -> tuple[list[Correction], dict[str, Any]]:
    """現況(kaipoke) と 最適化(CareFlow生成) の差分を CareFlow 内で計算する。

    week_start 指定時は「週スコープ」: 現況(月まるごと)と最適化の両方を対象週の
    日(1-31)で絞ってから比較する。これにより **対象週外のカイポケ既存予定は比較
    集合から消え、delete 差分にならない** (旧GASの週次運用と同じ安全設計)。
    週外を消さないための核心はこの両側フィルタ (diff/engine が target_week_* で実施)。

    **月を跨ぐ週 (例: 2026-09-28〜10-04) は両側とも月ごとに絞ってから渡す**
    (2026-09-11 の根治)。diff/engine の週フィルタは「日」(1-31) しか見ず、
    start>end の折返しを ``day>=28 or day<=4`` と解釈するため、らく助側を
    ``month`` 1 か月ぶんだけ生成して渡すと **9/1〜9/4 の行が 10/1〜10/4 として
    比較に混ざり**、偽の edit/delete/add を生む。さらに ``current_csv`` 未指定時は
    カイポケ側に翌月分がそもそも入らず非対称になる。そこでこの関数は
      * らく助側 = 週が跨ぐ各月を ``build_month_csv`` し ``_keep_week_rows`` で絞って結合
      * カイポケ側 = ``export_current_week_csv`` (同じ絞り方で週結合)
    を行う。非跨ぎの週と月スコープの経路は従来どおり (CSVはバイト同値)。
    **副作用の注意**: 月跨ぎ週の 🔄突合 が保存するスナップショットは週スコープ
    (``week_start`` 付き) だけで、**月スコープの行は更新されない**。そのため同月の
    他の週の ●未送信 は、より古い月スコープ行を土台に計算され続ける
    (週限定CSVを月スコープに書くと、その月の他の週が丸ごと空に見えるため意図的)。

    direction:
      * "outbound" (既定) — current=カイポケ現況 / optimized=CareFlow。
        Correction は「カイポケを CareFlow 確定形へ寄せる修正」(= /apply で押す内容)。
      * "inbound" — 引数を入れ替えて比較する。Correction は「CareFlow をカイポケ現況へ
        寄せる修正」= 提供中の週にカイポケ側で入った直し込みの取り込み内容になる。
        (delete=CareFlow にだけ残っている→キャンセル扱い / add=カイポケにだけある)

    Returns ``(corrections, meta)``。同期 export は csv_content を直接返す (async=false)。

    ``current_csv`` を渡した場合 RPA には一切触れない (``kaipoke=None`` で呼べる)。
    「●未送信」(week-cockpit §2-4) はこの経路で保存済みCSVを流し込む。
    """
    year, mon = int(month[:4]), int(month[5:7])

    # 週スコープの確定を export より前に済ませる: 月跨ぎ判定が
    # カイポケ側 export の取り方 (月まるごと / 週結合) を分けるため。
    if week_start and not week_end:
        week_end = week_start + timedelta(days=6)  # 月曜起点の7日 (旧GAS getWeekRange_ 相当)
    week_days = _week_days(week_start, week_end) if week_start and week_end else []
    week_months = _months_of(week_days)
    # 月跨ぎの週 = 週の開始と終了で (年,月) が違う (例: 2026-09-28〜10-04)。
    spanning = len(week_months) > 1

    # current_csv 注入 (2026-07-26 smart-inbound): ハイブリッド取り込みは export を
    # 1回だけ実行し、その結果を差分計算と置換計画の両方に渡す。未指定なら従来どおり
    # ここで export する。
    did_export = False
    did_week_export = False
    if current_csv is None:
        if kaipoke is None:
            raise ValueError("build_local_diff requires either `kaipoke` or `current_csv`")
        if spanning:
            # 月跨ぎの週は 1 か月の export では翌月分が欠ける (= 偽 add/delete)。
            # export_current_week_csv が両月を取って週内の行だけを結合する。
            # 保存は **こちらで** 行う (``db=None``) — export_current_week_csv の
            # 保存は _filter_current_by_office より前に走るので、office_id を
            # キーに持ちながら中身は全拠点、という嘘のスナップショットになる。
            # ここで week_start=None の月スコープ保存を重ねると ●未送信 の
            # month_only スナップショット検索が週限定CSVを掴んで全滅表示になるため、
            # did_export は False のままにする (2026-09-11)。
            current_csv = await export_current_week_csv(
                kaipoke=kaipoke,
                week_start=week_start,  # type: ignore[arg-type]
                credentials=credentials,
                db=None,
                office_id=office_id,
                source_op=source_op,
            )
            did_week_export = True
        else:
            export_payload: dict[str, Any] = {"month": month, "async": False}
            if credentials:
                # アプリ内設定の認証情報 (C-1)。HTTP body のみに載せ、永続化はしない。
                export_payload["credentials"] = credentials
            resp = await kaipoke.export(export_payload, timeout=_SYNC_EXPORT_TIMEOUT)
            # export の失敗 (success=False / 本文なし) を「カイポケが空」と読み違えない。
            # 空のまま進むと、らく助の全訪問が add 差分に化けて二重登録を招き、
            # 空CSVが「最後に見た姿」として保存されて ●未送信 も全滅表示になる。
            current_csv = ensure_export_ok(resp.get("result"))
            did_export = True

    # office_id 指定時: optimized は当該拠点のみ生成されるため、current も同じ拠点に
    # 絞る (揃えないと他拠点が全て delete 差分になり非対称化する)。
    if office_id is not None:
        office = await db.scalar(select(Office).where(Office.id == office_id))
        if office is not None:
            office_name = office.kaipoke_name or office.name
            current_csv = _filter_current_by_office(current_csv, office_name)

    if did_export:
        # 「最後に見たカイポケの姿」を保存 (week-cockpit §1 D3・mig 0076)。
        # 拠点フィルタ後に保存する = 保存CSVの中身は office_id キーと一致する。
        # 月まるごとの export なので week_start は付けない (どの週にも使える)。
        from app.services.kaipoke.csv_snapshot import save_snapshot

        await save_snapshot(
            db,
            office_id=office_id,
            month=month,
            week_start=None,
            csv_text=current_csv,
            source_op=source_op,
        )
    elif did_week_export and week_start is not None:
        # 月跨ぎ週の週結合 export。保存は **拠点フィルタ後** に、**週スコープ**
        # (week_start 付き) で行う。本文行が 1 行も無ければ保存しない
        # (export_current_week_csv の ``if db is not None and merged`` と同じ規則) —
        # 空を「最後に見た姿」にすると次の未送信で全訪問が add に化ける。
        from app.services.kaipoke.csv_snapshot import count_csv_rows, save_snapshot

        if count_csv_rows(current_csv) > 0:
            await save_snapshot(
                db,
                office_id=office_id,
                month=f"{week_start.year:04d}-{week_start.month:02d}",
                week_start=week_start,
                csv_text=current_csv,
                source_op=source_op,
            )

    # include_unassigned=True: 差分計算では未割当訪問も '-' 行として比較に含める。
    # 除外すると「らく助側が空」に見えてカイポケ全行が偽 delete になる
    # (2026-08-21 C2実機テストの実障害・BuildOptions docstring 参照)。
    if spanning:
        # 月跨ぎの週: 週が触れる各月を生成し、**その月に属する週内の日**だけ残して結合。
        # 1 か月ぶんだけ渡すと diff/engine の折返しフィルタが 9/1〜9/4 を
        # 10/1〜10/4 と同一視して偽差分を作る (2026-09-11 の根治・docstring 参照)。
        merged_header: list[str] | None = None
        merged_rows: list[list[str]] = []
        for y, m in week_months:
            allowed_days = {d.day for d in week_days if (d.year, d.month) == (y, m)}
            part_bytes = await build_month_csv(
                db,
                BuildOptions(year=y, month=m, office_id=office_id, include_unassigned=True),
                encoding="utf-8-sig",
            )
            part_header, part_rows = _keep_week_rows(part_bytes.decode("utf-8-sig"), allowed_days)
            if merged_header is None and part_header is not None:
                merged_header = part_header
            merged_rows.extend(part_rows)
        optimized_csv = _rows_to_csv(merged_header or HEADER, merged_rows)
    else:
        optimized_bytes = await build_month_csv(
            db,
            BuildOptions(year=year, month=mon, office_id=office_id, include_unassigned=True),
            encoding="utf-8-sig",
        )
        optimized_csv = optimized_bytes.decode("utf-8-sig")

    # 週スコープ: 対象週の「日」(1-31) を diff/engine の週フィルタへ渡す。
    # diff/engine は current/optimized の両方をこのレンジに絞り、月境界の折返し
    # (start>end) も処理する。CSVの日付列が「日のみ」の設計なので day 比較で成立。
    # 月跨ぎの週では **両側とも既に月ごとに絞ってある** ので、この折返しフィルタは
    # 素通し (何も落とさない) になり無害 — 絞り込みの正は上の月別フィルタ側にある
    # (2026-09-11)。
    week_start_day = week_start.day if week_start else None
    week_end_day = week_end.day if week_end else None

    # inbound は current/optimized を入れ替える (「正」がカイポケ側に移った週の取り込み)。
    if direction == "inbound":
        compare_current, compare_target = optimized_csv, current_csv
    else:
        compare_current, compare_target = current_csv, optimized_csv

    corrections = compare_schedules_from_content(
        compare_current,
        compare_target,
        target_week_start=week_start_day,
        target_week_end=week_end_day,
        # 氏名の空白違い (半角/全角) を正規化して同一人物に束ねる
        # (偽の delete+add ペア防止・2026-07-26)。
        # 2026-08-21: outbound も正規化する — らく助「今井 康敦」×カイポケ
        # 「今井　康敦」が同時刻で add+delete ペアに割れる実障害 (C2実機テスト)。
        # Correction.user_name の表示は現況(カイポケ)側の原文が優先されるため、
        # RPA の利用者選択 (name_matches = 正規化包含) はそのまま成立する。
        normalize_names=True,
        # 請求区分 (正看/准看) が変わる行に印を立て、サービス内容を **らく助側の値**
        # に差し替えるのは **outbound かつ RPA がサービス内容の分岐に対応済み**
        # (S3 完了 = kaipoke_rpa_service_branch_enabled) のときだけ。
        # カイポケの編集ダイアログはサービス内容を直せないので、RPA はこの印の行を
        # 「削除 → 再追加」で処理し、そのときこの値を書く (2026-09-03 W37 で edit が
        # 6 件・請求に影響)。門が閉じている間に印を立てても RPA は既定値
        # (精神科 × 看護師等) で再追加するだけなので、従来どおりの出方に倒す。
        # inbound はらく助に訪問ごとのサービス内容が無いので印も差し替えもしない。
        flag_grade_change=(direction != "inbound" and service_branch_enabled()),
    )

    meta: dict[str, Any] = {
        "current_row_count": max(0, current_csv.count("\n") - 1),
        "optimized_row_count": max(0, optimized_csv.count("\n") - 1),
        "correction_count": len(corrections),
        "scope": "week" if week_start else "month",
        "direction": direction,
        # 比較集合がどの月にまたがっているか (月跨ぎ週の切り分け用・2026-09-11)。
        "months": [f"{y:04d}-{m:02d}" for y, m in week_months] or [month],
    }
    if week_start:
        meta["week_start"] = week_start.isoformat()
        meta["week_end"] = week_end.isoformat() if week_end else None
    return corrections, meta


async def export_current_week_csv(
    *,
    kaipoke: KaipokeClient,
    week_start: date,
    credentials: dict[str, str] | None = None,
    db: AsyncSession | None = None,
    office_id: uuid.UUID | None = None,
    source_op: str = "export-week",
) -> str:
    """対象週 (月〜日) をカバーするカイポケ現況CSVを取得する (置換取り込み用)。

    export は月単位のため、週が月を跨ぐ場合 (例: 7/27〜8/2) は両月を取得し、
    各月から「その月に属する週内の日」の行だけを残して結合する。CSV の日付列は
    「日」(1-31) のみなので、月ごとに許可日集合で絞らないと 7/1 の行が
    8/1 の週に誤混入する — その防止が本ヘルパーの存在理由。

    ``db`` を渡すと取得結果を ``kaipoke_csv_snapshots`` に保存する
    (week-cockpit §1 D3)。この CSV は対象週の行しか含まないため、保存時は
    ``week_start`` を刻んで「この週専用の現況」であることを明示する
    (別の週の未送信計算に流用すると週が丸ごと空に見える)。
    ``office_id`` は既定 None — 置換/smart 取り込みは拠点で絞らず export する
    (単一事業所運用。呼び出し側に拠点の文脈が無い) ため。
    """
    week_days = _week_days(week_start, week_start + timedelta(days=6))

    header: list[str] | None = None
    merged: list[list[str]] = []
    for y, m in _months_of(week_days):
        month = f"{y:04d}-{m:02d}"
        allowed_days = {d.day for d in week_days if (d.year, d.month) == (y, m)}
        payload: dict[str, Any] = {"month": month, "async": False}
        if credentials:
            payload["credentials"] = credentials
        resp = await kaipoke.export(payload, timeout=_SYNC_EXPORT_TIMEOUT)
        # 失敗を空CSVとして飲み込むと「対象週に予定が無い」置換計画になる (全消し)。
        content = ensure_export_ok(resp.get("result"))
        # 月ごとの許可日フィルタは build_local_diff (らく助側) と同じ 1 箇所を使う。
        part_header, part_rows = _keep_week_rows(content, allowed_days)
        if header is None and part_header is not None:
            header = part_header
        merged.extend(part_rows)

    # 両月とも空でヘッダーが拾えなかった場合は csv_builder.HEADER で補う。
    # 空ヘッダー行を返すと下流パーサ (diff/engine・count_csv_rows) が見る列数が
    # 経路によって変わるため、build_local_diff のらく助側結合と同じ既定に揃える。
    merged_csv = _rows_to_csv(header or HEADER, merged)

    if db is not None and merged:
        from app.services.kaipoke.csv_snapshot import save_snapshot

        await save_snapshot(
            db,
            office_id=office_id,
            month=f"{week_start.year:04d}-{week_start.month:02d}",
            week_start=week_start,
            csv_text=merged_csv,
            source_op=source_op,
        )

    return merged_csv


def correction_before_after(c: Correction) -> tuple[dict[str, Any], dict[str, Any]]:
    """Correction を before/after の dict へ (CorrectionSheetItem 用)。

    値は文字列が主体だが ``grade_change`` だけ bool なので ``dict[str, Any]``。
    """
    # user_name/remarks を含める: patient_id 未解決(name_match失敗)でも管理画面で
    # どの利用者の修正か特定でき、イベント系のイベント名(remarks)も保持される。
    before = {
        "user_name": c.user_name,
        "date": c.date_from,
        "start_time": c.start_time_from,
        "end_time": c.end_time_from,
        "staff1": c.staff1_from,
        "staff2": c.staff2_from,
        "service_type": c.service_type,
        "business_type": c.business_type,
        "remarks": c.remarks,
        # 請求区分 (正看/准看) が変わる行の印と、変更前 (カイポケ現況) の
        # サービス内容。DB 列を増やさずシートまで運ぶため before/after の両側に
        # 載せる (RPA へ渡す平坦形式は item_to_kaipoke_correction が組み立てる)。
        "grade_change": c.grade_change,
        "service_type_from": c.service_type_from,
    }
    after = {
        "user_name": c.user_name,
        "date": c.date_to,
        "start_time": c.start_time_to,
        "end_time": c.end_time_to,
        "staff1": c.staff1_to,
        "staff2": c.staff2_to,
        "service_type": c.service_type,
        "business_type": c.business_type,
        "remarks": c.remarks,
        "grade_change": c.grade_change,
        "service_type_from": c.service_type_from,
    }
    return before, after


# CorrectionSheetItem の before/after キー → カイポケ Correction の *_from/*_to キー。
# correction_before_after() の逆変換。apply でカイポケへ送る平坦形式を作る。
def item_to_kaipoke_correction(
    action: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> dict[str, Any]:
    """CorrectionSheetItem(before/after dict) → カイポケ /api/apply の Correction dict。

    カイポケ側は ``Correction(**item)`` で復元するため、キーは Correction dataclass の
    フィールド名 (user_name / date_from / date_to / *_from / *_to / action /
    business_type / service_type / remarks / grade_change / service_type_from) と
    厳密一致させる。

    ``grade_change`` (bool) は **請求区分 (正看/准看) が変わる行** の印で、RPA への
    経路指定でもある: カイポケの編集ダイアログはサービス内容を変更できないので、
    RPA はこの印の付いた edit / date_change を「削除 → 再追加」で処理し、再追加時に
    ``service_type`` (= らく助側の値) を書く。``service_type_from`` は変更前
    (カイポケ現況) の値で、再追加が失敗したとき RPA が元の行を復旧するのに使う。
    RPA 側の ``Correction`` は ``Correction(**item)`` で復元するため、**この 2 つの
    キーを受ける RPA を先にデプロイ** してから らく助 を出すこと (キーは値に
    関わらず常に送る = 挙動がシートの中身で変わらない)。
    """
    b = before or {}
    a = after or {}

    def pick(key: str) -> str:
        """after 優先で取得 (空なら before)。user_name/service_type 等の共通フィールド用。"""
        val = a.get(key)
        if val is not None and val != "":
            return str(val)
        return str(b.get(key) or "")

    return {
        "user_name": pick("user_name"),
        "date_from": str(b.get("date") or ""),
        "date_to": str(a.get("date") or ""),
        "start_time_from": str(b.get("start_time") or ""),
        "start_time_to": str(a.get("start_time") or ""),
        "end_time_from": str(b.get("end_time") or ""),
        "end_time_to": str(a.get("end_time") or ""),
        "staff1_from": str(b.get("staff1") or ""),
        "staff1_to": str(a.get("staff1") or ""),
        "staff2_from": str(b.get("staff2") or ""),
        "staff2_to": str(a.get("staff2") or ""),
        "service_type": pick("service_type"),
        "action": action,
        "business_type": pick("business_type"),
        "remarks": pick("remarks"),
        # RPA の経路指定 (削除→再追加)。before/after のどちらに載っていても拾う。
        "grade_change": bool(a.get("grade_change") or b.get("grade_change")),
        # 再追加に失敗したとき RPA が元の行を復旧するための現況値。
        "service_type_from": str(a.get("service_type_from") or b.get("service_type_from") or ""),
    }
