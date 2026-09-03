"""コース担当 → 訪問担当のミラー (表示の正典を visits へ伝播する 1 箇所).

設計原則 (PO 確定 2026-07-09 / memory ``careflow-staff-assignment-source``):

    * 表示の正典 = ``courses.assigned_staff_id`` (その週そのコースの担当)
    * ``visits.primary_staff_id`` はそのミラー (手動上書き ``manual_staff_override``
      を除く)

``PATCH /api/v1/courses/{id}`` (``app/api/v1/courses.py``) は担当変更のたびに
このミラーを bulk UPDATE で維持している。同じことを **コース担当を書き換える
別の経路** (週生成 / 固定枠戻し = ``reset_visits_to_fixed``) でもやるための共有
ヘルパ。

なぜ必要か (2026-09-03 W37 の実害):
    プール一括投入 (``POST /schedule/v2/pool-bulk-apply``) は患者ごとに
    ``reset_visits_to_fixed`` を呼ぶ。その中でコース担当が未割当なら
    ローテーションで担当を決め ``courses.assigned_staff_id`` へ書き戻すが、
    **そのコースに既にあった他患者の visits** は再生成対象外なので
    ``primary_staff_id`` が NULL のまま残った。盤面はコース担当を見るので
    「宇田川」と表示される一方、カイポケ月次CSV は
    ``visits.primary_staff_id`` しか見ないため 職員名1='-' (担当なし) で送信され、
    カイポケ側の担当が消えた (稲毛A 9/9 の 5 件)。

``courses.py`` との差 (意図的な絞り込み):
    ``courses.py`` は「管理者がそのコースの担当を明示的に付け替えた」操作なので
    ``manual_staff_override`` 以外の visit を **無条件で** 上書きする。一方こちらは
    自動経路 (ローテーション) なので、``primary_staff_id`` が NULL か
    **旧コース担当と同一** の visit だけを対象にする。Layer3 の個別 swap
    (``auto_allocator._swap_consecutive_visits``: コース担当は変えずに visit 単位で
    担当を差し替える) の結果を自動処理で踏み潰さないための保険。さらに、自動経路が
    作り直さないと決めている訪問 (青ピン ``week_pinned`` / 打刻済み・取消済み
    ``status``) も対象外にする — 詳細は関数の docstring。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.visit import Visit

__all__ = ["mirror_course_staff_to_visits"]

# ``auto_allocator_v2._RESET_DELETABLE_STATUSES`` と同値 (= 週生成/固定枠戻しが
# 作り直してよい status)。同モジュールから import すると循環 (auto_allocator_v2 →
# course_staff_mirror → auto_allocator_v2) になるためここで定義する。値を変える時は
# ``app/services/scheduling/auto_allocator_v2.py`` の同名定数と必ず揃えること。
_MIRRORABLE_STATUSES: tuple[str, ...] = ("planned", "proposed")


async def mirror_course_staff_to_visits(
    db: AsyncSession,
    *,
    course_id: UUID | None,
    old_staff_id: UUID | None,
    new_staff_id: UUID | None,
) -> int:
    """コース担当の変更を、そのコースの既存 visits へ伝播する (bulk UPDATE)。

    Args:
        course_id: 対象コース。None は no-op (条件が ``course_id IS NULL`` に化けて
            コース無しの訪問を全件書き換えてしまうため明示的に弾く)。
        old_staff_id: 変更前の ``courses.assigned_staff_id`` (未割当なら None)。
        new_staff_id: 変更後の担当。None (未割当化) と 変更なし は no-op。

    Returns:
        更新した visit 行数 (0 なら何も書いていない)。

    対象行 (すべて AND):
        * ``course_id`` 一致 / ``deleted_at IS NULL``
        * ``manual_staff_override IS FALSE`` … 手動で担当を変えた訪問は尊重する
        * ``primary_staff_id`` が NULL または ``old_staff_id`` と一致 … 個別 swap の結果を残す
        * ``week_pinned IS FALSE`` … 青ピン (今週この位置) は自動処理で触らない
        * ``status IN ('planned', 'proposed')`` … 打刻済み (completed / in_progress) や
          取消済みの訪問は書き換えない

    後ろ 2 つは **自動経路 (週生成・固定枠戻し) が visit を作り直す条件と同じ** に
    そろえてある。これが無いと「退職者がコース担当のまま月・火は打刻済み → 水曜に
    プール一括投入でコース担当がローテーションで変わる → 実績付きの月・火の担当まで
    書き換わり、カイポケ週次差分が実績行に編集を出す」という事故になる。
    ``courses.py`` の PATCH (管理者が明示的に担当を付け替える操作) はこの絞り込みを
    持たないが、あちらはこのヘルパを呼んでいないので影響しない。

    VSA (``visit_staff_assignments`` = 可視性の正典) は **意図的に書かない**:
    ``courses.py`` の担当変更ミラーと同じ選択で、VSA は連携週スケジュール
    (コース担当優先) と layer3 再実行で吸収される。ここで触ると 2 名体制の相方まで
    巻き込むため。commit / flush も呼び出し側に任せる。
    """
    if course_id is None or new_staff_id is None or new_staff_id == old_staff_id:
        return 0

    staff_cond = Visit.primary_staff_id.is_(None)
    if old_staff_id is not None:
        staff_cond = or_(staff_cond, Visit.primary_staff_id == old_staff_id)

    result = await db.execute(
        sa_update(Visit)
        .where(
            Visit.course_id == course_id,
            Visit.deleted_at.is_(None),
            Visit.manual_staff_override.is_(False),
            Visit.week_pinned.is_(False),
            Visit.status.in_(_MIRRORABLE_STATUSES),
            staff_cond,
        )
        .values(primary_staff_id=new_staff_id)
    )
    return result.rowcount or 0
