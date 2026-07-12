"""Layer3Assigner — W4-BE9.

設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.4 / §5.4 / §3.6.7
と API 契約 ``docs/plans/v2-api-contracts.md`` §4.6
(`POST /api/v1/courses/assign-staff`) に対応する Layer 3 アルゴリズム。

責務 (§5.4):
    - 確定済みコース (course_status='course_fixed') を読み出し
    - 当該週の稼働スタッフリストとマッチング (ハンガリアン法 + ローテーション)
    - 結果を courses.assigned_staff_id に書き込み course_status='staff_assigned' に遷移

ハード制約 (§3.6.4):
    - 性別: 患者の sex_restriction を満たすスタッフ
    - 勤務日: 当該曜日にスタッフ勤務 (StaffShift.is_on=True)
    - 単一性: 1 スタッフは 1 日 1 コース
    - 役割: マネージャー (M1) は対象外 (role='manager' を除外)

MVP 前提 (Q3=ハイブリッド, §5.4 / 受入基準):
    - 直近 1 週の担当者 (= staff × course の組み合わせ) は強制除外
    - それ以前 N 週分はソフトペナルティ (rotation_penalty)

コスト関数 (§5.4):
    cost(weekday, course, staff) =
        α × distance_score(staff の主拠点 → コース重心)
      + β × rotation_penalty(staff × course の最近 N 週の担当回数)
      + γ × gender_mismatch_penalty(該当があれば INF)
      + δ × work_day_violation(勤務曜日違反は INF)

アルゴリズム:
    - ハンガリアン法 (二部マッチング、O(n^3))
    - 規模が小さい (~24 patients × 6 staff × 6 weekdays) ので自前 Hungarian 実装
      (scipy 依存を避ける)。各曜日ごとに独立に解く。
    - 1 曜日あたり O(max(C, S)^3); 全曜日合算 O(W × max(C, S)^3) (§5.4)

トランザクション:
    本サービスは ``db.commit()`` / ``db.rollback()`` を **呼ばない**。
    呼び出し側 (``app/api/v1/courses.py``) が 1 HTTP リクエストを 1
    トランザクションとして包む。
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls
from datetime import time as time_cls
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import (
    COURSE_STATUS_COURSE_FIXED,
    COURSE_STATUS_STAFF_ASSIGNED,
    Course,
)
from app.models.office import Office
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.patient import Patient
from app.models.staff import (
    Staff,
    StaffEvent,
    StaffSecondaryOffice,
    StaffShift,
    StaffWeeklyOverride,
)
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.scheduling.constants import DEFAULT_OFFICE_OPERATING_WEEKDAYS
from app.services.scheduling.layer2_clustering import haversine_km

# Phase G-45: 拠点稼働曜日デフォルト (= 月-土).
# Phase G-88: 正準値は ``constants`` に単一ソース化. 既存ローカル名は別名で維持.
_DEFAULT_OFFICE_OPERATING_WEEKDAYS: frozenset[int] = DEFAULT_OFFICE_OPERATING_WEEKDAYS

# Wave N-1: primary staff 固定割当 (旧「都賀」拠点名ハードコードの設定化) の
# OfficeFeatureFlag key. enabled_at IS NOT NULL の拠点で A コースへの primary staff
# (active role=staff の code 昇順先頭 1 名) 固定割当が発動する.
L3_FIX_PRIMARY_STAFF_FEATURE_KEY: str = "l3_fix_primary_staff"


def _coerce_office_operating_weekdays(raw: object) -> set[int]:
    """``Office.operating_weekdays`` (JSONB) を ``set[int]`` (0..6) に正規化.

    不正値 (= list でない / 要素が int でない / 範囲外) はデフォルト (= 月-土) に
    フォールバック. Layer 3 / auto_allocator_v2 で同じ正規化ロジックを共有する.
    """
    if not isinstance(raw, list) or not raw:
        return set(_DEFAULT_OFFICE_OPERATING_WEEKDAYS)
    out: set[int] = set()
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, int):
            return set(_DEFAULT_OFFICE_OPERATING_WEEKDAYS)
        if v < 0 or v > 6:
            return set(_DEFAULT_OFFICE_OPERATING_WEEKDAYS)
        out.add(v)
    if not out:
        return set(_DEFAULT_OFFICE_OPERATING_WEEKDAYS)
    return out


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — cost function weights (§5.4)
# ---------------------------------------------------------------------------

# Phase G-90: α (距離スコアの係数) は撤去した. スタッフは自拠点コースにのみ行く
# (拠点ハード制約) ため、 同拠点内では距離 (km) は割付に無関係.
# ``DISTANCE_UNKNOWN_KM`` / ``_distance_km`` は total_distance_km レポート用に残す.

# Phase 3: 座標欠損時の代替距離 (km).
# 根拠: 旧実装は座標 (course 重心 or staff 主拠点) が None のとき 0.0 を返し、
# 「最良 (= 最短距離)」 扱いになる逆インセンティブがあった (= 座標が無い staff/course
# ほど距離項で有利になり優先選択されてしまう). 12.0 km は当サービスの稼働圏 (千葉市内
# 拠点 → 患者宅) の典型的な中庸距離 (= 短すぎず長すぎない) を採用し、 座標既知の
# 近距離ペア (数 km) より不利、 遠距離ペア (20km+) より有利な中立値とする.
DISTANCE_UNKNOWN_KM: float = 12.0

# β: ローテーションペナルティの係数 — 最近 N 週で同一 course を担当した回数
#    1 回担当 = 5.0 のペナルティ (距離換算で 5 km 相当)
COST_BETA_ROTATION: float = 5.0

# γ: 性別ミスマッチ — INF 相当 (ハード制約)
COST_GAMMA_GENDER_MISMATCH: float = math.inf

# δ: 勤務曜日違反 — INF 相当 (ハード制約)
COST_DELTA_WORK_DAY: float = math.inf

# ローテーション履歴で参照する週数 (直近 N 週)
ROTATION_HISTORY_WEEKS: int = 4

# Q3 ハイブリッド: 直近 EXCLUSION_WEEKS 週の担当者は強制除外
ROTATION_EXCLUSION_WEEKS: int = 1

# W16: 前日と同じコースを同一スタッフが担当した場合のペナルティ
# 距離 km 換算で十分大きい (=実用上ほぼ強制回避) ようにする。
# ハード INF にしないのは「他に勤務可能なスタッフが居ない」場合の救済のため。
COST_W16_PREV_DAY_SAME_COURSE: float = 100.0

# ハンガリアン法でダミー行/列に使う「実質的に無限大」のコスト。
# math.inf は加算で扱いにくいので有限大の値を使う。
HUNGARIAN_INFINITY: float = 1.0e12

# 1 件 = 大きな整数化のための倍率 (Hungarian 内部で int 化したいときに使う)
# 浮動小数のまま処理するので未使用。残しておくと後々 numpy 化のヒントになる。
COST_SCALE: int = 10_000

# W33: 移動・準備のためのバッファ (分)。event 終了後 / visit 開始前に
# BUFFER_MINUTES 分未満の余裕しかない場合はハード除外する。
# 運用要件に応じて変更可 (例: 10 分 / 30 分)。
BUFFER_MINUTES: int = 15  # 移動・準備の余裕 (Wave 33)

# ---------------------------------------------------------------------------
# Phase 2: patient 中心ローテーション (= 同じ患者に毎回同じ staff を避ける)
# ---------------------------------------------------------------------------
# 設計: 旧 Wave5 の「前日 / 前々日 / 前週金土」継ぎ接ぎ (穴: 3日差・週またぎ同曜日)
# を廃止し、 患者ごとの「直近 N 回の担当者リスト」 を週・曜日横断で一元管理する.
# 段階的ペナルティで「避けられる限り必ず避ける」が「適任者ゼロなら埋める」を担保.

# 履歴を遡る週数 (= patient ごとの過去担当者を何週分集めるか).
PATIENT_ROTATION_LOOKBACK_WEEKS: int = 4

# 避ける「直近担当者」の人数 (= リストの最大長). 分散範囲 = 直近 2〜3 回分.
PATIENT_RECENT_DEPTH: int = 3

# 段階ペナルティ (= 直近 1〜3 回前の担当者を避ける重み). いずれも
# HUNGARIAN_INFINITY(1e12) 未満 = 「適任者ゼロなら埋める」段階的緩和を担保.
# distance(α=1.0 × 〜30km)や random(max10)より桁違いに大きく、 避けられる限り必ず避ける.
COST_PATIENT_RECENT_1: float = 1.0e6  # 1 回前 (= 最も最近の担当者)
COST_PATIENT_RECENT_2: float = 5.0e5  # 2 回前
COST_PATIENT_RECENT_3: float = 2.0e5  # 3 回前

# index -> penalty のマップ (= working_recent list の index に対応). DEPTH と整合.
_PATIENT_RECENT_PENALTY_BY_INDEX: dict[int, float] = {
    0: COST_PATIENT_RECENT_1,
    1: COST_PATIENT_RECENT_2,
    2: COST_PATIENT_RECENT_3,
}

# 決定的ランダム加算の上限値. cost に [0.0, COST_W5_ROTATION_MAX) を加算して
# ローテーション (= 同じ patient に常に同じ staff が当たる現象) の補助タイブレーク
# とする. 距離 km 換算で 0-10 km 程度の振れ幅. (Phase 2 でも補助として残す.)
COST_W5_ROTATION_MAX: float = 10.0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class Layer3AssignmentError(Exception):
    """サービス層から HTTP 層へ伝播する業務エラー (4xx に翻訳される)."""

    def __init__(self, message: str, *, http_status: int = 422) -> None:
        super().__init__(message)
        self.http_status = http_status


# ---------------------------------------------------------------------------
# Input / Output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class StaffInfo:
    """ハンガリアン法に渡すスタッフ情報."""

    staff_id: UUID
    name: str
    sex: str | None  # "male" / "female" / None
    role: str  # "admin" / "manager" / "staff"
    primary_office_lat: float | None
    primary_office_lng: float | None
    work_days: frozenset[int]  # 0=Mon..6=Sun
    is_trainee: bool = False  # W10-BE2: 新人フラグ
    # Phase G-45: 主拠点 ID. 拠点稼働曜日判定で primary が休業のときに
    # secondary に転入するために必要 (= load_active_staff が埋める).
    primary_office_id: UUID | None = None
    # Phase G-45: 全 secondary_office_id (挿入順保持). primary が休業の weekday
    # では 先頭の稼働 secondary を主拠点として扱う.
    secondary_office_ids: tuple[UUID, ...] = ()
    # Phase G-45: 当 staff が属する office (primary + secondary) の稼働曜日 map.
    # ``effective_office_for_weekday`` が primary 休業 → secondary 転入を判定する.
    office_operating_weekdays: dict[UUID, frozenset[int]] = field(default_factory=dict)

    def effective_office_for_weekday(self, weekday: int) -> UUID | None:
        """Phase G-45: 当該 weekday の effective 主拠点 (= 稼働日かどうかで切替).

        - primary_office が当該 weekday に稼働なら primary を返す.
        - primary 休業 + secondary がある場合、 最初に稼働している secondary を返す.
        - どの office も稼働しない場合は None (= 当該 weekday は除外対象).

        ``office_operating_weekdays`` map が空 (= G-45 未連携 / 後方互換) の
        場合は primary_office_id をそのまま返す.

        **適用範囲 (= scope) の明示**:

        本 API は Phase G-45 で ``count_active_staff_per_weekday`` (= H6 制約 /
        応援 staff_count 算入) 用に導入されたが、 **Phase G-90 以降は拠点ハード
        制約の唯一の権威** でもある: ``_cost_single_cell`` / manager fallback
        (``_try_fallback_manager_for_course``) / 固定割当防御 (``_solve_one_day``
        の c2) の 3 経路すべてが本 API を呼び、 ``effective_office_for_weekday
        (course.weekday) != course.office_id`` のセルを ``HUNGARIAN_INFINITY`` で
        除外する (= スタッフは自分の実効拠点のコースにしか割り当たらない).

        なお ``_distance_km`` は Phase G-90 でコスト関数から撤去され、 現在は
        ``total_distance_km`` レポート用にのみ残る (= 割付の最適化対象ではない).
        レポート距離は応援時も ``primary_office_lat/lng`` ベースの近似値である点に注意.
        """
        if not self.office_operating_weekdays:
            return self.primary_office_id
        if self.primary_office_id is not None:
            op = self.office_operating_weekdays.get(self.primary_office_id)
            if op is None or weekday in op:
                return self.primary_office_id
        for sec_oid in self.secondary_office_ids:
            op = self.office_operating_weekdays.get(sec_oid)
            if op is None or weekday in op:
                return sec_oid
        return None


@dataclass(frozen=True)
class VisitTimeSlot:
    """W27 — Layer 3 で event 重複判定に使う visit 時間帯."""

    start_time: time_cls
    end_time: time_cls


@dataclass
class CourseAssignmentTarget:
    """1 つの (weekday × course) を 1 行と見立てた assignment 対象."""

    course_id: UUID
    weekday: int
    course_code: str
    centroid_lat: float | None
    centroid_lng: float | None
    # 患者の性別制限の集合 (例: {"female"} は女性スタッフのみ受け入れ可)
    gender_restrictions: frozenset[str]
    patient_ids: list[UUID] = field(default_factory=list)
    # W27: コース所属 visit の時間帯 (start_time, end_time) の一覧.
    # StaffEvent との重複判定に用いる. 空リストのときは event 除外を skip.
    visits: list[VisitTimeSlot] = field(default_factory=list)
    # Phase G-90: コースの所属拠点 ID (= 拠点ハード制約の判定に使う).
    # ``_load_course_targets`` が ``Course.office_id`` を populate する.
    # ``None`` (= 合成テスト fixture / 後方互換) のときは拠点ハード制約を skip する.
    office_id: UUID | None = None


class StaffAssignment(BaseModel):
    """API レスポンスの 1 件 (`assignments[]` の要素)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    course_code: str
    course_id: UUID
    staff_id: UUID


@dataclass(frozen=True)
class RotationConflict:
    """Phase G-89: ローテーション衝突 (= 患者の担当が直近担当者リストに入っていた).

    人手不足で「同じ人を避ける」 を維持できず、 患者の直近担当者を再度割り当てざるを
    得なかったケースを 1 件 1 record で記録する。

    Attributes:
        course_id: 衝突が起きたコースの ID.
        weekday: 0=Mon..6=Sun.
        patient_id: 連続担当になってしまった患者の ID.
        staff_id: 割り当てられた (= 直近担当者だった) スタッフの ID.
        recent_index: 当該患者の ``working_recent`` リスト内での staff_id の index.
            0 = 1 つ前と同じ (= 連続), 1 = 2 個前と同じ (前回は別の人), 2 = 3 個前.
            cost 関数が penalty 判定に使ったのと同じ index を記録する
            (= ``_PATIENT_RECENT_PENALTY_BY_INDEX`` のキーと一致).
    """

    course_id: UUID
    weekday: int
    patient_id: UUID
    staff_id: UUID
    recent_index: int


@dataclass(frozen=True)
class ReviewVisit:
    """Phase G-91: レビューカード内の 1 visit (= コース所属の 1 患者訪問).

    確認レビューフロー (= 自動スタッフ割付の問題コードを管理者が最終判断する)
    のカード描画に必要な visit 単位のメタ.

    Attributes:
        patient_id: 患者 ID.
        patient_name: 患者名 (表示用).
        start_time: 訪問開始時刻.
        sex_restriction: 患者の性別制限 ('female_only'/'male_only'/None).
        is_cause: 当該 review item の原因となった患者なら True.
            reason='consecutive' のとき = 連続 (= 候補スタッフが直近担当者) になる患者.
            reason='gender' のとき = 性別制限を持つ (= 性別 NG の原因) 患者.
    """

    patient_id: UUID
    patient_name: str
    start_time: time_cls
    sex_restriction: str | None
    is_cause: bool


@dataclass(frozen=True)
class ReviewItem:
    """Phase G-91: 確認レビューフローの 1 カード (= レビュー対象の 1 コース).

    「自動スタッフ割付」 を実行したとき、 クリーンなコースは自動確定 (commit) する
    が、 以下 2 種のコースは自動では割り付けず (= DB 未割当のまま)、 管理者が
    レビューして最終判断する対象として返す:

    - reason='consecutive' (🟡 連続/軽度): 患者の直近担当者 (recent_index==0)
      と同じスタッフになるケース. candidate_staff は本来割り当たるはずだった
      (= solve が選んだ) スタッフ.
    - reason='gender' (🔴 性別/重度): コースに性別制限患者が居て、 適合性別の
      同拠点スタッフが居ないケース. candidate_staff は性別制約を無視したら
      割り当たる候補スタッフ (= 管理者が override 判断する材料).

    Attributes:
        course_id: レビュー対象コースの ID.
        office_name: コース所属拠点名 (表示用).
        course_code: コースコード (A/B/C/D/E/M).
        weekday: 0=Mon..6=Sun.
        reason: 'consecutive' (連続) または 'gender' (性別).
        candidate_staff_id: 候補スタッフ ID (= apply 時に割り当てる staff).
        candidate_staff_name: 候補スタッフ名 (表示用).
        candidate_staff_sex: 候補スタッフ性別 ('male'/'female'/None).
        visits: コース所属 visit 一覧 (原因患者に is_cause フラグ).
        linked_course_ids: 2 名体制で連動する partner course_id 一覧 (修正4).
            連続コース X とその visit_group partner Y は片方だけ apply すると
            half-assigned になるため、 apply 時に必ず同じ ``_persist`` 呼出へ
            一緒に渡す必要がある course を表す. 単独コースは空.
    """

    course_id: UUID
    office_name: str
    course_code: str
    weekday: int
    reason: str  # 'consecutive' | 'gender'
    candidate_staff_id: UUID
    candidate_staff_name: str
    candidate_staff_sex: str | None
    visits: list[ReviewVisit] = field(default_factory=list)
    linked_course_ids: list[UUID] = field(default_factory=list)


@dataclass(frozen=True)
class AutoCommittedNotice:
    """Wave N-1: 不可避連続の「お知らせ」(= レビュー無しで自動確定した連続コース).

    設計書 R-2: ``rotation_conflicts`` の recent_index==0 連続のうち、 代替候補
    (ハード制約 OK・当曜日未割当・原因患者の直近担当者でない・現候補本人以外) が
    1 名も存在しない = 体制上避けられない連続を、 レビュー (承認ゲート) に出さず
    クリーンコースと同一経路で自動確定したことを管理者へ伝える情報提示.

    Attributes:
        course_id: 自動確定した連続コースの ID.
        course_code: コースコード (A/B/C/D/E/M).
        weekday: 0=Mon..6=Sun.
        office_name: コース所属拠点名 (表示用).
        staff_name: 割り当てたスタッフ名 (= 連続担当になった本人).
        cause_patient_names: 連続になる原因患者名の一覧 (表示用).
        reason_kind: 'single_staff' (適格者が実質 1 名) |
            'all_recent' (適格者複数だが全員が原因患者の直近担当者).
        reason_text: BE で組み立てた日本語の理由文 (FE はそのまま表示).
    """

    course_id: UUID
    course_code: str
    weekday: int
    office_name: str
    staff_name: str
    cause_patient_names: list[str]
    reason_kind: str  # 'single_staff' | 'all_recent'
    reason_text: str


@dataclass(frozen=True)
class UnresolvedGenderWarning:
    """W-11: 性別制約を満たす候補ゼロで自動解消できなかった残留違反の警告.

    性別ブロックで未割当になったコースについて、 ``_compute_gender_candidate_for_course``
    が候補 (性別を無視した override 候補) を 1 名も返せない (= 純粋人手不足) 場合、
    従来は review にも notice にも出さず黙って continue していた。 その結果、 過去の
    自動割付で残った **性別制約違反の assigned_staff_id** が誰にも気づかれないまま
    「割当済み」に見え続ける問題があった (PO 報告 W-11 原因B)。

    本警告は「黙って消さない」原則のもと、 自動クリアはせず **可視化のみ** を行う:
    現在の担当が性別制約を満たしていないことを管理者に伝え、 手動調整を促す。
    ``review_items`` (承認可) とも ``auto_committed_notices`` (確定済みお知らせ) とも
    別枠の「未解決警告」として API レスポンスの ``unresolved_warnings`` に載せる。

    Attributes:
        course_id: 残留違反が残っているコースの ID.
        course_code: コースコード (A/B/C/D/E/M).
        weekday: 0=Mon..6=Sun.
        office_name: コース所属拠点名 (表示用).
        current_staff_name: 現在割り当てられている (性別制約違反の) スタッフ名.
        reason_text: BE で組み立てた日本語の理由文 (FE はそのまま表示).
    """

    course_id: UUID
    course_code: str
    weekday: int
    office_name: str
    current_staff_name: str
    reason_text: str


@dataclass
class Layer3Result:
    """Layer 3 の総合出力."""

    assignments: list[StaffAssignment] = field(default_factory=list)
    rotation_score: float = 0.0  # ローテ分散度 (低いほど分散している)
    total_distance_km: float = 0.0
    # Phase G-89: 人手不足で直近担当者を再割り当てせざるを得なかった衝突一覧.
    # solve() の曜日ループ内で「割当確定時点の working_recent (= cost 関数が
    # 参照した状態)」に対して検出する. 既存呼出は default の空 list で互換維持.
    rotation_conflicts: list[RotationConflict] = field(default_factory=list)
    # Phase G-89: visits>0 なのに担当が 1 人も確保できなかった (= 未割当) コースの
    # course_id 一覧. ``assign()`` が course_targets と assignments を突き合わせて
    # 埋める (solve() 単体では空のまま). 既存呼出は default の空 list で互換維持.
    unassigned_course_ids: list[UUID] = field(default_factory=list)
    # Phase G-91: 確認レビューフローのカード一覧 (= 連続 index0 / 性別ブロック).
    # ``assign()`` が solve() 結果 + DB 読みで構築する (solve() 単体では空のまま).
    # 既存呼出は default の空 list で互換維持.
    review_items: list[ReviewItem] = field(default_factory=list)
    # Phase G-91 (修正2): 実際に ``_persist`` で commit した course_id 一覧.
    # = assignments から「連続 index0 コース + その visit_group partner (clean 含む)」
    # を除外したもの. ``courses_assigned`` の commit 実数算出に使う (= partner が
    # clean で未 commit なのに確定カウントされる過大計上を防ぐ).
    # ``assign()`` のみが埋める (solve() 単体では空のまま). 既存呼出は default 空 list.
    committed_course_ids: list[UUID] = field(default_factory=list)
    # Wave N-1: 不可避連続の「お知らせ」(= 代替候補 0 名でレビュー無し自動確定した連続).
    # ``_build_review_items`` が連続 index0 コースを avoidable / unavoidable に分岐し、
    # unavoidable を本 list に積む (= review_items には出さず committed に残す).
    # 既存呼出は default の空 list で互換維持.
    auto_committed_notices: list[AutoCommittedNotice] = field(default_factory=list)
    # W-11: 性別制約を満たす候補ゼロで自動解消できなかった残留違反の警告一覧.
    # ``_build_review_items`` が「性別ブロック未割当 + 候補なし + 現担当が性別制約違反」
    # のコースを検出して積む (= review にも notice にも出さず、 可視化のみ・自動クリアなし).
    # 既存呼出は default の空 list で互換維持.
    unresolved_warnings: list[UnresolvedGenderWarning] = field(default_factory=list)


# ---------------------------------------------------------------------------
# W27 Phase A: StaffEvent overlap helpers
# ---------------------------------------------------------------------------


def _strip_tz(dt: datetime) -> datetime:
    """tz-aware と naive の datetime を一律 naive (壁時計) に揃える.

    ``StaffEvent.starts_at`` / ``ends_at`` は DB 上 ``DateTime(timezone=True)``
    だが、 ``app.api.v1.staff_events._combine`` は naive を INSERT する。
    SQLAlchemy + SQLite では naive がそのまま戻るが、PostgreSQL 経由では
    UTC tz が付与されるため、両方に対応するため tz を剥がして比較する。
    visit 側は ``datetime.combine(visit_date, time)`` で naive として作るので
    比較相手も naive に揃えた方が安全 (= naive 同士なら DB 種別に依らず
    壁時計比較で一致する).
    """
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _normalize_sex_restriction(restriction: str) -> str:
    """patients.sex_restriction の値 ('female_only'/'male_only') を
    staff.sex の値 ('female'/'male') と比較可能な形に正規化する.

    Phase G-27 fix: DB 上は sex_restriction='female_only' / staff.sex='female' と
    suffix が異なるため、 直接比較すると全 staff INF になる cost matrix bug の
    対策. cost 計算前にここで suffix を剥がす.
    """
    if restriction.endswith("_only"):
        return restriction[: -len("_only")]
    return restriction


def _sex_satisfies_restrictions(sex: str | None, restrictions: frozenset[str]) -> bool:
    """``sex`` が ``restrictions`` (例: {'female_only'}) を満たすなら True.

    Phase 1: 性別ハード制約判定のプリミティブ (= 単一ソース). ``StaffInfo`` を
    持たない経路 (= ``_build_fixed_assignments`` の生 ``Staff`` ORM) でも同一
    セマンティクスを共有するため、 ``sex`` と ``restrictions`` の組で受ける.

    セマンティクスは AND (= 全 restriction を満たす):
    - ``restrictions`` が空 → 制限なし → True.
    - 非空 → ``sex is None`` なら False (= 性別不明は割当不可).
    - 各 restriction について ``_normalize_sex_restriction(restriction) != sex``
      が一つでもあれば False (= 'female_only' + 'male_only' の複合制限では
      どの staff も満たせない → 誤割当を防ぐ AND semantics).
    """
    if not restrictions:
        return True
    if sex is None:
        return False
    for restriction in restrictions:
        if _normalize_sex_restriction(restriction) != sex:
            return False
    return True


def _staff_satisfies_gender(staff: StaffInfo, course: CourseAssignmentTarget) -> bool:
    """staff がコースの性別ハード制約を満たすなら True (= 割当可能).

    既存の ``_cost_single_cell`` (γ ブロック) / ``_try_fallback_manager_for_course``
    の性別判定ロジックの **単一ソース** (``_sex_satisfies_restrictions`` に委譲).
    ``gender_restrictions`` の値 ('female_only'/'male_only') は
    ``_normalize_sex_restriction`` で staff.sex の形式 ('female'/'male') に
    正規化してから比較する (Phase G-27 fix と整合).

    Phase 1 (固定割当の性別穴塞ぎ): 通常ルート (cost) / manager fallback /
    固定割当の 3 経路で同一ロジックを共有し、 固定割当ルートの性別未チェックを塞ぐ.
    """
    return _sex_satisfies_restrictions(staff.sex, course.gender_restrictions)


def _has_event_overlap(
    *,
    staff_id: UUID,
    course: CourseAssignmentTarget,
    weekday: int,
    events_by_staff: dict[UUID, list[StaffEvent]],
    week_monday: date_cls,
) -> bool:
    """``staff`` の event がそのコースの visits 時間帯と 1 件でも重なるなら True.

    重なり判定 (半開区間): ``event.starts_at < visit_end AND event.ends_at > visit_start``
    """
    events = events_by_staff.get(staff_id)
    if not events:
        return False
    if not course.visits:
        return False
    target_date = week_monday + timedelta(days=weekday)
    for visit in course.visits:
        visit_start_dt = datetime.combine(target_date, visit.start_time)
        visit_end_dt = datetime.combine(target_date, visit.end_time)
        for event in events:
            ev_start = _strip_tz(event.starts_at)
            ev_end = _strip_tz(event.ends_at)
            if ev_start < visit_end_dt and ev_end > visit_start_dt:
                return True
    return False


def _has_event_overlap_with_buffer(
    *,
    staff_id: UUID,
    course: CourseAssignmentTarget,
    weekday: int,
    events_by_staff: dict[UUID, list[StaffEvent]],
    week_monday: date_cls,
) -> bool:
    """``staff`` の event が visit 時間帯と **バッファ込みで** 重なるなら True.

    W33 追加: ``BUFFER_MINUTES`` 分のバッファを event 両端に付与した拡張区間と
    visit 時間帯との重複を判定する。

    例: event 14:00-14:30, visit 14:45 開始
        → event_end_buffered = 14:30 + 15 分 = 14:45
        → 半開区間で 14:45 > 14:45 は False → ギリギリ OK (= 16 分以上の余裕なし)
        ※ visit 14:46 開始ならセーフ (event_end_buffered 14:45 < 14:46)

    「15:00-15:30 event + 15:30 visit」のような詰め込みを防止する。

    重なり判定 (半開区間、バッファ込み):
        ``event_start_buffered < visit_end AND event_end_buffered > visit_start``
    """
    events = events_by_staff.get(staff_id)
    if not events:
        return False
    if not course.visits:
        return False
    target_date = week_monday + timedelta(days=weekday)
    buf = timedelta(minutes=BUFFER_MINUTES)
    for visit in course.visits:
        visit_start_dt = datetime.combine(target_date, visit.start_time)
        visit_end_dt = datetime.combine(target_date, visit.end_time)
        for event in events:
            ev_start = _strip_tz(event.starts_at)
            ev_end = _strip_tz(event.ends_at)
            ev_start_buffered = ev_start - buf
            ev_end_buffered = ev_end + buf
            if ev_start_buffered < visit_end_dt and ev_end_buffered > visit_start_dt:
                return True
    return False


# ---------------------------------------------------------------------------
# Wave 5: deterministic randomness for rotation scoring
# ---------------------------------------------------------------------------


def _deterministic_random(
    *,
    iso_year: int,
    iso_week: int,
    patient_id: UUID | None,
    staff_id: UUID,
) -> float:
    """``[0.0, COST_W5_ROTATION_MAX)`` の決定的擬似乱数を返す.

    Wave 5: 同じ patient に常に同じ staff が当たる現象 (= ローテーション固定化) を
    避けるため、cost 行列に小さなランダム加算を入れる。完全乱数だと再現性がなく
    テストできないので、``(iso_year, iso_week, patient_id, staff_id)`` を seed と
    した SHA-256 ベースの decision を採用する.

    - 同じ (year, week, patient, staff) なら何度呼んでも同じ値
    - 週が変われば異なる値 → 週次でローテーションが回る
    - patient × staff の組合せでも一意な値 → ペア固有の偏りが出ない

    ``patient_id`` が None の場合は (例えば fixture / 空コース) staff_id のみで計算する.
    """
    parts: list[str] = [str(iso_year), str(iso_week), str(staff_id)]
    if patient_id is not None:
        parts.append(str(patient_id))
    seed = "|".join(parts).encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    # 上位 8 byte を 64bit 整数化 → [0, 1) の小数 → COST_W5_ROTATION_MAX 倍
    value = int.from_bytes(digest[:8], "big")
    fraction = value / float(1 << 64)
    return fraction * COST_W5_ROTATION_MAX


# ---------------------------------------------------------------------------
# Hungarian algorithm (square matrix)
# ---------------------------------------------------------------------------


def hungarian_min_cost(cost: list[list[float]]) -> list[int]:
    """正方行列 ``cost`` に対する最小コストの割当を返す.

    入力は n×n の浮動小数行列で、``cost[i][j]`` は行 i を列 j に割り当てたときの
    コスト。返り値は長さ n の int 配列で、``result[i] = j`` なら行 i に列 j を
    割り当てる。

    実装: O(n^3) の Jonker–Volgenant 形式 (= Kuhn-Munkres の標準実装).

    用途: 規模が小さい (n <= 10) ので自前実装で十分。scipy 依存を避ける。

    禁止セルは ``HUNGARIAN_INFINITY`` で表現する。math.inf は使わない
    (内部で双対変数を引き算するときに NaN になり得るため)。
    """
    n = len(cost)
    if n == 0:
        return []
    for row in cost:
        if len(row) != n:
            raise ValueError(f"cost matrix is not square: row len {len(row)} != n {n}")

    # u[0..n], v[0..n], p[0..n]: 1-based の補助配列 (慣例).
    inf_sentinel = HUNGARIAN_INFINITY * 10  # ループ内のセンチネル
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf_sentinel] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf_sentinel
            j1 = 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        # augment
        while j0 != 0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    # p[j] = i  →  row (i-1) is assigned to col (j-1)
    assignment = [-1] * n
    for j in range(1, n + 1):
        i = p[j]
        if i != 0:
            assignment[i - 1] = j - 1
    return assignment


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class Layer3Assigner:
    """Layer 3: スタッフ割付 (ハンガリアン法 + ローテーション)."""

    async def assign(
        self,
        db: AsyncSession,
        *,
        iso_year: int,
        iso_week: int,
        office_id: UUID | None = None,
    ) -> Layer3Result:
        """指定週の確定済みコースに対しスタッフを割り付ける.

        Args:
            db: 共有 SQLAlchemy セッション.
            iso_year: ISO 年.
            iso_week: ISO 週 (1-53).
            office_id: W16 — 対象拠点 (None=全拠点合算).

        Returns:
            Layer3Result: 割当結果 + ローテーションスコア + 総距離.

        Raises:
            Layer3AssignmentError: 入力範囲外などの業務エラー.
        """
        if iso_year < 2000 or iso_year > 2100:
            raise Layer3AssignmentError(f"iso_year out of range: {iso_year}")
        if iso_week < 1 or iso_week > 53:
            raise Layer3AssignmentError(f"iso_week out of range: {iso_week}")
        try:
            week_monday = date_cls.fromisocalendar(iso_year, iso_week, 1)
        except ValueError as exc:
            raise Layer3AssignmentError(
                f"invalid ISO week: year={iso_year} week={iso_week}",
            ) from exc

        # ---------- 1. 確定済みコース取得 (course_fixed) ----------
        course_targets = await self._load_course_targets(
            db, iso_year=iso_year, iso_week=iso_week, office_id=office_id
        )

        # ---------- 2. 稼働スタッフ取得 ----------
        staff_pool = await self.load_active_staff(
            db, iso_year=iso_year, iso_week=iso_week, week_monday=week_monday
        )

        # ---------- 3. ローテーション履歴取得 ----------
        history = await self._load_rotation_history(
            db,
            iso_year=iso_year,
            iso_week=iso_week,
            history_weeks=ROTATION_HISTORY_WEEKS,
        )

        # ---------- 4. W16 固定割当 (manager -> M / 都賀 staff -> 都賀 A) ----------
        fixed_staff_by_course = await self._build_fixed_assignments(
            db, iso_year=iso_year, iso_week=iso_week, office_id=office_id
        )

        # ---------- 4b. Bug #2 fix (W25): staff_assigned コースを保護 ----------
        # assign-staff-only 再実行時に、既に staff_assigned 状態のコースの担当スタッフが
        # 別コースのハンガリアンに巻き込まれて重複割付される問題を防ぐ。
        # staff_assigned コースを fixed_staff_by_course に追加することで、
        # Layer 3 内の既存「固定スタッフ除外」ロジックが自動適用される。
        already_assigned_stmt = select(Course).where(
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.deleted_at.is_(None),
            Course.course_status == COURSE_STATUS_STAFF_ASSIGNED,
            Course.assigned_staff_id.isnot(None),
        )
        already_assigned_courses = list((await db.scalars(already_assigned_stmt)).all())

        # Phase G-28 H1 fix: 既に staff_id が付与済みの 0-visits コース (= 過去の
        # 自動割付で assign 済のまま visit 削除で空になったゴミ状態) は W25 経路でも
        # ``fixed_staff_by_course`` に再注入しない。 staff_pool が解放されず、
        # 患者ありコースが NULL になる本番バグ (2026-W21 で 5 件 NULL) の根本回避.
        # ``_load_course_targets`` / ``_build_fixed_assignments`` の空コース skip と整合.
        if already_assigned_courses:
            aa_visit_counts = await self._count_planned_visits_by_courses(
                db, [c.id for c in already_assigned_courses]
            )
            already_assigned_courses = [
                c for c in already_assigned_courses if aa_visit_counts.get(c.id, 0) > 0
            ]

        # Phase G-91 (修正A): 適用済み連続コースの再浮上防止用マップ.
        # 既に course_status='staff_assigned' かつ assigned_staff_id 付き (NOT NULL)
        # の course_id -> 確定済み staff_id. ``_build_review_items`` で「候補 (=solve
        # 割当) が既に確定済み staff と一致する連続/partner コース」を review から
        # 除外し、 かつ exclude_course_ids にも入れない (= commit に残す) ために使う.
        # already_assigned_courses は上の visits>0 フィルタ済 (= ゴミ 0-visits は除く).
        applied_staff_by_course: dict[UUID, UUID] = {
            c.id: c.assigned_staff_id
            for c in already_assigned_courses
            if c.assigned_staff_id is not None
        }

        for c in already_assigned_courses:
            if c.id not in fixed_staff_by_course and c.assigned_staff_id is not None:
                fixed_staff_by_course[c.id] = c.assigned_staff_id

        # ---------- 4c. W27 Phase A: StaffEvent 取得 (event 時間帯で重なる
        #               staff×course をハード除外するため) ----------
        events_by_staff = await self._load_staff_events(
            db,
            staff_ids=[s.staff_id for s in staff_pool],
            week_monday=week_monday,
        )

        # ---------- 4d. Phase 2: patient ごとの直近担当者リスト (週・曜日横断) ----------
        patient_recent_staff = await self._load_patient_recent_staff(db, week_monday=week_monday)

        # ---------- 5. 計算 ----------
        result = self.solve(
            course_targets,
            staff_pool,
            history=history,
            fixed_staff_by_course=fixed_staff_by_course,
            events_by_staff=events_by_staff,
            week_monday=week_monday,
            iso_year=iso_year,
            iso_week=iso_week,
            patient_recent_staff=patient_recent_staff,
        )

        # ---------- 5b. Phase G-89: 未割当コース検出 ----------
        # course_targets は visits>0 のコースのみ (= _load_course_targets が空コース
        # を early-skip 済). assignments に course_id が出てこないものは「人手不足で
        # 担当を確保できなかった」未割当コース. course_targets の順 (= weekday, code
        # 昇順) を保つことで決定的な並びにする.
        assigned_course_ids = {a.course_id for a in result.assignments}
        result.unassigned_course_ids = [
            ct.course_id for ct in course_targets if ct.course_id not in assigned_course_ids
        ]

        # ---------- 5c. Phase G-91: 確認レビューフロー (連続 index0 / 性別ブロック) ----------
        # クリーンなコースは自動確定 (commit) し、 以下 2 種はレビュー対象として返す:
        #   🟡 連続 (index0): solve で割当たったが直近担当者 (recent_index==0) と同じ
        #      → DB 未割当のまま (= commit しない). candidate は本来割り当たるスタッフ.
        #   🔴 性別ブロック: gender で未割当 + 性別無視時の候補が居る
        #      → DB 未割当のまま. candidate は性別無視時の候補スタッフ.
        # ``review_items`` を埋め、 連続 index0 コースを ``_persist`` 対象から除外する.
        (
            review_items,
            exclude_course_ids,
            auto_committed_notices,
            unresolved_warnings,
        ) = await self._build_review_items(
            db,
            course_targets=course_targets,
            result=result,
            staff_pool=staff_pool,
            fixed_staff_by_course=fixed_staff_by_course,
            events_by_staff=events_by_staff,
            week_monday=week_monday,
            history=history,
            iso_year=iso_year,
            iso_week=iso_week,
            patient_recent_staff=patient_recent_staff,
            applied_staff_by_course=applied_staff_by_course,
        )
        result.review_items = review_items
        # Wave N-1: 不可避連続の「お知らせ」(= 代替候補 0 名で自動確定した連続コース).
        result.auto_committed_notices = auto_committed_notices
        # W-11: 性別制約を満たす候補ゼロで自動解消できなかった残留違反の警告.
        result.unresolved_warnings = unresolved_warnings

        # 連続 index0 コースは自動 commit しない (= DB 未割当のまま). visit_group
        # partner を含めた除外集合を使い、 _persist へ渡す assignments から落とす.
        persist_assignments = [
            a for a in result.assignments if a.course_id not in exclude_course_ids
        ]
        # Phase G-91 (修正2): commit 実数 = _persist へ渡した assignments の course.
        # courses_assigned 算出はこれを単一ソースにする (= clean partner の過大計上回避).
        result.committed_course_ids = [a.course_id for a in persist_assignments]

        # ---------- 6. DB 反映 ----------
        await self._persist(db, persist_assignments)

        return result

    # ------------------------------------------------------------------ #
    # Phase G-91: 確認レビューフロー review_items 構築
    # ------------------------------------------------------------------ #

    async def _build_review_items(
        self,
        db: AsyncSession,
        *,
        course_targets: list[CourseAssignmentTarget],
        result: Layer3Result,
        staff_pool: list[StaffInfo],
        fixed_staff_by_course: dict[UUID, UUID],
        events_by_staff: dict[UUID, list[StaffEvent]],
        week_monday: date_cls | None,
        history: list[tuple[int, str, UUID]],
        iso_year: int | None,
        iso_week: int | None,
        patient_recent_staff: dict[UUID, list[UUID]],
        applied_staff_by_course: dict[UUID, UUID] | None = None,
    ) -> tuple[
        list[ReviewItem],
        set[UUID],
        list[AutoCommittedNotice],
        list[UnresolvedGenderWarning],
    ]:
        """Phase G-91: 連続 index0 / 性別ブロックの ``ReviewItem`` を構築する.

        レビュー対象 2 種を判定し、 表示用メタ (拠点名 / 患者名 / 訪問時刻 /
        sex_restriction / is_cause / 候補スタッフ) を DB から肉付けして返す.

        判定:
        - 🟡 連続 (consecutive): ``result.rotation_conflicts`` のうち
          ``recent_index==0`` を持つ course. **固定割当 (都賀 A / manager M) でも
          除外しない** (修正3 / オーナー決定 B: 1 名拠点でも連続は必ずレビューに
          出す. candidate = solve が割り当てた staff = 固定なら本名 1 名と同一).
          原因患者 (is_cause) = 連続になる患者 (= conflict の patient_id).
        - 🔴 性別 (gender): ``result.unassigned_course_ids`` のうち、
          gender_restrictions を持ち、 性別を無視したときの候補スタッフが居る
          course. candidate = ``_compute_gender_candidate_for_course`` の結果.
          原因患者 (is_cause) = 性別制限を持つ患者.
        - 🔗 partner (修正4): 連続コース X の visit_group partner Y (clean) も
          review_item に出す (candidate = Y の solve 割当 staff). X と Y は
          ``linked_course_ids`` で相互に紐付け、 apply 時に同一 ``_persist`` へ
          一緒に渡して 2 名体制 secondary を正しく解決する (= half-assigned 回避).
          partner Y の reason は元コード X の reason ('consecutive') を継ぐ.

        非 commit 除外集合 (``exclude_course_ids``):
            連続 index0 コース + その visit_group partner コース
            (= 片方だけ未割当を避けるため group 単位で除外).

        修正A (適用済み再浮上防止):
            ``applied_staff_by_course`` = 既に DB 上で course_status='staff_assigned'
            かつ assigned_staff_id (NOT NULL) のコース -> その確定 staff_id.
            連続 (X) / partner (Y) コードが、 既に当該マップにあり、 かつ確定 staff が
            候補 staff (= solve 割当) と一致する場合は「承認済み (=再確認不要)」とみなし、
            (1) review_items に出さない (2) exclude_course_ids にも入れない
            (= committed に残し件数を正しく数える). assigned_staff_id=NULL (一斉未割当
            後) や候補と不一致のコースは従来どおり review に出す.

        Wave N-1 (不可避連続の notice 化):
            連続 index0 コースのうち「代替候補」(= ハード制約 OK・当曜日未割当・
            原因患者の直近担当者でない・現候補本人以外) が 1 名も居ないものは
            **不可避**とみなし、 review_items に出さず exclude にも入れず
            (= クリーンコースと同一 ``_persist`` 経路で自動確定)、
            ``AutoCommittedNotice`` として理由つきで返す (reason_kind='single_staff'
            | 'all_recent'). 代替候補が 1 名以上なら従来どおり review_items.
            制約判定は ``_cost_single_cell`` (< HUNGARIAN_INFINITY) を再利用する.

        W-11 (性別残留違反の可視化):
            性別ブロック未割当コースで ``_compute_gender_candidate_for_course`` が
            候補を 1 名も返せない (= 純粋人手不足) 場合、 従来は黙って continue して
            いたが、 そのコースの現在の ``assigned_staff_id`` が非 null かつ性別制約を
            満たしていない (= 過去の割付で残った違反) ときは ``UnresolvedGenderWarning``
            として返す (自動クリアはしない = 「黙って消さない」原則). 現担当が性別制約を
            満たす / assigned_staff_id が null (純粋未割当) のときは従来どおり何も出さない.

        Returns:
            ``(review_items, exclude_course_ids, auto_committed_notices,
            unresolved_warnings)``.
            review_items は (weekday, course_code, 代表 start_time) で決定的ソート.
            auto_committed_notices は (weekday, course_code) で決定的ソート.
            unresolved_warnings は (weekday, course_code) で決定的ソート.
        """
        targets_by_id: dict[UUID, CourseAssignmentTarget] = {
            ct.course_id: ct for ct in course_targets
        }
        staff_by_id: dict[UUID, StaffInfo] = {s.staff_id: s for s in staff_pool}
        assigned_staff_by_course: dict[UUID, UUID] = {
            a.course_id: a.staff_id for a in result.assignments
        }
        if applied_staff_by_course is None:
            applied_staff_by_course = {}

        def _is_applied_match(course_id: UUID) -> bool:
            """修正A: 当該コースが既に承認済み (= 確定 staff == solve 候補) か.

            DB 上で staff_assigned + assigned_staff_id (NOT NULL) のコースが、 今回
            solve で割り当たった候補と同一 staff のとき True. これに該当する連続 /
            partner は再確認不要 (= review に出さず commit に残す). 確定 staff が NULL
            (一斉未割当後) や候補と不一致のときは False (= 従来どおり review 対象).
            """
            applied = applied_staff_by_course.get(course_id)
            if applied is None:
                return False
            candidate = assigned_staff_by_course.get(course_id)
            return candidate is not None and candidate == applied

        # ----- 🟡 連続 index0 コース (修正3: 固定割当も除外しない) -----
        # course_id -> 連続になる患者 id 集合 (is_cause マーク用).
        # オーナー決定 B: 都賀 A / manager M の固定連続も「候補=本名」でレビューに出す.
        # 修正A: 既に承認済み (確定 staff == 候補) の連続コースは再浮上させない.
        consecutive_cause_patients: dict[UUID, set[UUID]] = {}
        for conflict in result.rotation_conflicts:
            if conflict.recent_index != 0:
                continue
            if _is_applied_match(conflict.course_id):
                continue  # 修正A: 適用済み連続 → review にも exclude にも入れない
            consecutive_cause_patients.setdefault(conflict.course_id, set()).add(
                conflict.patient_id
            )

        # ----- 🔴 性別ブロック コース (候補が居るもののみ) -----
        # course_id -> candidate StaffInfo.
        gender_candidates: dict[UUID, StaffInfo] = {}
        # W-11: 性別ブロック未割当 + override 候補ゼロ (純粋人手不足) のコース集合.
        # このうち「現担当が非 null かつ性別制約違反」のものを後段で残留違反警告にする.
        gender_blocked_no_candidate: set[UUID] = set()
        for course_id in result.unassigned_course_ids:
            course = targets_by_id.get(course_id)
            if course is None:
                continue
            if not course.gender_restrictions:
                continue  # 性別制限が無い → 純粋人手不足 (レビュー対象外)
            candidate = self._compute_gender_candidate_for_course(
                course=course,
                staff_pool=staff_pool,
                weekday=course.weekday,
                events_by_staff=events_by_staff,
                week_monday=week_monday,
                fixed_staff_by_course=fixed_staff_by_course,
                history=history,
                prev_day_pairs=set(),
                iso_year=iso_year,
                iso_week=iso_week,
                patient_recent_staff=patient_recent_staff,
            )
            if candidate is None:
                # W-11: 性別無視でも候補なし = 純粋人手不足. 従来は黙って continue して
                # いたが、 現担当が性別制約違反のまま残っている可能性があるため観測ログを
                # 残し、 後段 (_build_unresolved_gender_warnings) で残留違反を可視化する.
                gender_blocked_no_candidate.add(course_id)
                logger.warning(
                    "gender_blocked_no_candidate: course_id=%s weekday=%s office_id=%s",
                    course_id,
                    course.weekday,
                    course.office_id,
                )
                continue
            gender_candidates[course_id] = candidate

        # W-11: 性別残留違反警告を構築 (= 現担当が非 null かつ性別制約違反のみ).
        # DB I/O を伴うため review 早期 return より前で 1 度だけ実行し、 全 return
        # パスで返す (= 純粋人手不足で他に何も無いケースでも警告だけは届ける).
        unresolved_warnings = await self._build_unresolved_gender_warnings(
            db,
            course_ids=gender_blocked_no_candidate,
            targets_by_id=targets_by_id,
        )

        # ----- Wave N-1: 不可避連続の判定 (代替候補 0 名 → review から外し自動確定+notice) -----
        # 各連続 index0 コースについて「代替候補」(ハード制約 OK・当曜日未割当・原因患者の
        # 直近担当者でない・現候補本人以外) の実在を検査. 0 名 = 不可避 → consecutive_cause_
        # patients から外して committed に残し、 notice を積む. 1 名以上 = 従来どおり review.
        # unavoidable_reason: course_id -> 'single_staff' | 'all_recent'.
        unavoidable_reason = self._detect_unavoidable_consecutive(
            consecutive_cause_patients=consecutive_cause_patients,
            targets_by_id=targets_by_id,
            result=result,
            staff_pool=staff_pool,
            assigned_staff_by_course=assigned_staff_by_course,
            events_by_staff=events_by_staff,
            week_monday=week_monday,
            history=history,
            iso_year=iso_year,
            iso_week=iso_week,
            patient_recent_staff=patient_recent_staff,
        )
        # notice の原因患者名組み立て用に不可避コースの cause patient を保存してから pop.
        original_consecutive_cause_patients: dict[UUID, set[UUID]] = {
            cid: set(consecutive_cause_patients.get(cid, set())) for cid in unavoidable_reason
        }
        # 不可避コースは連続 review 対象から除外 (= exclude にも入れず committed に残る).
        for cid in unavoidable_reason:
            consecutive_cause_patients.pop(cid, None)

        if not consecutive_cause_patients and not gender_candidates and not unavoidable_reason:
            # W-11: review/notice が無くても残留違反警告だけは返す.
            return [], set(), [], unresolved_warnings

        # ----- 🔗 修正4: 連続コースの visit_group partner を解決 -----
        # 連続コース X を非 commit にする際、 同 visit_group の partner course Y
        # (= 2 名体制の相方; clean のことが多い) も DB 未割当のまま残ると
        # half-assigned になる. Y を review_item として出し、 X と相互に
        # ``linked_course_ids`` で紐付ける. apply 時に X+Y を同一 _persist へ渡す.
        # course_id -> 連動する partner course_id 集合 (相互).
        partner_links: dict[UUID, set[UUID]] = {}
        # exclude_course_ids = 連続コース + その partner (= 非 commit 集合).
        exclude_course_ids: set[UUID] = set(consecutive_cause_patients)
        if consecutive_cause_patients:
            seed_ids = list(consecutive_cause_patients)
            seed_group_rows = (
                await db.execute(
                    select(Visit.visit_group_id, Visit.course_id).where(
                        Visit.course_id.in_(seed_ids),
                        Visit.visit_group_id.isnot(None),
                        Visit.status == VISIT_STATUS_PLANNED,
                        Visit.deleted_at.is_(None),
                    )
                )
            ).all()
            seed_group_ids = {gid for gid, _ in seed_group_rows if gid is not None}
            if seed_group_ids:
                grp_member_rows = (
                    await db.execute(
                        select(Visit.visit_group_id, Visit.course_id).where(
                            Visit.visit_group_id.in_(list(seed_group_ids)),
                            Visit.status == VISIT_STATUS_PLANNED,
                            Visit.deleted_at.is_(None),
                        )
                    )
                ).all()
                # visit_group_id -> 所属 course_id 集合
                courses_by_group: dict[UUID, set[UUID]] = {}
                for gid, c_id in grp_member_rows:
                    if gid is None or c_id is None:
                        continue
                    courses_by_group.setdefault(gid, set()).add(c_id)
                # 各 seed (連続) course の所属 group の他 course を partner として相互紐付け.
                # 修正A: partner Y が既に承認済み (確定 staff == 候補) の場合は exclude /
                # partner_links に含めない (= commit に残す). 連動対象は未承認メンバーのみ.
                seed_set = set(consecutive_cause_patients)
                for members in courses_by_group.values():
                    seeds_in_group = members & seed_set
                    if not seeds_in_group:
                        continue
                    # seed (X) は guard 済 (= consecutive_cause_patients に残ったもの)。
                    # partner (= seed 以外) のみ承認済みなら除外集合から落とす.
                    linkable = {m for m in members if m in seed_set or not _is_applied_match(m)}
                    for member in linkable:
                        exclude_course_ids.add(member)
                    for x in linkable:
                        partner_links.setdefault(x, set()).update(linkable - {x})

        # partner Y (= 連続コードではないが exclude された group メンバー) を review に含める.
        partner_only_ids = exclude_course_ids - set(consecutive_cause_patients)

        review_course_ids = (
            set(consecutive_cause_patients) | set(gender_candidates) | partner_only_ids
        )
        # Wave N-1: notice の理由文組み立て用に不可避コースの名前も一括ロードする.
        name_course_ids = review_course_ids | set(unavoidable_reason)
        if not name_course_ids:
            return [], exclude_course_ids, [], unresolved_warnings

        # ----- visit 情報 (patient_id, patient_name, start_time, sex_restriction) を一括ロード -----
        # course_id -> [(start_time, patient_id, patient_name, sex_restriction), ...]
        visit_rows = (
            await db.execute(
                select(
                    Visit.course_id,
                    Visit.start_time,
                    Visit.patient_id,
                    Patient.name,
                    Patient.sex_restriction,
                )
                .join(Patient, Patient.id == Visit.patient_id)
                .where(
                    Visit.course_id.in_(list(name_course_ids)),
                    Visit.status == VISIT_STATUS_PLANNED,
                    Visit.deleted_at.is_(None),
                    Patient.deleted_at.is_(None),
                )
            )
        ).all()
        visits_by_course: dict[UUID, list[tuple[time_cls, UUID, str, str | None]]] = {}
        for c_id, start_time, p_id, p_name, sex_restriction in visit_rows:
            if p_id is None:
                continue
            visits_by_course.setdefault(c_id, []).append(
                (start_time, p_id, p_name or "", sex_restriction)
            )

        # ----- 拠点名 (office_id -> name) を一括ロード -----
        office_ids = {
            ct.office_id
            for cid in name_course_ids
            if (ct := targets_by_id.get(cid)) is not None and ct.office_id is not None
        }
        office_name_by_id: dict[UUID, str] = {}
        if office_ids:
            o_rows = (await db.scalars(select(Office).where(Office.id.in_(list(office_ids))))).all()
            office_name_by_id = {o.id: o.name for o in o_rows}

        # ----- ReviewItem 構築 -----
        items: list[ReviewItem] = []
        for course_id in review_course_ids:
            course = targets_by_id.get(course_id)
            if course is None:
                continue
            is_consecutive = course_id in consecutive_cause_patients
            is_gender = course_id in gender_candidates
            # 連続 / partner はともに reason='consecutive' (= 黄カード) で扱う.
            # partner Y は連続コードではないが、 X と連動して apply するため同 reason.
            reason = "gender" if is_gender else "consecutive"

            # 候補スタッフ
            if is_gender:
                candidate = gender_candidates.get(course_id)
            else:
                # 連続 X / partner Y はともに solve が割り当てた staff が候補.
                candidate_id = assigned_staff_by_course.get(course_id)
                candidate = staff_by_id.get(candidate_id) if candidate_id is not None else None
            if candidate is None:
                continue  # 候補不明 (= 連続なのに assignment が無い等の異常) は skip

            # 原因患者集合 (is_cause)
            if is_consecutive:
                cause_pids = consecutive_cause_patients.get(course_id, set())
            elif is_gender:
                # 性別: sex_restriction を持つ患者が原因.
                cause_pids = {
                    p_id for (_st, p_id, _name, sr) in visits_by_course.get(course_id, []) if sr
                }
            else:
                # partner Y 自体は原因患者なし (= X が原因; Y は連動 apply のため出すだけ).
                cause_pids = set()

            # visits 構築 (start_time, patient_id 昇順で決定的に).
            raw_visits = sorted(
                visits_by_course.get(course_id, []),
                key=lambda t: (t[0], str(t[1])),
            )
            review_visits = [
                ReviewVisit(
                    patient_id=p_id,
                    patient_name=p_name,
                    start_time=start_time,
                    sex_restriction=sex_restriction,
                    is_cause=(p_id in cause_pids),
                )
                for (start_time, p_id, p_name, sex_restriction) in raw_visits
            ]

            # 連動 partner (修正4): exclude された group の他 course を決定的順に.
            linked = sorted(partner_links.get(course_id, set()), key=str)

            office_name = (
                office_name_by_id.get(course.office_id, "") if course.office_id is not None else ""
            )
            items.append(
                ReviewItem(
                    course_id=course_id,
                    office_name=office_name,
                    course_code=course.course_code,
                    weekday=course.weekday,
                    reason=reason,
                    candidate_staff_id=candidate.staff_id,
                    candidate_staff_name=candidate.name,
                    candidate_staff_sex=candidate.sex,
                    visits=review_visits,
                    linked_course_ids=linked,
                )
            )

        # 決定的ソート (weekday, course_code, 代表 start_time = 最小 visit start_time).
        def _sort_key(item: ReviewItem) -> tuple[int, str, str]:
            rep = item.visits[0].start_time.isoformat() if item.visits else ""
            return (item.weekday, item.course_code, rep)

        items.sort(key=_sort_key)

        # ----- Wave N-1: 不可避連続の AutoCommittedNotice を構築 -----
        notices = self._build_auto_committed_notices(
            unavoidable_reason=unavoidable_reason,
            original_cause_patients=original_consecutive_cause_patients,
            targets_by_id=targets_by_id,
            staff_by_id=staff_by_id,
            assigned_staff_by_course=assigned_staff_by_course,
            visits_by_course=visits_by_course,
            office_name_by_id=office_name_by_id,
        )
        return items, exclude_course_ids, notices, unresolved_warnings

    # ------------------------------------------------------------------ #
    # W-11: 性別残留違反 (候補ゼロ) の可視化警告構築
    # ------------------------------------------------------------------ #

    async def _build_unresolved_gender_warnings(
        self,
        db: AsyncSession,
        *,
        course_ids: set[UUID],
        targets_by_id: dict[UUID, CourseAssignmentTarget],
    ) -> list[UnresolvedGenderWarning]:
        """W-11: 性別ブロック未割当 + 候補ゼロのコースの残留違反を可視化する.

        ``course_ids`` = 性別制限つき未割当 + ``_compute_gender_candidate_for_course``
        が候補を返せなかったコース集合. このうち現在の ``assigned_staff_id`` が
        非 null かつその担当が性別制約を **満たしていない** (= 過去の割付で残った違反)
        ものだけを ``UnresolvedGenderWarning`` にする。 自動クリアはしない (可視化のみ)。

        判定に使う担当スタッフは ``status`` を問わず Staff 行から解決する (= 既に退職
        した違反担当も名前を表示できるようにするため). 現担当が性別制約を満たす /
        assigned_staff_id が null のコースは警告に含めない (= 純粋人手不足のみ).

        Returns:
            (weekday, course_code) 昇順の ``UnresolvedGenderWarning`` list.
            ``course_ids`` が空なら空 list.
        """
        if not course_ids:
            return []

        rows = (
            await db.execute(
                select(
                    Course.id,
                    Course.assigned_staff_id,
                    Staff.name,
                    Staff.sex,
                    Office.name,
                )
                .join(Staff, Staff.id == Course.assigned_staff_id)
                .outerjoin(Office, Office.id == Course.office_id)
                .where(
                    Course.id.in_(list(course_ids)),
                    Course.assigned_staff_id.isnot(None),
                )
            )
        ).all()

        warnings: list[UnresolvedGenderWarning] = []
        for c_id, _staff_id, staff_name, staff_sex, office_name in rows:
            course = targets_by_id.get(c_id)
            if course is None:
                continue
            # 現担当が性別制約を満たしていれば残留違反ではない (= 警告不要).
            if _sex_satisfies_restrictions(staff_sex, course.gender_restrictions):
                continue
            name = staff_name or "不明"
            warnings.append(
                UnresolvedGenderWarning(
                    course_id=c_id,
                    course_code=course.course_code,
                    weekday=course.weekday,
                    office_name=office_name or "",
                    current_staff_name=name,
                    reason_text=(
                        f"性別制約を満たす候補が見つかりません。現在の担当（{name}）は"
                        "性別制約を満たしていません — 手動で調整してください"
                    ),
                )
            )

        warnings.sort(key=lambda w: (w.weekday, w.course_code))
        return warnings

    # ------------------------------------------------------------------ #
    # Wave N-1: 不可避連続の判定・自動確定 notice 構築
    # ------------------------------------------------------------------ #

    @staticmethod
    def _reconstruct_recent_before_weekday(
        course_targets: list[CourseAssignmentTarget],
        assignments: list[StaffAssignment],
        patient_recent_staff: dict[UUID, list[UUID]],
    ) -> dict[int, dict[UUID, list[UUID]]]:
        """Wave N-1: 各曜日の割当直前 ``working_recent`` スナップショットを再構築する.

        ``solve()`` は ``patient_recent_staff`` のコピーを曜日順に前進伝搬させ、
        各曜日完了後に当日割当を各患者リストへ prepend する
        (``layer3_assignment.solve`` の working_recent 更新ロジックと同一手順).
        ローテ衝突検出は「当日割当を prepend する **前**」の状態を見るため、
        本メソッドも各曜日の prepend 直前の状態を snapshot として返す
        (= 代替候補が原因患者の直近担当者か判定する基準を衝突検出と一致させる).

        Returns:
            ``{weekday: {patient_id: [staff_id, ...]}}``. 割当のある曜日のみキーを持つ.
        """
        targets_by_id = {ct.course_id: ct for ct in course_targets}
        assignments_by_weekday: dict[int, list[StaffAssignment]] = {}
        for a in assignments:
            assignments_by_weekday.setdefault(a.weekday, []).append(a)

        working: dict[UUID, list[UUID]] = {
            pid: list(staff_list) for pid, staff_list in patient_recent_staff.items()
        }
        snapshots: dict[int, dict[UUID, list[UUID]]] = {}
        for weekday in sorted(assignments_by_weekday.keys()):
            # 当日割当を prepend する前の状態を snapshot (= 衝突検出が見る状態).
            snapshots[weekday] = {pid: list(lst) for pid, lst in working.items()}
            for a in assignments_by_weekday[weekday]:
                ct = targets_by_id.get(a.course_id)
                if ct is None:
                    continue
                for pid in ct.patient_ids:
                    recent = [sid for sid in working.get(pid, []) if sid != a.staff_id]
                    recent.insert(0, a.staff_id)
                    working[pid] = recent[:PATIENT_RECENT_DEPTH]
        return snapshots

    def _detect_unavoidable_consecutive(
        self,
        *,
        consecutive_cause_patients: dict[UUID, set[UUID]],
        targets_by_id: dict[UUID, CourseAssignmentTarget],
        result: Layer3Result,
        staff_pool: list[StaffInfo],
        assigned_staff_by_course: dict[UUID, UUID],
        events_by_staff: dict[UUID, list[StaffEvent]],
        week_monday: date_cls | None,
        history: list[tuple[int, str, UUID]],
        iso_year: int | None,
        iso_week: int | None,
        patient_recent_staff: dict[UUID, list[UUID]],
    ) -> dict[UUID, str]:
        """Wave N-1: 連続 index0 コースを不可避 / 回避可能に分類する (設計書 R-2).

        各コースについて「代替候補」の実在を検査する:
            代替候補 = active staff のうち
              ・ハード制約を全て満たす (勤務曜日 / 実効拠点一致 / 性別 AND /
                StaffEvent 非重複 / 直近1週同コード除外)
                 = ``_cost_single_cell(...) < HUNGARIAN_INFINITY`` の再利用で判定.
              ・当該曜日の他コースに (この実行で) 割当済みでない
              ・原因患者すべての直近担当者リスト (snapshot) に入っていない
              ・現候補スタッフ本人でない

        代替候補が 1 名以上 → 回避可能 (= 従来どおり review). 0 名 → 不可避.
        不可避の reason_kind:
            ・適格者 (ハード制約 OK + 当曜日空き, 現候補本人を含む) が実質 1 名 = single_staff
            ・適格者が複数だが全員が原因患者の直近担当者 = all_recent

        既知の edge case (レビュー記録・実害限定): 不可避コース X の visit_group
        partner Y が「回避可能な連続」の場合、 X は自動確定・Y は review 残留となり
        一時的に片割れ確定になる。 Y を review → apply すれば解消するため許容
        (2 名体制かつ片方のみ不可避という条件自体が稀)。 頻発するなら partner を
        不可避側に連動させる修正を検討する。

        Returns:
            ``{course_id: reason_kind}`` (不可避コースのみ). 空 dict = 全て回避可能.
        """
        if not consecutive_cause_patients:
            return {}

        snapshots = self._reconstruct_recent_before_weekday(
            list(targets_by_id.values()), result.assignments, patient_recent_staff
        )
        # weekday -> その曜日に (この実行で) 割当済みの staff_id 集合 (= 代替に使えない).
        assigned_by_weekday: dict[int, set[UUID]] = {}
        for a in result.assignments:
            assigned_by_weekday.setdefault(a.weekday, set()).add(a.staff_id)

        unavoidable: dict[UUID, str] = {}
        for course_id, cause_pids in consecutive_cause_patients.items():
            course = targets_by_id.get(course_id)
            if course is None:
                continue
            current_staff_id = assigned_staff_by_course.get(course_id)
            if current_staff_id is None:
                continue
            weekday = course.weekday
            snapshot = snapshots.get(weekday, {})
            taken = assigned_by_weekday.get(weekday, set())
            # 原因患者すべての直近担当者 (= このいずれかに入る候補は連続を作る).
            cause_recent: set[UUID] = set()
            for pid in cause_pids:
                cause_recent.update(snapshot.get(pid, []))

            # 適格者集合 (= 当該コースを実際に担当し得る staff). 現候補本人は必ず含む.
            eligible_ids: set[UUID] = {current_staff_id}
            alt_exists = False
            for staff in staff_pool:
                # manager は通常割付の候補にしない (solve の eligible_staff と整合).
                if staff.role == "manager":
                    continue
                if staff.staff_id == current_staff_id:
                    continue
                # 当該曜日の他コースに割当済み = 空いていない (= 代替不可).
                if staff.staff_id in taken:
                    continue
                # ハード制約 (勤務曜日 / 実効拠点 / 性別 AND / event 重複 / 直近1週同コード)
                # を ``_cost_single_cell`` の再利用で判定 (< INF なら全ハード OK).
                cost = self._cost_single_cell(
                    weekday=weekday,
                    course=course,
                    staff=staff,
                    history=history,
                    prev_day_pairs=set(),
                    events_by_staff=events_by_staff,
                    week_monday=week_monday,
                    iso_year=iso_year,
                    iso_week=iso_week,
                    patient_recent_staff=snapshot,
                )
                if cost >= HUNGARIAN_INFINITY:
                    continue
                eligible_ids.add(staff.staff_id)
                # 代替候補 = 適格 かつ 原因患者の直近担当者でない (= 連続を作らない).
                # 1 名見つかれば回避可能が確定する (eligible_ids は不可避時のみ参照
                # されるため打ち切ってよい).
                if staff.staff_id not in cause_recent:
                    alt_exists = True
                    break
            if alt_exists:
                continue  # 回避可能 → 従来どおり review
            unavoidable[course_id] = "single_staff" if len(eligible_ids) <= 1 else "all_recent"
        return unavoidable

    @staticmethod
    def _build_auto_committed_notices(
        *,
        unavoidable_reason: dict[UUID, str],
        original_cause_patients: dict[UUID, set[UUID]],
        targets_by_id: dict[UUID, CourseAssignmentTarget],
        staff_by_id: dict[UUID, StaffInfo],
        assigned_staff_by_course: dict[UUID, UUID],
        visits_by_course: dict[UUID, list[tuple[time_cls, UUID, str, str | None]]],
        office_name_by_id: dict[UUID, str],
    ) -> list[AutoCommittedNotice]:
        """Wave N-1: 不可避連続コードの ``AutoCommittedNotice`` を組み立てる.

        理由文 (設計書 R-2) は BE 側で日本語を組み立てる (FE はそのまま表示):
        - single_staff: 「この曜日に◯◯(拠点)で勤務できるスタッフが△△さん 1 名のため、
          連続担当は避けられません」
        - all_recent: 「対応可能なスタッフ全員がこの患者の直近担当者のため、
          どなたが担当しても連続になります」

        Returns:
            (weekday, course_code) 昇順の ``AutoCommittedNotice`` リスト.
        """
        notices: list[AutoCommittedNotice] = []
        for course_id, reason_kind in unavoidable_reason.items():
            course = targets_by_id.get(course_id)
            if course is None:
                continue
            staff_id = assigned_staff_by_course.get(course_id)
            staff = staff_by_id.get(staff_id) if staff_id is not None else None
            if staff is None:
                continue
            office_name = (
                office_name_by_id.get(course.office_id, "") if course.office_id is not None else ""
            )
            # 原因患者名 (patient_id 昇順で決定的に).
            cause_pids = original_cause_patients.get(course_id, set())
            name_by_pid = {
                p_id: p_name for (_st, p_id, p_name, _sr) in visits_by_course.get(course_id, [])
            }
            cause_patient_names = [name_by_pid.get(pid, "") for pid in sorted(cause_pids, key=str)]

            if reason_kind == "single_staff":
                reason_text = (
                    f"この曜日に{office_name}で勤務できるスタッフが{staff.name}さん1名のため、"
                    "連続担当は避けられません"
                )
            else:  # all_recent
                reason_text = (
                    "対応可能なスタッフ全員がこの患者の直近担当者のため、"
                    "どなたが担当しても連続になります"
                )

            notices.append(
                AutoCommittedNotice(
                    course_id=course_id,
                    course_code=course.course_code,
                    weekday=course.weekday,
                    office_name=office_name,
                    staff_name=staff.name,
                    cause_patient_names=cause_patient_names,
                    reason_kind=reason_kind,
                    reason_text=reason_text,
                )
            )
        notices.sort(key=lambda n: (n.weekday, n.course_code))
        return notices

    # ------------------------------------------------------------------ #
    # 純粋関数: solve()
    # ------------------------------------------------------------------ #

    def solve(
        self,
        course_targets: list[CourseAssignmentTarget],
        staff_pool: list[StaffInfo],
        *,
        history: list[tuple[int, str, UUID]] | None = None,
        fixed_staff_by_course: dict[UUID, UUID] | None = None,
        same_course_prev_day_penalty: bool = True,
        events_by_staff: dict[UUID, list[StaffEvent]] | None = None,
        week_monday: date_cls | None = None,
        iso_year: int | None = None,
        iso_week: int | None = None,
        patient_recent_staff: dict[UUID, list[UUID]] | None = None,
    ) -> Layer3Result:
        """純粋関数版エントリポイント (テスト / fixture 評価で直接使う).

        Args:
            course_targets: 確定済みコースのリスト (=対象).
            staff_pool: 稼働スタッフのリスト.
            history: ``[(weeks_ago, course_code, staff_id), ...]`` の履歴.
                ``weeks_ago`` は当該週からの距離 (1 = 直近 1 週前).
            fixed_staff_by_course: W16 — 事前固定割当 (course_id -> staff_id).
                指定された course はマッチングを skip し直接当該 staff を割当て、
                かつ当該 staff はその曜日の他の course から除外される.
                典型例: 川名(manager) -> M / 関谷 -> 都賀 A.
            same_course_prev_day_penalty: W16 — True のとき、同一スタッフが
                前日と同じ course_code を担当することにペナルティを付与する
                (= 同患者連続回避).
            events_by_staff: W27 Phase A — ``staff_id -> [StaffEvent, ...]``.
                event 時間帯と course 内 visit 時間帯が重なる staff は
                当該 course から **ハード除外** される (HUNGARIAN_INFINITY).
                ``None`` または空辞書 = event 除外無し (regression 互換).
            week_monday: W27 Phase A — 当該 ISO 週の月曜日 (date).
                ``events_by_staff`` 利用時に必須。weekday から実日付を
                算出して event 時間帯と比較する。
            iso_year: Wave 5 — ローテーション seed 用 ISO 年.
                ``None`` のとき rotation score の加算を skip.
            iso_week: Wave 5 — ローテーション seed 用 ISO 週 (1-53).
            patient_recent_staff: Phase 2 — 患者ごとの「直近担当者リスト」
                (``patient_id -> [staff_id, ...]``). 最近順 (index 0 = 最も最近)、
                最大 ``PATIENT_RECENT_DEPTH`` 件. 週・曜日を横断した過去履歴を
                ``_load_patient_recent_staff`` で構築して渡す. solve 内で当日割当を
                各患者リストの先頭に prepend しながら後続曜日へ伝搬し、 「同じ患者に
                毎回同じ staff が当たる」 のを段階ペナルティ
                (``COST_PATIENT_RECENT_1/2/3``) で避ける. ``None`` で履歴なし.
                **前提: 患者は1日1 visit**。 working_recent の前進伝搬は曜日ループの
                各曜日完了後に一括更新するため、 同一 weekday 内で同患者が複数コースに
                跨っても当日内の相互伝搬はしない (= 曜日横断でのみ伝搬する).

        Returns:
            Layer3Result.

        Notes:
            - マネージャー (role='manager') は **fixed_staff_by_course で割当
              対象外でない限り** staff_pool から自動除外する.
              W16 では manager を M コースに固定割当する用途のため、
              fixed 経由で割り当てられた manager は許容する.
            - 各曜日ごとに独立にハンガリアン法を適用 (1 スタッフ 1 日 1 コース原則).
            - W16: 曜日順 (Mon -> Sat) に解き、前日割当を後続曜日へ伝搬する.
            - Phase G-27: ``course.gender_restrictions`` の値 ('female_only'/'male_only') は
              cost 計算時に ``_normalize_sex_restriction`` で staff.sex の形式
              ('female'/'male') に正規化してから比較する.
            - Phase G-29: 1st pass で NULL のまま残ったコースに対しては、
              ``_apply_manager_fallback`` (2nd pass) で manager を greedy 配置する.
        """
        if history is None:
            history = []
        if fixed_staff_by_course is None:
            fixed_staff_by_course = {}
        if events_by_staff is None:
            events_by_staff = {}
        if patient_recent_staff is None:
            patient_recent_staff = {}

        # 固定割当先となる staff_id 集合 (= manager を除外しない対象)
        fixed_staff_ids: set[UUID] = set(fixed_staff_by_course.values())

        # マネージャー除外 (§3.6.4) — ただし fixed 割当対象の manager は保持
        # (fixed 経路で M コースへの lookup に使用するため eligible_staff に残す必要がある)
        eligible_staff = [
            s for s in staff_pool if s.role != "manager" or s.staff_id in fixed_staff_ids
        ]

        # 曜日でグルーピング
        by_weekday: dict[int, list[CourseAssignmentTarget]] = {}
        for ct in course_targets:
            by_weekday.setdefault(ct.weekday, []).append(ct)

        all_assignments: list[StaffAssignment] = []
        total_distance = 0.0
        # Phase G-89: ローテ衝突 (= 直近担当者を再割り当てした) の記録先.
        rotation_conflicts: list[RotationConflict] = []

        # W16: 「前日同コースペナルティ」用. (course_code, staff_id) を直前曜日
        # の割当から構築する.
        prev_day_pairs: set[tuple[str, UUID]] = set()

        # Phase 2: 患者ごとの「直近担当者リスト」を週内で前進伝搬する単一機構.
        # loader 結果のコピーで初期化 (= 呼び出し側辞書を破壊しない). 各曜日完了後に
        # 当日割当の各患者リストへ当日 staff を prepend し、 後続曜日は当日割当を
        # 「1 回前」 として見る (= 旧 working_prev_day/working_prev2_day を統合置換).
        working_recent: dict[UUID, list[UUID]] = {
            pid: list(staff_list) for pid, staff_list in patient_recent_staff.items()
        }

        # 各曜日ごとに独立して解く (= 1 スタッフ 1 日 1 コース制約は曜日内で閉じる)
        # W16: 曜日順 (Mon -> Sat) に解き、前日割当を後続曜日へ伝搬する.
        for weekday in sorted(by_weekday.keys()):
            day_courses = by_weekday[weekday]
            day_assignments = self._solve_one_day(
                weekday=weekday,
                day_courses=day_courses,
                staff_pool=eligible_staff,
                history=history,
                fixed_staff_by_course=fixed_staff_by_course,
                prev_day_pairs=prev_day_pairs if same_course_prev_day_penalty else set(),
                events_by_staff=events_by_staff,
                week_monday=week_monday,
                iso_year=iso_year,
                iso_week=iso_week,
                patient_recent_staff=working_recent,
            )

            # ---------- Phase G-29: 2nd pass — manager fallback ----------
            # 1st pass (Hungarian, manager 除外) 完了後、 当該曜日で NULL のまま
            # 残ったコースに対し manager を greedy 配置する。
            # User 仕様: 「割り当ての人がいなかった場合に manager を配置する。
            # 最初からの割り当てロジックの中に manager を混ぜない」.
            day_assignments = self._apply_manager_fallback(
                weekday=weekday,
                day_courses=day_courses,
                staff_pool=staff_pool,
                day_assignments=day_assignments,
                events_by_staff=events_by_staff,
                week_monday=week_monday,
            )

            for a in day_assignments:
                all_assignments.append(a)
                # 距離集計 — fallback で入った manager も staff_pool に居るので参照可
                course = next(c for c in day_courses if c.course_id == a.course_id)
                staff = next(s for s in staff_pool if s.staff_id == a.staff_id)
                total_distance += self._distance_km(course, staff)

            # 当日割当を「前日割当」として次曜日へ受け渡し
            prev_day_pairs = {(a.course_code, a.staff_id) for a in day_assignments}

            # Phase G-89: ローテ衝突検出 (= working_recent prepend 更新 **前**).
            # この時点の working_recent は cost 関数が penalty 算出に参照したのと
            # 同一状態 (= 当日割当を未だ反映していない). 各 assignment の course の
            # 各 patient について working_recent.get(pid) 内の staff_id index を引き、
            # ヒットしたら衝突として記録する (= 人手不足で直近担当者を再割り当て).
            # Hungarian 経由は cost が使った recent index と一致する。 manager
            # fallback 経由は cost 未評価のため、 本ループで実 recent 位置を新規算出する
            # (= patient 安全上は同じ衝突として扱う).
            for a in day_assignments:
                course = next(c for c in day_courses if c.course_id == a.course_id)
                for pid in course.patient_ids:
                    recent = working_recent.get(pid)
                    if not recent:
                        continue
                    for idx, sid in enumerate(recent):
                        if sid == a.staff_id:
                            rotation_conflicts.append(
                                RotationConflict(
                                    course_id=a.course_id,
                                    weekday=weekday,
                                    patient_id=pid,
                                    staff_id=a.staff_id,
                                    recent_index=idx,
                                )
                            )
                            break  # 最小 index (= cost が使った最大 penalty) のみ

            # Phase 2: 当日割当の各患者リスト先頭に当日 staff を prepend する.
            # 既存なら前方へ移動 (distinct 維持) し、 DEPTH で truncate する. これにより
            # 後続曜日は当日割当を「1 回前」 として見る (= 火→木 同患者で別 staff を満たす).
            # 前提: 患者は1日1 visit。 同一 weekday 内で同患者が複数コースに跨る場合、
            # working_recent の更新は曜日ループ完了後に一括で行われるため、 当日内の
            # 相互伝搬 (=同日別コース間で互いを「1回前」 と見る) は発生しない.
            for a in day_assignments:
                course = next(c for c in day_courses if c.course_id == a.course_id)
                for pid in course.patient_ids:
                    recent = working_recent.get(pid, [])
                    # 既存 staff を除去してから先頭に追加 (= 前方移動 + distinct)
                    recent = [sid for sid in recent if sid != a.staff_id]
                    recent.insert(0, a.staff_id)
                    working_recent[pid] = recent[:PATIENT_RECENT_DEPTH]

        # ローテーション分散度 (Gini) — fixed 割当は分散度計算対象外
        # (固定スタッフはローテーション対象ではないため)
        rotatable_assignments = [a for a in all_assignments if a.staff_id not in fixed_staff_ids]
        rotatable_staff_count = max(
            1, len([s for s in eligible_staff if s.staff_id not in fixed_staff_ids])
        )
        rotation_score = self._gini_index(
            [a.staff_id for a in rotatable_assignments],
            staff_count=rotatable_staff_count,
        )

        return Layer3Result(
            assignments=all_assignments,
            rotation_score=round(rotation_score, 6),
            total_distance_km=round(total_distance, 4),
            rotation_conflicts=rotation_conflicts,
        )

    # ------------------------------------------------------------------ #
    # private: per-weekday solver
    # ------------------------------------------------------------------ #

    def _solve_one_day(
        self,
        *,
        weekday: int,
        day_courses: list[CourseAssignmentTarget],
        staff_pool: list[StaffInfo],
        history: list[tuple[int, str, UUID]],
        fixed_staff_by_course: dict[UUID, UUID] | None = None,
        prev_day_pairs: set[tuple[str, UUID]] | None = None,
        events_by_staff: dict[UUID, list[StaffEvent]] | None = None,
        week_monday: date_cls | None = None,
        iso_year: int | None = None,
        iso_week: int | None = None,
        patient_recent_staff: dict[UUID, list[UUID]] | None = None,
    ) -> list[StaffAssignment]:
        """1 曜日内で (course × staff) のハンガリアンを解く.

        Args:
            weekday: 0=Mon..6=Sun.
            day_courses: 当該曜日の course list.
            staff_pool: 稼働スタッフ.
            history: ローテーション履歴.
            fixed_staff_by_course: W16 — 事前固定割当 (course_id -> staff_id).
                対象 course はマッチング対象外とし結果に直接含める. 当該 staff
                は他の course への割当からも除外する.
            prev_day_pairs: W16 — 前日の (course_code, staff_id) 集合.
                同一ペアの再選択にペナルティを付与する (= 同患者連続回避).
            events_by_staff: W27 Phase A — staff_id -> [StaffEvent, ...].
            week_monday: W27 Phase A — 当該週月曜日 (visit 時刻と event の
                重複判定に必要).
            patient_recent_staff: Phase 2 — 患者ごとの直近担当者リスト
                (``patient_id -> [staff_id, ...]``, 最近順, 最大 ``PATIENT_RECENT_DEPTH``).
        """
        if not day_courses or not staff_pool:
            return []
        if fixed_staff_by_course is None:
            fixed_staff_by_course = {}
        if prev_day_pairs is None:
            prev_day_pairs = set()
        if events_by_staff is None:
            events_by_staff = {}
        if patient_recent_staff is None:
            patient_recent_staff = {}

        # ----- W16 + Phase 1: 固定割当を先に剥がす (有効な固定のみ確定) -----
        # Phase 1: 固定割当ルートは従来 work_days のみ見て無条件確定し、 性別 /
        # event 重複のハード制約を素通りしていた (= 患者安全の穴). 固定 course は
        # 「(a) staff が pool に存在 (b) 当日勤務 (c) 性別制約を満たす (d) event 重複なし」
        # を **全て満たす時のみ** 確定し、 満たさない course は free_courses に回して
        # 通常ハンガリアン + manager fallback で正しい性別の人へ割当する (不能なら未割当).
        result: list[StaffAssignment] = []
        free_courses: list[CourseAssignmentTarget] = []
        # 有効に固定確定したスタッフのみ (= free_staff から除外する集合).
        # 違反でドロップした固定スタッフは free に残し、 別コースで再利用可能にする.
        valid_fixed_staff_ids: set[UUID] = set()
        for course in day_courses:
            staff_id = fixed_staff_by_course.get(course.course_id)
            if staff_id is None:
                free_courses.append(course)
                continue
            staff = next((s for s in staff_pool if s.staff_id == staff_id), None)
            # (a) pool 存在 + (b) 当日勤務
            if staff is None or weekday not in staff.work_days:
                free_courses.append(course)
                continue
            # (c) 性別ハード制約 (Phase 1: 固定割当ルートの性別穴塞ぎ)
            if not _staff_satisfies_gender(staff, course):
                free_courses.append(course)
                continue
            # (c2) Phase G-90: 拠点ハード制約 (固定割当ルートの多重防御).
            # ``_build_fixed_assignments`` は primary_office_id==office.id で
            # 絞っているので通常は整合するが、 primary 休業曜日に effective が
            # secondary へ転入する edge では固定 staff が他拠点コースに残り得る.
            # 実効拠点 ≠ course 拠点なら固定確定せず free_courses へ回す.
            # ``course.office_id is None`` (= 合成 fixture) では skip (後方互換).
            if course.office_id is not None:
                if staff.effective_office_for_weekday(weekday) != course.office_id:
                    free_courses.append(course)
                    continue
            # (d) event 時間帯重複 (W33 buffer 込み, events 情報がある場合のみ)
            if events_by_staff and week_monday is not None and course.visits:
                if _has_event_overlap_with_buffer(
                    staff_id=staff.staff_id,
                    course=course,
                    weekday=weekday,
                    events_by_staff=events_by_staff,
                    week_monday=week_monday,
                ):
                    free_courses.append(course)
                    continue
            result.append(
                StaffAssignment(
                    weekday=weekday,
                    course_code=course.course_code,
                    course_id=course.course_id,
                    staff_id=staff.staff_id,
                )
            )
            valid_fixed_staff_ids.add(staff.staff_id)

        # 固定で取られたスタッフは free のマッチング対象から除外。
        # Phase 1 修正: 除外対象は「**有効確定した固定割当のスタッフのみ**」.
        # 旧 W25 fix は fixed_staff_by_course の全 values を除外していたが、 これだと
        # 性別/event 違反でドロップした固定スタッフまで free から消え、 他コースで
        # 再利用できなくなる (= 不要な未割当を生む). 違反でドロップした分は free に残す.
        free_staff = [s for s in staff_pool if s.staff_id not in valid_fixed_staff_ids]

        if not free_courses or not free_staff:
            return result

        n_courses = len(free_courses)
        n_staff = len(free_staff)
        n = max(n_courses, n_staff)  # 正方化

        # cost[i][j] = (course i, staff j) のコスト. ダミー行/列は 0.0 で埋める.
        cost: list[list[float]] = [[0.0] * n for _ in range(n)]

        for i, course in enumerate(free_courses):
            for j, staff in enumerate(free_staff):
                cost[i][j] = self._cost_single_cell(
                    weekday=weekday,
                    course=course,
                    staff=staff,
                    history=history,
                    prev_day_pairs=prev_day_pairs,
                    events_by_staff=events_by_staff,
                    week_monday=week_monday,
                    iso_year=iso_year,
                    iso_week=iso_week,
                    patient_recent_staff=patient_recent_staff,
                )
        # ダミー行 (i >= n_courses): 全列コスト 0  → 「未割当の course/staff」を吸収
        # ダミー列 (j >= n_staff): 全行コスト 0
        # → 既に 0 で埋めている。OK.

        assignment = hungarian_min_cost(cost)

        # 結果フィルタリング: 実コース × 実スタッフかつ INF 未満のもののみ採用
        for i in range(n_courses):
            j = assignment[i]
            if j < 0 or j >= n_staff:
                continue
            if cost[i][j] >= HUNGARIAN_INFINITY:
                # ハード制約違反のセル — 割当不能
                continue
            course = free_courses[i]
            staff = free_staff[j]
            result.append(
                StaffAssignment(
                    weekday=weekday,
                    course_code=course.course_code,
                    course_id=course.course_id,
                    staff_id=staff.staff_id,
                )
            )

        return result

    def _cost_single_cell(
        self,
        *,
        weekday: int,
        course: CourseAssignmentTarget,
        staff: StaffInfo,
        history: list[tuple[int, str, UUID]],
        prev_day_pairs: set[tuple[str, UUID]] | None = None,
        events_by_staff: dict[UUID, list[StaffEvent]] | None = None,
        week_monday: date_cls | None = None,
        iso_year: int | None = None,
        iso_week: int | None = None,
        patient_recent_staff: dict[UUID, list[UUID]] | None = None,
    ) -> float:
        """単一セル (course, staff) のコストを返す.

        コスト関数 (§5.4 + W16 拡張 + W27 Phase A 拡張 + W33 バッファ拡張
        + Phase 2 拡張 + Phase G-90 拡張):
            cost = β * rotation_penalty (course-code 単位の補助タイブレーク)
                 + γ * gender (ハード)
                 + δ * work_day (ハード)
                 + Phase G-90: 拠点ハード制約 (= 実効拠点 ≠ course 拠点で INF)
                 + W16: 前日同コース penalty
                 + W27/W33: event 時間帯重複 + BUFFER_MINUTES バッファ (ハード除外)
                 + Phase 2: patient 中心ローテ段階ペナルティ
                   (直近 1/2/3 回前 = COST_PATIENT_RECENT_1/2/3, 患者間は max)
                 + Wave 5: deterministic random rotation score (0 .. COST_W5_ROTATION_MAX)

        γ / δ / 拠点 / W27/W33 はハード制約なので INF 相当 (= ``HUNGARIAN_INFINITY``).
        Phase 2 / Wave 5 ペナルティはすべてソフト (= INF ではなく加算).

        Phase G-90: 距離 (α) はコストから撤去した. スタッフは自拠点コースにのみ
        行く (拠点ハード制約) ため、 同拠点内では距離 (km) は割付に無関係.
        ``_distance_km`` は total_distance_km レポート用にのみ残す.
        """
        if prev_day_pairs is None:
            prev_day_pairs = set()
        if events_by_staff is None:
            events_by_staff = {}
        if patient_recent_staff is None:
            patient_recent_staff = {}

        # ---------- δ: 勤務曜日違反 (ハード制約) ----------
        if weekday not in staff.work_days:
            return HUNGARIAN_INFINITY

        # ---------- γ: 性別ミスマッチ (ハード制約) ----------
        # 患者の sex_restriction (例: "female_only") はそのスタッフの sex と一致する必要あり.
        # Phase 1: 判定ロジックは ``_staff_satisfies_gender`` に単一ソース化
        # (固定割当ルートと同一セマンティクス). 'female_only'/'male_only' の
        # 正規化と AND semantics は同ヘルパー内で処理する (Phase G-27 fix 維持).
        if not _staff_satisfies_gender(staff, course):
            return HUNGARIAN_INFINITY

        # ---------- Phase G-90: 拠点ハード制約 ----------
        # スタッフの実効拠点 (当該 weekday の effective office) ≠ コースの拠点なら
        # 割当不可 (= 他拠点漏れの防止). スタッフは住所で拠点が決まり、 自拠点の
        # コースにのみ行く (距離は無関係). primary 休業曜日は応援先 secondary が
        # effective になる (``effective_office_for_weekday`` の既存仕様).
        # ``course.office_id is None`` (= 合成テスト fixture) のときは制約 skip
        # (後方互換).
        if course.office_id is not None:
            eff = staff.effective_office_for_weekday(weekday)
            if eff != course.office_id:
                return HUNGARIAN_INFINITY

        # ---------- W27/W33: StaffEvent 時間帯重複 + バッファ (ハード制約) ----------
        # W27: event 時間帯と course 内 visit 時間帯が重なる場合は除外。
        # W33: BUFFER_MINUTES 分のバッファを加味した拡張区間で判定。
        #      「15:00-15:30 event + 15:30 visit」のような無茶な詰め込みを防止。
        if events_by_staff and week_monday is not None and course.visits:
            if _has_event_overlap_with_buffer(
                staff_id=staff.staff_id,
                course=course,
                weekday=weekday,
                events_by_staff=events_by_staff,
                week_monday=week_monday,
            ):
                return HUNGARIAN_INFINITY

        # ---------- Q3 ハイブリッド: 直近 1 週は強制除外 ----------
        for weeks_ago, course_code, staff_id in history:
            if (
                weeks_ago <= ROTATION_EXCLUSION_WEEKS
                and course_code == course.course_code
                and staff_id == staff.staff_id
            ):
                return HUNGARIAN_INFINITY

        # ---------- Phase G-90: 距離はコストから撤去 ----------
        # スタッフは自拠点コースにのみ行く (上の拠点ハード制約). 同拠点内では
        # 距離 (km) は割付に無関係なので cost 項から除去した. ``_distance_km`` は
        # total_distance_km レポート用に残す (solve の集計で使用).

        # ---------- β: ローテーションペナルティ (ソフト) ----------
        # 直近 ROTATION_HISTORY_WEEKS 週で同一 course_code を担当した回数
        # (直近ほど重み大, weeks_ago=1 -> 重み 1.0, weeks_ago=4 -> 重み 0.25)
        rotation_count = 0.0
        for weeks_ago, course_code, staff_id in history:
            if course_code == course.course_code and staff_id == staff.staff_id:
                if weeks_ago <= ROTATION_EXCLUSION_WEEKS:
                    # ハード除外で既に処理済み
                    continue
                if weeks_ago > ROTATION_HISTORY_WEEKS:
                    continue
                weight = 1.0 / max(1, weeks_ago)
                rotation_count += weight

        # ---------- W16: 前日同コースペナルティ (ソフト) ----------
        # 前日と同じ (course_code, staff_id) を選ぶと大きなコストを足す.
        # ハード INF にしないのは「他に勤務可能なスタッフが居ない」場合の救済のため.
        prev_day_penalty = 0.0
        if (course.course_code, staff.staff_id) in prev_day_pairs:
            prev_day_penalty = COST_W16_PREV_DAY_SAME_COURSE

        # ---------- Phase 2: patient 中心ローテ段階ペナルティ ----------
        # course.patient_ids の各患者について、 その患者の直近担当者リスト
        # ``patient_recent_staff[pid]`` (最近順, index 0 = 1 回前) における
        # staff.staff_id の index を引き、 {0: RECENT_1, 1: _2, 2: _3} で penalty 化.
        # 段階ペナルティはいずれも HUNGARIAN_INFINITY 未満 = 「適任者ゼロなら埋める」
        # 段階的緩和を担保する (= 避けられる限り必ず避けるが、 尽きたら妥協して埋める).
        # 患者間は **max** (= 1 人でも該当すれば回避). 同一患者で複数 index にヒット
        # することは distinct リスト構造上ありえないが、 念のため最小 index (= 最大
        # penalty) を採用する.
        patient_rotation_penalty = 0.0
        for pid in course.patient_ids:
            recent = patient_recent_staff.get(pid)
            if not recent:
                continue
            cell_penalty = 0.0
            for idx, sid in enumerate(recent):
                if sid == staff.staff_id:
                    cell_penalty = _PATIENT_RECENT_PENALTY_BY_INDEX.get(idx, 0.0)
                    break  # 最小 index (= 最大 penalty) のみ採用
            patient_rotation_penalty = max(patient_rotation_penalty, cell_penalty)

        # ---------- Wave 5: 決定的ランダム rotation score ----------
        # 同じ patient に常に同じ staff が当たる現象を避ける。
        # 1 コース内で代表 patient (= min(patient_ids)) を seed に使う.
        # patient_ids が空のときは course_code を hash 化したダミー値を seed にする.
        # Wave 5 fix: DB 取得順依存を避けるため min() で決定的に最小 UUID を選ぶ.
        rotation_random = 0.0
        if iso_year is not None and iso_week is not None:
            seed_patient = min(course.patient_ids) if course.patient_ids else None
            rotation_random = _deterministic_random(
                iso_year=iso_year,
                iso_week=iso_week,
                patient_id=seed_patient,
                staff_id=staff.staff_id,
            )

        return (
            COST_BETA_ROTATION * rotation_count
            + prev_day_penalty
            + patient_rotation_penalty
            + rotation_random
        )

    def _distance_km(self, course: CourseAssignmentTarget, staff: StaffInfo) -> float:
        """主拠点 → コース重心の Haversine 距離 (km).

        Phase G-90: **本メソッドはコスト関数から撤去され、 現在は
        ``total_distance_km`` レポート集計用にのみ呼ばれる** (= 割付の最適化対象
        ではない). スタッフの拠点振り分けは ``effective_office_for_weekday`` の
        ハード制約が担う (距離は割付に無関係、というドメイン要件).

        座標欠損 (None) のときは ``DISTANCE_UNKNOWN_KM`` (= 12.0) を返す.

        応援 (primary 休業日に secondary へ転入) 時もレポート距離は常に
        ``staff.primary_office_lat`` / ``primary_office_lng`` ベースの近似値であり、
        実際の稼働拠点 (secondary) 座標には切り替えない (レポート用途のため許容).
        """
        if (
            course.centroid_lat is None
            or course.centroid_lng is None
            or staff.primary_office_lat is None
            or staff.primary_office_lng is None
        ):
            return DISTANCE_UNKNOWN_KM
        return haversine_km(
            staff.primary_office_lat,
            staff.primary_office_lng,
            course.centroid_lat,
            course.centroid_lng,
        )

    # ------------------------------------------------------------------ #
    # Phase G-29: manager fallback (2nd pass)
    # ------------------------------------------------------------------ #

    def _try_fallback_manager_for_course(
        self,
        *,
        course: CourseAssignmentTarget,
        free_managers: list[StaffInfo],
        weekday: int,
        events_by_staff: dict[UUID, list[StaffEvent]],
        week_monday: date_cls | None,
    ) -> StaffInfo | None:
        """1 つの unassigned course に対し best manager を返す.

        Phase G-29: 1st pass (Hungarian, manager 除外) で割当不能だったコースに
        対する 2nd pass の helper. 制約 (拠点 / work_days / 性別 / 当日 event 重複)
        を満たす manager のうち、 ``staff_id`` 昇順の決定的タイブレークで選ぶ。

        Phase G-90: 拠点ハード制約を追加し、 距離を選定基準から撤去した.
        manager も自拠点コースにのみ配置する (= 実効拠点 ≠ course 拠点なら skip).
        同拠点内では距離 (km) は割付に無関係なので、 選定キーを
        ``staff_id`` (UUID 文字列) 昇順の決定的タイブレークに変更した
        (旧 ``(distance, staff_id)`` を置換).

        Args:
            course: 割当対象の NULL コース.
            free_managers: まだ当該曜日で未使用の manager 候補リスト.
            weekday: 0=Mon..6=Sun.
            events_by_staff: ``staff_id -> [StaffEvent, ...]``. 重複判定用.
            week_monday: 当該週月曜日 (event 重複判定に必要).

        Returns:
            適合 manager (= ``StaffInfo``) または None (適合者なし).

        Notes:
            - 選定は ``staff_id`` (UUID) 文字列の昇順で決定的に安定化する.
              spec 上は ``staff.code`` 昇順だが ``StaffInfo`` に code フィールドが
              無いため代理として ``staff_id`` を用いる.
        """
        best_mgr: StaffInfo | None = None
        best_key: str | None = None

        for mgr in free_managers:
            # ---------- Phase G-90: 拠点ハード制約 ----------
            # manager も自拠点コースにのみ配置する (= 他拠点漏れ防止).
            # ``course.office_id is None`` (= 合成テスト fixture) では skip (後方互換).
            if course.office_id is not None:
                if mgr.effective_office_for_weekday(weekday) != course.office_id:
                    continue

            # ---------- 性別 check (ハード) ----------
            # 1st pass の ``_cost_single_cell`` と同じ AND semantics に統一.
            # Phase G-29 reviewer 指摘 HIGH-1: 元の OR semantics だと「female_only +
            # male_only 両方を含むコース」で manager が誤割当される hard 制約違反の
            # リスクがあった. Phase 1: ``_staff_satisfies_gender`` に単一ソース化.
            if not _staff_satisfies_gender(mgr, course):
                continue

            # ---------- 当日勤務 check (ハード, work_days) ----------
            if weekday not in mgr.work_days:
                continue

            # ---------- event 重複 check (ハード, W33 buffer) ----------
            if events_by_staff and week_monday is not None and course.visits:
                if _has_event_overlap_with_buffer(
                    staff_id=mgr.staff_id,
                    course=course,
                    weekday=weekday,
                    events_by_staff=events_by_staff,
                    week_monday=week_monday,
                ):
                    continue

            # ---------- Phase G-90: staff_id 昇順の決定的タイブレーク ----------
            # 同拠点内なので距離は無意味. staff_id (UUID 文字列) 昇順で安定選択.
            key = str(mgr.staff_id)
            if best_key is None or key < best_key:
                best_key = key
                best_mgr = mgr

        return best_mgr

    def _compute_gender_candidate_for_course(
        self,
        *,
        course: CourseAssignmentTarget,
        staff_pool: list[StaffInfo],
        weekday: int,
        events_by_staff: dict[UUID, list[StaffEvent]],
        week_monday: date_cls | None,
        fixed_staff_by_course: dict[UUID, UUID],
        history: list[tuple[int, str, UUID]],
        prev_day_pairs: set[tuple[str, UUID]],
        iso_year: int | None,
        iso_week: int | None,
        patient_recent_staff: dict[UUID, list[UUID]],
    ) -> StaffInfo | None:
        """Phase G-91: 性別ブロック course の「性別を無視したら誰が割り当たるか」候補.

        性別制限 (= ``_staff_satisfies_gender``) のため Layer 3 で未割当になった
        course について、 **性別制約だけを外した** ときの最小コスト same-office
        staff を 1 名 greedy に算出する (= 管理者が override 判断する材料).

        選定ロジック (architect 助言 Q2):
        - 固定対象 (都賀 A / manager M) ならその固定スタッフを候補にする
          (= 性別違反でも「管理者が override すべきか」 の判断材料として返す).
        - それ以外は「同拠点 (effective_office_for_weekday==course.office_id) +
          当日勤務 + event 重複なし」 の same-office staff のうち、 性別以外の
          コスト (= ``_cost_single_cell`` を性別 skip で算出) が最小の 1 名.
        - タイブレーク: cost 昇順 → 同コストは staff_id (UUID) 昇順で決定的に安定化.

        Hungarian 全体を gender 無効で再実行しないのは、 既に確定した他コースの
        結果を引きはがして整合性を壊すため (= レビュー候補は排他性不要、 単一
        course の greedy で十分).

        Returns:
            候補 ``StaffInfo`` または None (純粋人手不足 = 候補なし → レビュー対象外).
        """
        # ---------- 固定対象 (都賀 A / manager M) はその固定スタッフを候補に ----------
        fixed_staff_id = fixed_staff_by_course.get(course.course_id)
        if fixed_staff_id is not None:
            fixed_staff = next((s for s in staff_pool if s.staff_id == fixed_staff_id), None)
            if fixed_staff is not None and weekday in fixed_staff.work_days:
                return fixed_staff

        # ---------- 性別以外のハード制約を満たす same-office staff を greedy 評価 ----------
        best_staff: StaffInfo | None = None
        best_key: tuple[float, str] | None = None
        for staff in staff_pool:
            # manager は通常割付の候補にしない (M 固定 / fallback は別経路).
            if staff.role == "manager":
                continue
            # 当日勤務 (ハード)
            if weekday not in staff.work_days:
                continue
            # 拠点ハード制約 (= 自拠点コースのみ). 合成 fixture (office_id None) は skip.
            if course.office_id is not None:
                if staff.effective_office_for_weekday(weekday) != course.office_id:
                    continue
            # event 重複 (ハード, W33 buffer)
            if events_by_staff and week_monday is not None and course.visits:
                if _has_event_overlap_with_buffer(
                    staff_id=staff.staff_id,
                    course=course,
                    weekday=weekday,
                    events_by_staff=events_by_staff,
                    week_monday=week_monday,
                ):
                    continue
            # 性別「以外」のコスト. _cost_single_cell は gender INF を返し得るので、
            # gender_restrictions を空にした課題コースを使って性別だけ無効化する.
            course_no_gender = CourseAssignmentTarget(
                course_id=course.course_id,
                weekday=course.weekday,
                course_code=course.course_code,
                centroid_lat=course.centroid_lat,
                centroid_lng=course.centroid_lng,
                gender_restrictions=frozenset(),
                patient_ids=course.patient_ids,
                visits=course.visits,
                office_id=course.office_id,
            )
            cost = self._cost_single_cell(
                weekday=weekday,
                course=course_no_gender,
                staff=staff,
                history=history,
                prev_day_pairs=prev_day_pairs,
                events_by_staff=events_by_staff,
                week_monday=week_monday,
                iso_year=iso_year,
                iso_week=iso_week,
                patient_recent_staff=patient_recent_staff,
            )
            if cost >= HUNGARIAN_INFINITY:
                # 性別以外のハード制約 (= 履歴除外等) で不可 → 候補外.
                continue
            key = (cost, str(staff.staff_id))
            if best_key is None or key < best_key:
                best_key = key
                best_staff = staff
        return best_staff

    def _apply_manager_fallback(
        self,
        *,
        weekday: int,
        day_courses: list[CourseAssignmentTarget],
        staff_pool: list[StaffInfo],
        day_assignments: list[StaffAssignment],
        events_by_staff: dict[UUID, list[StaffEvent]],
        week_monday: date_cls | None,
    ) -> list[StaffAssignment]:
        """Phase G-29: 1st pass 後、 NULL コースに manager を greedy 配置する.

        User 要望: 「割り当ての人がいなかった場合に manager を配置する。 最初からの
        割り当てロジックの中に manager を混ぜない」 = 1st pass は通常 staff (+ 固定
        manager) のみ、 2nd pass で残った NULL コースに manager を fallback 配置する。

        Manager 配置条件 (全て満たす):
          1. role='manager' (= staff_pool に居る) かつ is_trainee=False
          2. 当日勤務 (weekday ∈ manager.work_days)
          3. 性別制限を満たす (``_normalize_sex_restriction`` で正規化後の集合に
             manager.sex が含まれる)
          4. 当日まだ他コース未担当 (= 1 staff 1 day 1 course を manager にも適用.
             1st pass で M コース固定担当の manager は 2nd pass で再利用しない)
          5. StaffEvent 時間帯重複なし (``_has_event_overlap_with_buffer``)
          6. Phase G-90: 拠点ハード制約 (= 実効拠点 == course 拠点). 詳細は
             ``_try_fallback_manager_for_course`` 参照.

        複数 NULL コースがある場合は greedy: コースを 1 件ずつ巡回し、 残存
        manager から best (= staff_id 昇順の決定的タイブレーク) を選ぶ。 1 度
        割当てた manager は free_managers から除外する (= 1 day 1 course 制約).

        Args:
            weekday: 0=Mon..6=Sun.
            day_courses: 当該曜日の全コース.
            staff_pool: 全スタッフ (= manager 含む生の pool).
            day_assignments: 1st pass の結果 (= solve_one_day の戻り値).
            events_by_staff: ``staff_id -> [StaffEvent, ...]``.
            week_monday: 当該週月曜日 (event 重複判定用).

        Returns:
            1st pass + 2nd pass を統合した最終 assignment list.

        Notes:
            - Phase G-90: 拠点ハード制約 + staff_id 昇順の決定的タイブレークで判定.
              ローテ履歴 / W16 連続防止 / Wave 5 同患者ペナルティは 2nd pass では
              考慮しない (= 「割当不能な救済」 という性質上、 副次的最適化より
              配置できることを優先).
        """
        if not day_courses or not staff_pool:
            return day_assignments

        assigned_course_ids: set[UUID] = {a.course_id for a in day_assignments}
        assigned_staff_ids: set[UUID] = {a.staff_id for a in day_assignments}

        # NULL のままのコース
        unassigned = [c for c in day_courses if c.course_id not in assigned_course_ids]
        if not unassigned:
            return day_assignments

        # fallback 候補 manager: role='manager', is_trainee=False,
        # 当日他コース未担当 (= 1st pass で固定 M を担当した manager は除外).
        # work_days / 性別 / event は course 個別に判定するため
        # ``_try_fallback_manager_for_course`` 内で行う。
        free_managers = [
            s
            for s in staff_pool
            if s.role == "manager" and not s.is_trainee and s.staff_id not in assigned_staff_ids
        ]
        if not free_managers:
            return day_assignments

        # greedy: 各 unassigned course に対し best manager を 1 件ずつ確定し、
        # 確定した manager を free_managers から除く. 同一 course を複数 manager に
        # 割り当てない (1 day 1 course 制約) + 同一 manager を複数 course に割り
        # 当てない (1 staff 1 day 1 course 制約).
        # コース処理順は day_courses の元順 (= _load_course_targets の order_by
        # weekday, code に従う) で決定論的.
        result = list(day_assignments)
        for course in unassigned:
            if not free_managers:
                break
            best_mgr = self._try_fallback_manager_for_course(
                course=course,
                free_managers=free_managers,
                weekday=weekday,
                events_by_staff=events_by_staff,
                week_monday=week_monday,
            )
            if best_mgr is None:
                continue
            result.append(
                StaffAssignment(
                    weekday=weekday,
                    course_code=course.course_code,
                    course_id=course.course_id,
                    staff_id=best_mgr.staff_id,
                )
            )
            free_managers.remove(best_mgr)

        return result

    def _gini_index(self, items: list[Any], *, staff_count: int) -> float:
        """ローテ分散度を Gini 係数で表現する.

        各スタッフの担当回数の不均等度を 0.0 (完全均等) 〜 1.0 (1 人独占) で返す。
        受入基準: naive round-robin 以下。
        """
        if not items:
            return 0.0
        counts = Counter(items)
        # 担当ゼロのスタッフも分散度評価に含める (= staff_count 全員を母集団とする)
        values = list(counts.values())
        # 担当ゼロのスタッフ数だけ 0 を追加
        zero_staff = max(0, staff_count - len(counts))
        values.extend([0] * zero_staff)
        n = len(values)
        if n == 0:
            return 0.0
        s = sum(values)
        if s == 0:
            return 0.0
        values.sort()
        # Gini = (2 * Σ(i * x_i) ) / (n * Σx) - (n + 1) / n   (1-based i)
        cum_idx = sum((i + 1) * v for i, v in enumerate(values))
        gini = (2.0 * cum_idx) / (n * s) - (n + 1.0) / n
        return max(0.0, gini)

    # ------------------------------------------------------------------ #
    # private: DB I/O
    # ------------------------------------------------------------------ #

    async def _load_course_targets(
        self,
        db: AsyncSession,
        *,
        iso_year: int,
        iso_week: int,
        office_id: UUID | None = None,
        include_manager_courses: bool = True,
    ) -> list[CourseAssignmentTarget]:
        """確定済みコース (course_status='course_fixed') をロードして対象に変換.

        各コースの重心は所属する visits の患者 lat/lng の平均で算出。
        性別制限はコース内全患者の sex_restriction 集合。

        Args:
            office_id: 指定時は当該拠点のコースに絞る (W16).
            include_manager_courses: W16 — True のとき code='M' も対象に含める
                (manager 固定割当のため). False で旧挙動 (M を除外).

        Phase G-28 fix: ``patient_ids`` が空になるコース (= 所属 visits が
        0 件のコース) を target list から除外する。 ハンガリアン法の入力に
        含まれると staff_pool を圧迫し、 患者ありコースが NULL のまま残る
        本番バグ (W21 で 5/35 件 NULL) の原因となるため、 visit 取得を
        先頭に移動して early-skip する。
        """
        # CareFlow バグ修正 (Layer 3 staff_assigned 拾い漏れ): assign-staff-only
        # で ``course_status='course_fixed'`` のみ処理対象にしていたため、
        # auto_allocator_v2 由来の ``'staff_assigned'`` コースが完全スルー
        # されていた (= 自動割付ボタン押下で 15/112 件のみ割付される本質バグ).
        # ``COURSE_STATUS_STAFF_ASSIGNED`` も対象に含めることで再割付/再評価を
        # 正しく実行する. 既存 assigned_staff_id の保護は ``run`` 内
        # ``already_assigned_stmt`` (line 429-) が ``fixed_staff_by_course`` に
        # 追加し、Layer 3 内「固定スタッフ除外」ロジックで担保する (W25 fix).
        where_clauses = [
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.deleted_at.is_(None),
            Course.course_status.in_([COURSE_STATUS_COURSE_FIXED, COURSE_STATUS_STAFF_ASSIGNED]),
        ]
        if not include_manager_courses:
            where_clauses.append(Course.code != "M")  # マネージャー枠は対象外 (§3.6.5)
        if office_id is not None:
            where_clauses.append(Course.office_id == office_id)

        stmt = select(Course).where(*where_clauses).order_by(Course.weekday, Course.code)
        courses = (await db.scalars(stmt)).all()

        targets: list[CourseAssignmentTarget] = []
        for course in courses:
            # Phase G-28 fix: コース所属 visits を先頭で取得し、 0 件のコースは
            # staff 割当対象から除外する (= 空コースに staff を縛ると
            # staff_pool が圧迫され、 患者ありコースが NULL になる本番バグ回避).
            # W27 Phase A: コース所属 visit の時間帯一覧 (event 重複判定用)
            visit_stmt = select(Visit).where(
                Visit.course_id == course.id,
                Visit.status == VISIT_STATUS_PLANNED,
                Visit.deleted_at.is_(None),
            )
            course_visit_rows = list((await db.scalars(visit_stmt)).all())
            if not course_visit_rows:
                # visits=0 のコースは patient_ids が空になりハンガリアン法に
                # 含めても意味がない (W21 本番 5/35 NULL の根本原因).
                continue
            visit_slots = [
                VisitTimeSlot(start_time=v.start_time, end_time=v.end_time)
                for v in course_visit_rows
            ]

            # コース所属 visits → patient_id → patient (lat/lng/sex_restriction)
            patient_stmt = (
                select(Patient)
                .join(Visit, Visit.patient_id == Patient.id)
                .where(
                    Visit.course_id == course.id,
                    Visit.status == VISIT_STATUS_PLANNED,
                    Visit.deleted_at.is_(None),
                    Patient.deleted_at.is_(None),
                )
            )
            patients = list((await db.scalars(patient_stmt)).all())

            # 重心算出
            lats = [float(p.lat) for p in patients if p.lat is not None]
            lngs = [float(p.lng) for p in patients if p.lng is not None]
            centroid_lat = sum(lats) / len(lats) if lats else None
            centroid_lng = sum(lngs) / len(lngs) if lngs else None

            # 性別制限集合
            restrictions: set[str] = set()
            for p in patients:
                if p.sex_restriction:
                    restrictions.add(p.sex_restriction)

            targets.append(
                CourseAssignmentTarget(
                    course_id=course.id,
                    weekday=course.weekday,
                    course_code=course.code,
                    centroid_lat=centroid_lat,
                    centroid_lng=centroid_lng,
                    gender_restrictions=frozenset(restrictions),
                    patient_ids=[p.id for p in patients],
                    visits=visit_slots,
                    # Phase G-90: 拠点ハード制約用に所属拠点 ID を伝搬する.
                    office_id=course.office_id,
                )
            )
        return targets

    async def load_active_staff(
        self,
        db: AsyncSession,
        *,
        iso_year: int,
        iso_week: int,
        week_monday: date_cls,
    ) -> list[StaffInfo]:
        """稼働スタッフ + 主拠点座標 + 勤務曜日 を取得.

        H2 (review) で ``_load_active_staff`` から public 化 (W41).
        auto_allocator が直接呼び出して staff_pool を構築できる.

        - ``Staff.status='active'`` のみ
        - ``StaffShift`` から ``is_on=True`` の曜日集合を構築
        - ``StaffWeeklyOverride`` (override_type='off') があれば当該曜日を除外

        Phase G-45: ``StaffSecondaryOffice`` を staff ごとにロードし、
        各 office の ``operating_weekdays`` を ``StaffInfo`` に持たせる. これにより
        ``StaffInfo.effective_office_for_weekday(wd)`` が primary 休業日の
        secondary 転入を判定できる.
        """
        # スタッフ + 主拠点 (Office) を取得
        stmt = (
            select(Staff, Office)
            .outerjoin(Office, Office.id == Staff.primary_office_id)
            .where(
                Staff.status == "active",
                Staff.deleted_at.is_(None),
            )
        )
        rows = (await db.execute(stmt)).all()

        # シフト (固定週次) 取得
        staff_ids = [s.id for s, _ in rows]
        if not staff_ids:
            return []
        shift_stmt = select(StaffShift).where(StaffShift.staff_id.in_(staff_ids))
        shifts = list((await db.scalars(shift_stmt)).all())
        shift_map: dict[UUID, set[int]] = {}
        for sh in shifts:
            if sh.is_on:
                shift_map.setdefault(sh.staff_id, set()).add(sh.weekday)

        # 当該週の override を取得 (off は除外, custom_time は曜日 ON とみなす)
        override_stmt = select(StaffWeeklyOverride).where(
            and_(
                StaffWeeklyOverride.iso_year == iso_year,
                StaffWeeklyOverride.iso_week == iso_week,
                StaffWeeklyOverride.staff_id.in_(staff_ids),
            )
        )
        overrides = list((await db.scalars(override_stmt)).all())
        off_days: dict[UUID, set[int]] = {}
        on_days_override: dict[UUID, set[int]] = {}
        for ov in overrides:
            if ov.override_type == "off":
                off_days.setdefault(ov.staff_id, set()).add(ov.weekday)
            elif ov.override_type == "custom_time":
                on_days_override.setdefault(ov.staff_id, set()).add(ov.weekday)

        # Phase G-45: secondary_offices を staff_id ごとに集約 (1 query).
        # ORDER BY (staff_id, office_id) で deterministic 順序を保証する
        # (= fallback で先頭 secondary を選ぶため UUID 昇順で安定化).
        sec_rows = list(
            (
                await db.scalars(
                    select(StaffSecondaryOffice)
                    .where(StaffSecondaryOffice.staff_id.in_(staff_ids))
                    .order_by(
                        StaffSecondaryOffice.staff_id,
                        StaffSecondaryOffice.office_id,
                    )
                )
            ).all()
        )
        secondaries_by_staff: dict[UUID, list[UUID]] = {}
        for sec in sec_rows:
            secondaries_by_staff.setdefault(sec.staff_id, []).append(sec.office_id)

        # Phase G-45: 全 office (primary + secondary) の operating_weekdays を bulk fetch.
        candidate_office_ids: set[UUID] = set()
        for staff, _office in rows:
            if staff.primary_office_id is not None:
                candidate_office_ids.add(staff.primary_office_id)
            for oid in secondaries_by_staff.get(staff.id, []):
                candidate_office_ids.add(oid)
        op_weekdays_by_office: dict[UUID, frozenset[int]] = {}
        if candidate_office_ids:
            op_rows = (
                await db.execute(
                    select(Office.id, Office.operating_weekdays).where(
                        Office.id.in_(list(candidate_office_ids))
                    )
                )
            ).all()
            for oid, raw in op_rows:
                wds = _coerce_office_operating_weekdays(raw)
                op_weekdays_by_office[oid] = frozenset(wds)

        result: list[StaffInfo] = []
        for staff, office in rows:
            base_days = shift_map.get(staff.id, set())
            # override で off になっている曜日を除外 / custom_time で追加
            effective = (base_days | on_days_override.get(staff.id, set())) - off_days.get(
                staff.id, set()
            )
            sec_ids = tuple(secondaries_by_staff.get(staff.id, []))
            # 当 staff に関連する office のみ map に詰める (コピー).
            staff_op_map: dict[UUID, frozenset[int]] = {}
            if staff.primary_office_id is not None:
                pop = op_weekdays_by_office.get(staff.primary_office_id)
                if pop is not None:
                    staff_op_map[staff.primary_office_id] = pop
            for oid in sec_ids:
                pop = op_weekdays_by_office.get(oid)
                if pop is not None:
                    staff_op_map[oid] = pop
            result.append(
                StaffInfo(
                    staff_id=staff.id,
                    name=staff.name,
                    sex=staff.sex,
                    role=staff.role,
                    primary_office_lat=(
                        float(office.lat) if office is not None and office.lat is not None else None
                    ),
                    primary_office_lng=(
                        float(office.lng) if office is not None and office.lng is not None else None
                    ),
                    work_days=frozenset(effective),
                    is_trainee=staff.is_trainee,
                    primary_office_id=staff.primary_office_id,
                    secondary_office_ids=sec_ids,
                    office_operating_weekdays=staff_op_map,
                )
            )
        return result

    async def _load_patient_recent_staff(
        self,
        db: AsyncSession,
        *,
        week_monday: date_cls,
    ) -> dict[UUID, list[UUID]]:
        """Phase 2: 患者ごとの「直近担当者リスト」 (最近順) を返す.

        ``week_monday`` より前の過去 ``PATIENT_ROTATION_LOOKBACK_WEEKS`` 週分の visit を
        遡り、 患者ごとに **新しい順** で distinct な staff_id を最大
        ``PATIENT_RECENT_DEPTH`` 件まで並べた list を返す (index 0 = 最も最近).

        旧 ``_load_patient_visit_history`` (前日/前々日/前週金土の3マップ) を置換する.
        「前日/前々日/前週金土」 の継ぎ接ぎでは 3 日差 (月→金) や週またぎ同曜日に穴が
        あったが、 本ローダーは「直近 N 回」 を週・曜日横断で一元的に捉える.

        Returns:
            ``{patient_id: [staff_id, ...]}`` (最近順, 最大 ``PATIENT_RECENT_DEPTH`` 件).

        Notes:
            - 対象は ``week_monday`` **より前** の visit のみ (= 当該週は含めない.
              当該週内の伝搬は ``solve()`` の working_recent prepend が担う).
            - ``visit_staff_assignments`` JOIN ``Visit`` JOIN ``Course`` で、
              ``course_status == 'staff_assigned'`` のコースのみに限定する
              (= 前回 Layer 3 実行の残骸 (course_fixed) VSA を除外し re-run swing を回避).
            - 並びは (visit_date desc, start_time desc) で新しい順に走査し、
              患者ごとに初出の staff_id を順に DEPTH 件まで採用する.
            - 同時刻同患者の複数 staff (required_staff_count>1 等) は staff_id 昇順で安定化
              (= tie-break を全順序化し、 DB 物理行順依存の再実行 swing を防ぐ).
        """
        lookback_start = week_monday - timedelta(weeks=PATIENT_ROTATION_LOOKBACK_WEEKS)

        stmt = (
            select(
                Visit.patient_id,
                Visit.visit_date,
                Visit.start_time,
                VisitStaffAssignment.staff_id,
            )
            .join(VisitStaffAssignment, VisitStaffAssignment.visit_id == Visit.id)
            .join(Course, Course.id == Visit.course_id)
            .where(
                Visit.visit_date >= lookback_start,
                Visit.visit_date < week_monday,
                Visit.deleted_at.is_(None),
                Course.course_status == COURSE_STATUS_STAFF_ASSIGNED,
            )
            .order_by(
                Visit.visit_date.desc(),
                Visit.start_time.desc(),
                VisitStaffAssignment.staff_id.asc(),
            )
        )
        rows = (await db.execute(stmt)).all()

        recent: dict[UUID, list[UUID]] = {}
        for patient_id, _visit_date, _start_time, staff_id in rows:
            if patient_id is None or staff_id is None:
                continue
            staff_list = recent.setdefault(patient_id, [])
            if len(staff_list) >= PATIENT_RECENT_DEPTH:
                continue
            if staff_id in staff_list:
                continue  # distinct 維持 (= 同 staff の複数回は最も最近の 1 回のみ)
            staff_list.append(staff_id)

        return recent

    async def _load_staff_events(
        self,
        db: AsyncSession,
        *,
        staff_ids: list[UUID],
        week_monday: date_cls,
    ) -> dict[UUID, list[StaffEvent]]:
        """W27 Phase A: 当該週 (月曜〜日曜) に存在する StaffEvent を staff 別に取得.

        重なり判定の正確性のため、event 区間が週末をまたぐケースも拾えるよう、
        ``starts_at < (week_sunday+1)`` かつ ``ends_at >= week_monday`` で SELECT する.

        ``staff_ids`` が空のときは空 dict を返す.
        """
        if not staff_ids:
            return {}
        week_sunday_plus1 = week_monday + timedelta(days=7)
        # naive datetime で比較する (DB 側 tz の有無に依らず動作させるため
        # SQLAlchemy が tz を付与/剥離するハンドリングに任せる).
        # SQLite (テスト) と PostgreSQL (本番) いずれも naive 値を渡せば
        # column type に合わせた比較に変換される.
        range_start = datetime.combine(week_monday, time_cls(0, 0))
        range_end = datetime.combine(week_sunday_plus1, time_cls(0, 0))
        stmt = select(StaffEvent).where(
            StaffEvent.staff_id.in_(staff_ids),
            StaffEvent.starts_at < range_end,
            StaffEvent.ends_at >= range_start,
        )
        rows = list((await db.scalars(stmt)).all())
        result: dict[UUID, list[StaffEvent]] = {}
        for ev in rows:
            result.setdefault(ev.staff_id, []).append(ev)
        return result

    @staticmethod
    async def _count_planned_visits_by_courses(
        db: AsyncSession, course_ids: list[UUID]
    ) -> dict[UUID, int]:
        """指定 course の planned visit 数を course_id 別に返す helper.

        Phase G-28 fix: 0 件のコースを Layer 3 / W25 fix から除外する判定で
        複数経路 (manager 固定 / 都賀 staff 固定 / W25 already_assigned fix) で
        使われるため共通化.

        Args:
            db: 共有 AsyncSession.
            course_ids: 集計対象 course の id リスト. 空ならクエリ skip.

        Returns:
            ``{course_id: planned visit count}`` の dict. visit 0 件のコースは
            返り値に含まれない (= ``.get(cid, 0)`` で 0 扱いするのが定石).
        """
        if not course_ids:
            return {}
        rows = (
            await db.execute(
                select(Visit.course_id, func.count(Visit.id))
                .where(
                    Visit.course_id.in_(course_ids),
                    Visit.status == VISIT_STATUS_PLANNED,
                    Visit.deleted_at.is_(None),
                )
                .group_by(Visit.course_id)
            )
        ).all()
        return {cid: cnt for cid, cnt in rows}

    @staticmethod
    async def _load_gender_restrictions_by_courses(
        db: AsyncSession, course_ids: list[UUID]
    ) -> dict[UUID, frozenset[str]]:
        """指定 course の所属患者 ``sex_restriction`` 集合を course_id 別に返す helper.

        Phase 1: ``_build_fixed_assignments`` で固定割当 (manager→M / 都賀→A) を
        貼る前に性別ハード制約を多重防御チェックするために使う. ``_load_course_targets``
        と同じく planned visit 経由で患者を辿り、 非 NULL の ``sex_restriction`` を集約する.

        Returns:
            ``{course_id: frozenset[restriction]}``. 制限のないコースは空集合を持つ
            (= キーは存在し ``.get(cid, frozenset())`` で空扱い).
        """
        if not course_ids:
            return {}
        rows = (
            await db.execute(
                select(Visit.course_id, Patient.sex_restriction)
                .join(Patient, Patient.id == Visit.patient_id)
                .where(
                    Visit.course_id.in_(course_ids),
                    Visit.status == VISIT_STATUS_PLANNED,
                    Visit.deleted_at.is_(None),
                    Patient.deleted_at.is_(None),
                )
            )
        ).all()
        out: dict[UUID, set[str]] = {}
        for cid, restriction in rows:
            bucket = out.setdefault(cid, set())
            if restriction:
                bucket.add(restriction)
        return {cid: frozenset(vals) for cid, vals in out.items()}

    @staticmethod
    async def _load_l3_fix_primary_staff_offices(
        db: AsyncSession,
        *,
        office_ids: list[UUID],
    ) -> set[UUID]:
        """Wave N-1: ``l3_fix_primary_staff`` feature flag が enabled な拠点集合を返す.

        ``OfficeFeatureFlag.enabled_at IS NOT NULL`` の office のみ、 primary staff
        (active role=staff の code 昇順先頭 1 名) の A コース固定割当を発動する
        (旧「都賀」拠点名ハードコードの設定化). enabled_at IS NULL は「未有効化」
        として固定割当を発動しない.

        ``auto_allocator_v2._load_g21_enabled_offices`` と同一の照会パターン.
        """
        if not office_ids:
            return set()
        rows = await db.scalars(
            select(OfficeFeatureFlag.office_id).where(
                OfficeFeatureFlag.office_id.in_(office_ids),
                OfficeFeatureFlag.feature_key == L3_FIX_PRIMARY_STAFF_FEATURE_KEY,
                OfficeFeatureFlag.enabled_at.is_not(None),
            )
        )
        return set(rows.all())

    async def _build_fixed_assignments(
        self,
        db: AsyncSession,
        *,
        iso_year: int,
        iso_week: int,
        office_id: UUID | None = None,
    ) -> dict[UUID, UUID]:
        """W16: 固定割当 (manager -> M / primary staff -> A) を構築する.

        ロジック:
        1. role='manager' かつ status='active' の各スタッフ
           → そのスタッフの primary_office で M / M2 / .. の course (course_fixed)
              に当該スタッフを 1:1 で割り当てる
           → ラベル並びは M < M2 < M3 < ... の順 (= staff.code 昇順 + manager 順)
        2. OfficeFeatureFlag ``l3_fix_primary_staff`` が enabled_at IS NOT NULL の
           office の active staff (role='staff')
           → 当該 office の code='A' course にスタッフを 1 名固定
              (複数人居る場合は staff.code 昇順で先頭の 1 名)

        Wave N-1: step 2 の対象拠点判定を「拠点名に '都賀' を含む」ハードコードから
        OfficeFeatureFlag ``l3_fix_primary_staff`` (enabled_at IS NOT NULL) に置き換えた
        (マルチテナント対応 = 他社事業所展開でも拠点名に依存せず設定で発動).
        旧「都賀」拠点は migration 0051 で本フラグを INSERT し現行挙動を保存する.
        スタッフ選定ロジック (active role=staff の code 昇順先頭 1 名) は不変.

        Phase G-26 fix: 対象 course の status 条件は
        ``course_fixed`` だけでなく ``staff_assigned`` も含める.
        「一斉未割当」 ボタン押下後は全コースが
        ``course_status='staff_assigned' / assigned_staff_id=NULL`` 状態になる
        ため、 旧条件 (course_fixed のみ) では固定割当辞書が空になり
        manager → M / 都賀 staff → 都賀 A のルールが全く効かない不具合があった.
        (``_load_course_targets`` line 1071 は既に同種修正済だったが、 本関数への伝播漏れ.
         VPS 本番 DB 35 件中 10 件 NULL + manager 0 件 割当の症状で発覚.)

        Phase G-26 safe-guard: SQL fetch 後に Python 側で
        「assigned_staff_id が NULL もしくは 該当 manager / primary_staff の id」
        のコースだけに絞り込み、 admin が UI から手動で別 staff を
        割り当てた M / 都賀 A コースを上書きしないようにする
        (W25 admin 手動割付保護 = ``already_assigned_stmt`` との整合).

        Phase G-28 fix: M / 都賀 A コースでも、 所属 visits が 0 件のものは
        固定対象から除外する。 空コースに manager / 都賀 staff を縛ると
        staff_pool が無駄に消費され、 他コースが NULL になるため
        (``_load_course_targets`` の空コース skip と整合).

        Returns:
            ``{course_id: staff_id}`` の dict.
        """
        result: dict[UUID, UUID] = {}

        # ---------- 対象拠点を取得 ----------
        office_stmt = select(Office).where(Office.deleted_at.is_(None))
        if office_id is not None:
            office_stmt = office_stmt.where(Office.id == office_id)
        offices = list((await db.scalars(office_stmt)).all())
        offices_by_id: dict[UUID, Office] = {o.id: o for o in offices}

        # Wave N-1: primary staff 固定割当 (旧「都賀」) の対象拠点を feature flag で判定.
        fix_primary_staff_office_ids = await self._load_l3_fix_primary_staff_offices(
            db, office_ids=[o.id for o in offices]
        )

        # ---------- 1) manager 固定割当 ----------
        for office in offices:
            mgr_stmt = (
                select(Staff)
                .where(
                    Staff.status == "active",
                    Staff.role == "manager",
                    Staff.deleted_at.is_(None),
                    Staff.primary_office_id == office.id,
                )
                .order_by(Staff.code.asc().nulls_last(), Staff.created_at.asc())
            )
            managers = list((await db.scalars(mgr_stmt)).all())

            # M / M2 / .. の course (course_fixed) を取得
            # Phase G-26 fix: assign-staff-only 再実行時に staff_assigned 状態 (= 一斉未割当後) でも固定割当が効くよう両 status を対象に含める
            mgr_course_stmt = (
                select(Course)
                .where(
                    Course.iso_year == iso_year,
                    Course.iso_week == iso_week,
                    Course.deleted_at.is_(None),
                    Course.course_status.in_(
                        [COURSE_STATUS_COURSE_FIXED, COURSE_STATUS_STAFF_ASSIGNED]
                    ),
                    Course.office_id == office.id,
                    Course.code == "M",
                )
                .order_by(Course.weekday)
            )
            mgr_courses = list((await db.scalars(mgr_course_stmt)).all())

            # Phase G-26 safe-guard: admin が手動で別 staff (= manager 以外) を割当済の
            # M course は保護し、 manager で上書きしない (W25 fix との整合).
            # 旧 SQL は course_status='staff_assigned' を含めるようになったため、
            # admin が UI から手動変更した「別 staff 割付済の M」 も拾ってしまい、
            # W25 の admin 手動割付保護 (already_assigned_stmt) と競合する。
            # 「assigned_staff_id が NULL もしくは manager のいずれかの id」 に絞る.
            manager_ids = {m.id for m in managers}
            mgr_courses = [
                c
                for c in mgr_courses
                if c.assigned_staff_id is None or c.assigned_staff_id in manager_ids
            ]

            # Phase G-28: visits=0 の M コースは固定対象から除外 (空コースに
            # manager を縛ると無駄に staff_pool を消費して他コースが未割当に
            # なるため). visit 数は ``_count_planned_visits_by_courses`` helper
            # (Phase G-28 H1 fix で共通化) で集計し、 0 件のコースを Python 側で filter.
            if mgr_courses:
                mgr_visit_count_by_course = await self._count_planned_visits_by_courses(
                    db, [c.id for c in mgr_courses]
                )
                mgr_courses = [c for c in mgr_courses if mgr_visit_count_by_course.get(c.id, 0) > 0]

            # Phase 1: 固定割当を貼る前に性別ハード制約をチェック (多重防御).
            # 違反するペア (= 例: 女性のみ患者コース × 男性 manager) は result に
            # 入れず通常割付に回す. ``_solve_one_day`` 側のガードと二重防御になる.
            mgr_restrictions = await self._load_gender_restrictions_by_courses(
                db, [c.id for c in mgr_courses]
            )

            # NOTE: code 列は ('A','B','C','D','E','M') CHECK 制約があるため
            # 複数 manager の場合も全て code='M' で運用される (W16 想定 N=1).
            # 同 (year, week, weekday) 内に M course が複数あればラベル順に
            # 1 manager ずつ割当てる.
            # 曜日ごとに (course_id) のリストを構築
            by_weekday: dict[int, list[Course]] = {}
            for c in mgr_courses:
                by_weekday.setdefault(c.weekday, []).append(c)
            for _weekday, day_courses in by_weekday.items():
                for idx, course in enumerate(day_courses):
                    if idx >= len(managers):
                        break
                    mgr = managers[idx]
                    if not _sex_satisfies_restrictions(
                        mgr.sex, mgr_restrictions.get(course.id, frozenset())
                    ):
                        continue
                    result[course.id] = mgr.id

        # ---------- 2) primary staff 固定割当 (旧「都賀」= l3_fix_primary_staff flag) ----------
        # Wave N-1: 拠点名 '都賀' ハードコードを OfficeFeatureFlag に置換.
        # フラグ enabled の拠点でのみ、 primary staff (active role=staff の code 昇順
        # 先頭 1 名) を A コースに固定する (選定ロジック自体は不変).
        for office in offices:
            if office.id not in fix_primary_staff_office_ids:
                continue
            tsuga_stmt = (
                select(Staff)
                .where(
                    Staff.status == "active",
                    Staff.role == "staff",
                    Staff.deleted_at.is_(None),
                    Staff.primary_office_id == office.id,
                )
                .order_by(Staff.code.asc().nulls_last(), Staff.created_at.asc())
            )
            tsuga_staff = list((await db.scalars(tsuga_stmt)).all())
            if not tsuga_staff:
                continue
            primary_staff = tsuga_staff[0]

            # Phase G-26 fix: assign-staff-only 再実行時に staff_assigned 状態 (= 一斉未割当後) でも固定割当が効くよう両 status を対象に含める
            tsuga_course_stmt = (
                select(Course)
                .where(
                    Course.iso_year == iso_year,
                    Course.iso_week == iso_week,
                    Course.deleted_at.is_(None),
                    Course.course_status.in_(
                        [COURSE_STATUS_COURSE_FIXED, COURSE_STATUS_STAFF_ASSIGNED]
                    ),
                    Course.office_id == office.id,
                    Course.code == "A",
                )
                .order_by(Course.weekday)
            )
            tsuga_courses = list((await db.scalars(tsuga_course_stmt)).all())

            # Phase G-26 safe-guard: admin が手動で別 staff (= primary_staff 以外) を割当済の
            # 固定対象 A course は保護し、 primary_staff で上書きしない (W25 fix との整合).
            # 「assigned_staff_id が NULL もしくは primary_staff.id」 に絞る.
            tsuga_courses = [
                c
                for c in tsuga_courses
                if c.assigned_staff_id is None or c.assigned_staff_id == primary_staff.id
            ]

            # Phase G-28: visits=0 の固定対象 A コースは固定対象から除外 (空コースに
            # primary_staff を縛ると本人が無駄に消費され、 他拠点の他曜日 staff_pool
            # が圧迫されるため. ``_load_course_targets`` の空コース skip と整合).
            # Phase G-28 H1 fix: ヘルパー ``_count_planned_visits_by_courses`` で共通化.
            if tsuga_courses:
                tsuga_visit_count_by_course = await self._count_planned_visits_by_courses(
                    db, [c.id for c in tsuga_courses]
                )
                tsuga_courses = [
                    c for c in tsuga_courses if tsuga_visit_count_by_course.get(c.id, 0) > 0
                ]

            # Phase 1: primary staff A 固定割当も性別ハード制約をチェック (多重防御).
            # 違反するペアは result に入れず通常割付に回す.
            tsuga_restrictions = await self._load_gender_restrictions_by_courses(
                db, [c.id for c in tsuga_courses]
            )
            for course in tsuga_courses:
                if not _sex_satisfies_restrictions(
                    primary_staff.sex, tsuga_restrictions.get(course.id, frozenset())
                ):
                    continue
                result[course.id] = primary_staff.id

        # offices_by_id は将来拡張用 (mypy 警告抑止のため軽く参照)
        del offices_by_id

        return result

    async def _load_rotation_history(
        self,
        db: AsyncSession,
        *,
        iso_year: int,
        iso_week: int,
        history_weeks: int,
    ) -> list[tuple[int, str, UUID]]:
        """過去 ``history_weeks`` 週の (course_code, staff_id) 履歴を返す.

        対象は ``course_status='staff_assigned'`` のコースのみ。
        ``weeks_ago`` は当該週との距離 (1 = 直前の週).

        ISO 週の境界をまたぐ年差を考慮するため、月曜日ベースの date 演算で
        history_weeks 分の (year, week) ペアを構築する。
        """
        try:
            cur_monday = date_cls.fromisocalendar(iso_year, iso_week, 1)
        except ValueError:
            return []

        # 過去 history_weeks 週分の (iso_year, iso_week, weeks_ago) を組み立て
        target_weeks: list[tuple[int, int, int]] = []
        for w in range(1, history_weeks + 1):
            past_monday = date_cls.fromordinal(cur_monday.toordinal() - 7 * w)
            iso = past_monday.isocalendar()
            target_weeks.append((iso.year, iso.week, w))

        if not target_weeks:
            return []

        # OR 連結 (year=Y AND week=W) ... の集合
        from sqlalchemy import or_

        stmt = select(Course).where(
            Course.deleted_at.is_(None),
            Course.course_status == COURSE_STATUS_STAFF_ASSIGNED,
            Course.assigned_staff_id.isnot(None),
            or_(*[and_(Course.iso_year == y, Course.iso_week == w) for y, w, _ in target_weeks]),
        )
        rows = list((await db.scalars(stmt)).all())

        # weeks_ago マップ
        wa_map = {(y, w): wa for y, w, wa in target_weeks}
        result: list[tuple[int, str, UUID]] = []
        for c in rows:
            wa = wa_map.get((c.iso_year, c.iso_week))
            if wa is None:
                continue
            if c.assigned_staff_id is None:
                continue
            result.append((wa, c.code, c.assigned_staff_id))
        return result

    async def _persist(
        self,
        db: AsyncSession,
        assignments: list[StaffAssignment],
    ) -> None:
        """割当結果を DB に反映する (W7-BE4 / Codex Must-fix #7).

        実施内容:
            1. ``courses.assigned_staff_id`` / ``course_status`` を更新
               (``staff_assigned`` 遷移)
            2. **当該コース配下の planned visits** を取得し、v2 正規表現で
               ある ``visit_staff_assignments`` に行を INSERT
               (Staff の visit 可視性は本テーブル経由)
            3. 2 名体制 (``required_staff_count == 2`` または ``visit_group_id``
               が設定済み) の場合は primary + secondary の 2 行を INSERT
               (secondary は同じ ``visit_group_id`` グループ内の partner visit
               の所属 course の ``assigned_staff_id`` から導出)
            4. レガシー互換のため visits.primary_staff_id / secondary_staff_id
               も同期更新 (Wave 6 まで併用)
            5. 冪等性: 既存の ``visit_staff_assignments`` 行は DELETE してから
               再 INSERT する

        本サービスは commit しない。呼び出し側がトランザクション境界を握る
        (§5.4 / module docstring)。
        """
        if not assignments:
            return

        # ---------- 1. course の更新 ----------
        course_ids = [a.course_id for a in assignments]
        stmt = select(Course).where(Course.id.in_(course_ids))
        courses = list((await db.scalars(stmt)).all())
        course_by_id = {c.id: c for c in courses}
        now = datetime.now(UTC)
        # course.id -> assigned staff_id (今回の Layer 3 出力)
        staff_by_course: dict[UUID, UUID] = {}
        for a in assignments:
            c = course_by_id.get(a.course_id)
            if c is None:
                continue
            c.assigned_staff_id = a.staff_id
            c.course_status = COURSE_STATUS_STAFF_ASSIGNED
            c.staff_assigned_at = now
            staff_by_course[a.course_id] = a.staff_id

        if not staff_by_course:
            await db.flush()
            return

        # ---------- 2. 対象 visits をロード ----------
        # 当該コース配下の planned visits + (2 名体制ペア解決のため) 同一
        # visit_group_id を共有する別コースの visits も併せて取得する。
        visit_stmt = select(Visit).where(
            Visit.course_id.in_(list(staff_by_course.keys())),
            Visit.status == VISIT_STATUS_PLANNED,
            Visit.deleted_at.is_(None),
        )
        target_visits = list((await db.scalars(visit_stmt)).all())

        # ---------- P3-① 保護: 手動差替え visit を再割当対象から除外 ----------
        # manual_staff_override=True の visit は当日欠勤の代替スタッフ提案
        # (staff_substitute.py apply) で人間が確定した差替え結果 (VSA +
        # primary/secondary_staff_id) を保持している。assign-staff-only 系の
        # 再割当で VSA を DELETE/INSERT・primary/secondary を同期し直すと差替えが
        # 失われるため、行単位で対象から除外する。除外した visit は以降の DELETE
        # (target_visit_ids) にも INSERT ループにも乗らないので、既存の VSA が
        # そのまま温存される。2 名体制は Commit 1 が visit_group 全行に override を
        # 立てるため、行単位の除外で group 全体が一貫して保護される。
        # (設計: docs/plans/p3-1-staff-substitute-design.md §4)
        target_visits = [v for v in target_visits if not v.manual_staff_override]

        if not target_visits:
            await db.flush()
            return

        # 2 名体制グループの partner 解決のため、同じ visit_group_id を持つ
        # visit を全件取得 (別コースに所属する partner も含む)
        group_ids = {v.visit_group_id for v in target_visits if v.visit_group_id is not None}
        group_visits: list[Visit] = []
        if group_ids:
            partner_stmt = select(Visit).where(
                Visit.visit_group_id.in_(list(group_ids)),
                Visit.status == VISIT_STATUS_PLANNED,
                Visit.deleted_at.is_(None),
            )
            group_visits = list((await db.scalars(partner_stmt)).all())

        # visit_group_id -> [Visit, Visit, ...]
        visits_by_group: dict[UUID, list[Visit]] = {}
        for v in group_visits:
            if v.visit_group_id is None:
                continue
            visits_by_group.setdefault(v.visit_group_id, []).append(v)

        # partner visit の course から secondary staff_id を解決するために、
        # まだ staff_by_course にない course_id の assigned_staff_id を補完
        # (= 別コースが今回の assignments に含まれていないケース)
        unknown_course_ids: set[UUID] = set()
        for vs in visits_by_group.values():
            for v in vs:
                if v.course_id is not None and v.course_id not in staff_by_course:
                    unknown_course_ids.add(v.course_id)
        if unknown_course_ids:
            extra_stmt = select(Course).where(Course.id.in_(list(unknown_course_ids)))
            for c in (await db.scalars(extra_stmt)).all():
                if c.assigned_staff_id is not None:
                    staff_by_course[c.id] = c.assigned_staff_id

        # ---------- 3. 既存の visit_staff_assignments を冪等のため DELETE ----------
        target_visit_ids = [v.id for v in target_visits]
        await db.execute(
            delete(VisitStaffAssignment).where(VisitStaffAssignment.visit_id.in_(target_visit_ids))
        )

        # ---------- 4. visit_staff_assignments を再 INSERT ----------
        for visit in target_visits:
            primary_staff_id = (
                staff_by_course.get(visit.course_id) if visit.course_id is not None else None
            )
            if primary_staff_id is None:
                # 当該 visit のコースが今回の出力に含まれていない (= 想定外)
                continue

            staff_ids: list[UUID] = [primary_staff_id]
            secondary_staff_id: UUID | None = None

            # 2 名体制: required_staff_count == 2 もしくは visit_group_id がある
            is_two_person = visit.required_staff_count == 2 or visit.visit_group_id is not None
            if is_two_person and visit.visit_group_id is not None:
                # 同 group の partner visit の course から secondary を解決
                for partner in visits_by_group.get(visit.visit_group_id, []):
                    if partner.id == visit.id:
                        continue
                    if partner.course_id is None:
                        continue
                    partner_staff = staff_by_course.get(partner.course_id)
                    if partner_staff is None or partner_staff == primary_staff_id:
                        continue
                    secondary_staff_id = partner_staff
                    break
                if secondary_staff_id is not None:
                    staff_ids.append(secondary_staff_id)

            # 重複排除 (primary == secondary になり得ないが念のため)
            seen: set[UUID] = set()
            for sid in staff_ids:
                if sid in seen:
                    continue
                seen.add(sid)
                db.add(
                    VisitStaffAssignment(
                        visit_id=visit.id,
                        staff_id=sid,
                    )
                )

            # ---------- 5. レガシー互換: visits.primary/secondary_staff_id ----------
            visit.primary_staff_id = primary_staff_id
            visit.secondary_staff_id = secondary_staff_id

        await db.flush()


# ---------------------------------------------------------------------------
# Naive round-robin baseline (受入基準: ローテ分散度の比較対象)
# ---------------------------------------------------------------------------


def naive_round_robin(
    course_targets: list[CourseAssignmentTarget],
    staff_pool: list[StaffInfo],
) -> Layer3Result:
    """素朴な順番割当 (= ローテ分散度の比較ベースライン).

    各曜日内で、(course_code 順) に staff を 0,1,2,... と割り当てるだけの実装。
    ハード制約 (性別 / 勤務曜日) は無視するため不正な割当を含み得るが、
    分散度の比較対象としてのみ使う。
    """
    eligible = [s for s in staff_pool if s.role != "manager"]
    by_weekday: dict[int, list[CourseAssignmentTarget]] = {}
    for ct in course_targets:
        by_weekday.setdefault(ct.weekday, []).append(ct)

    all_assignments: list[StaffAssignment] = []
    for weekday in sorted(by_weekday.keys()):
        day_courses = sorted(by_weekday[weekday], key=lambda c: c.course_code)
        for i, course in enumerate(day_courses):
            if not eligible:
                continue
            staff = eligible[i % len(eligible)]
            all_assignments.append(
                StaffAssignment(
                    weekday=weekday,
                    course_code=course.course_code,
                    course_id=course.course_id,
                    staff_id=staff.staff_id,
                )
            )
    # 分散度
    asgn = Layer3Assigner()
    rotation_score = asgn._gini_index(
        [a.staff_id for a in all_assignments],
        staff_count=max(1, len(eligible)),
    )
    return Layer3Result(
        assignments=all_assignments,
        rotation_score=round(rotation_score, 6),
        total_distance_km=0.0,
    )


# ---------------------------------------------------------------------------
# Fixture helpers (tests/fixtures/layer3_4weeks.json 用)
# ---------------------------------------------------------------------------


_WEEKDAY_CODE_TO_INT: dict[str, int] = {
    "Mon": 0,
    "Tue": 1,
    "Wed": 2,
    "Thu": 3,
    "Fri": 4,
    "Sat": 5,
    "Sun": 6,
}


def _stable_uuid(s: str) -> UUID:
    """ダミー文字列を決定的な UUID に変換 (fixture 評価用)."""
    import hashlib

    digest = hashlib.md5(s.encode("utf-8")).hexdigest()
    return UUID(digest)


def staff_from_fixture_dict(d: dict[str, Any]) -> StaffInfo:
    """fixture JSON の 1 staff を ``StaffInfo`` に変換."""
    sid_raw = str(d["staff_id"])
    try:
        sid = UUID(sid_raw)
    except ValueError:
        sid = _stable_uuid(sid_raw)
    work_days_raw = d.get("work_days") or []
    if isinstance(work_days_raw, list):
        work_days_int: list[int] = []
        for w in work_days_raw:
            if isinstance(w, int):
                work_days_int.append(w)
            else:
                if w in _WEEKDAY_CODE_TO_INT:
                    work_days_int.append(_WEEKDAY_CODE_TO_INT[w])
        work_days = frozenset(work_days_int)
    else:
        work_days = frozenset()

    office_lat = d.get("primary_office_lat")
    office_lng = d.get("primary_office_lng")
    return StaffInfo(
        staff_id=sid,
        name=d.get("name", ""),
        sex=d.get("gender") or d.get("sex"),
        role=d.get("role", "staff"),
        primary_office_lat=float(office_lat) if office_lat is not None else None,
        primary_office_lng=float(office_lng) if office_lng is not None else None,
        work_days=work_days,
        is_trainee=bool(d.get("is_trainee", False)),
    )


def course_target_from_fixture_dict(d: dict[str, Any]) -> CourseAssignmentTarget:
    """fixture JSON の 1 course を ``CourseAssignmentTarget`` に変換."""
    cid_raw = str(d.get("course_id") or f"{d['iso_week']}-{d['weekday']}-{d['course_code']}")
    try:
        cid = UUID(cid_raw)
    except ValueError:
        cid = _stable_uuid(cid_raw)

    patients = d.get("patients", []) or []
    lats: list[float] = []
    lngs: list[float] = []
    pids: list[UUID] = []
    restrictions: set[str] = set()
    for p in patients:
        if p.get("lat") is not None:
            lats.append(float(p["lat"]))
        if p.get("lng") is not None:
            lngs.append(float(p["lng"]))
        gr = p.get("gender_restriction")
        if gr:
            restrictions.add(gr)
        pid_raw = str(p.get("patient_id", ""))
        try:
            pids.append(UUID(pid_raw))
        except ValueError:
            pids.append(_stable_uuid(pid_raw))

    weekday_raw = d.get("weekday")
    if isinstance(weekday_raw, str):
        weekday_int = _WEEKDAY_CODE_TO_INT.get(weekday_raw, 0)
    else:
        weekday_int = int(weekday_raw or 0)

    return CourseAssignmentTarget(
        course_id=cid,
        weekday=weekday_int,
        course_code=str(d.get("course_code", "A")),
        centroid_lat=(sum(lats) / len(lats)) if lats else None,
        centroid_lng=(sum(lngs) / len(lngs)) if lngs else None,
        gender_restrictions=frozenset(restrictions),
        patient_ids=pids,
    )


def history_from_fixture_list(
    raw: list[dict[str, Any]],
    *,
    current_iso_year: int,
    current_iso_week: int,
) -> list[tuple[int, str, UUID]]:
    """fixture の history 配列を ``[(weeks_ago, course_code, staff_id), ...]`` に変換."""
    try:
        cur_monday = date_cls.fromisocalendar(current_iso_year, current_iso_week, 1)
    except ValueError:
        return []
    out: list[tuple[int, str, UUID]] = []
    for h in raw:
        try:
            past_monday = date_cls.fromisocalendar(int(h["iso_year"]), int(h["iso_week"]), 1)
        except (ValueError, KeyError):
            # iso_year 省略時は current_iso_year を使う
            try:
                past_monday = date_cls.fromisocalendar(current_iso_year, int(h["iso_week"]), 1)
            except (ValueError, KeyError):
                continue
        weeks_ago = (cur_monday.toordinal() - past_monday.toordinal()) // 7
        if weeks_ago <= 0:
            continue
        sid_raw = str(h["staff_id"])
        try:
            sid = UUID(sid_raw)
        except ValueError:
            sid = _stable_uuid(sid_raw)
        out.append((weeks_ago, str(h["course_code"]), sid))
    return out


__all__ = [
    "COST_BETA_ROTATION",
    "COST_PATIENT_RECENT_1",
    "COST_PATIENT_RECENT_2",
    "COST_PATIENT_RECENT_3",
    "COST_W5_ROTATION_MAX",
    "DISTANCE_UNKNOWN_KM",
    "HUNGARIAN_INFINITY",
    "PATIENT_RECENT_DEPTH",
    "PATIENT_ROTATION_LOOKBACK_WEEKS",
    "ROTATION_EXCLUSION_WEEKS",
    "ROTATION_HISTORY_WEEKS",
    "CourseAssignmentTarget",
    "Layer3Assigner",
    "Layer3AssignmentError",
    "Layer3Result",
    "ReviewItem",
    "ReviewVisit",
    "RotationConflict",
    "StaffAssignment",
    "StaffInfo",
    "VisitTimeSlot",
    "_deterministic_random",
    "course_target_from_fixture_dict",
    "history_from_fixture_list",
    "hungarian_min_cost",
    "naive_round_robin",
    "sex_satisfies_restrictions",
    "staff_from_fixture_dict",
]

# N-3 (schedule-advisor P0-1): 性別ハード制約判定の公開 API.
# propose_slots_service がプライベート関数 (_sex_satisfies_restrictions) を
# 直接 import する代わりにこのエイリアスを使う. layer3 内部の実装は変えない.
sex_satisfies_restrictions = _sex_satisfies_restrictions
