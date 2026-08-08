"""提案スロットソルバ (Phase2-1).

モバイル現場ボードの「新規訪問の提案」が、自動割当エンジン
(``auto_allocator_v2``) と **同じ手法・同じ数値** で「ある候補患者を、
ある週・コース (office × weekday × course) の空きに入れられる時刻はどこか /
不可か」を判定するための **純ロジック層**.

設計方針:
    - DB 非依存. 入力は素のデータ構造 (dataclass) のみ.
    - プリミティブ (距離・移動時間・バッファー・5 分刻み・昼休み・営業枠・容量・
      同住所判定) は ``auto_allocator_v2`` から **import して再利用** する
      (ハードコード再定義しない). これにより自動割当と挙動が一致する.
    - ``_apply_travel_time_to_courses`` の earliest_start 分岐 (auto_allocator_v2
      4720-4954 付近) を「単一候補をギャップに収められるか」用に切り出した.
      元関数は一括 in-place なので再利用不可だが、規約 (移動 + バッファー →
      _add_minutes → 5 分刻み切上げ → 営業枠 / time_type / 昼休み / 18:00 / 容量
      で判定) は同一.

API / UI 接続は次段. 本モジュールは純ロジック + 単体テストのみ.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace as _dataclass_replace
from datetime import time

from app.services.scheduling.auto_allocator_v2 import (
    AM_BLOCK_END,
    AM_BLOCK_START,
    COURSE_MAX_MINUTES,
    MAX_PATIENTS_PER_COURSE,
    PM_BLOCK_END,
    PM_BLOCK_START,
    SAME_ADDRESS_PAIR_MIN_OCCUPANCY,
    SHORTAGE_THRESHOLD_MIN,
    TRAVEL_SPEED_KMH,
    VISIT_BUFFER_MINUTES,
    _add_minutes,
    _address_bucket,
    _round_up_to_5min,
    _time_to_min,
    haversine_km,
    haversine_minutes,
)
from app.services.scheduling.config import SchedulingConfig

# ---------------------------------------------------------------------------
# Phase G-88 Step3: 設定注入の解決ヘルパ.
#
# propose 経路 (proposal_solver) は auto_allocator_v2 と **同一 config** を受け、
# module 定数の代わりに使用して一貫性を保つ. ``config=None`` は module 定数を使い
# 挙動不変 (既存テストはすべて config 無しで呼ぶ).
#
# config 化する項目: 訪問間バッファー / 移動速度 / 営業開始境界 (AM_BLOCK_START) /
# 営業終了境界 (PM_BLOCK_END) / 1 コース最大人数. **正午境界 (AM_BLOCK_END=12:00 /
# PM_BLOCK_START=13:00) と COURSE_MAX_MINUTES は据え置き** (auto_allocator_v2 と同方針).
# ---------------------------------------------------------------------------


def _cfg_buffer_min(config: SchedulingConfig | None) -> int:
    return config.visit_buffer_min if config is not None else VISIT_BUFFER_MINUTES


def _cfg_speed_kmh(config: SchedulingConfig | None) -> float:
    return config.travel_speed_kmh if config is not None else TRAVEL_SPEED_KMH


def _cfg_business_start(config: SchedulingConfig | None) -> time:
    return config.business_start if config is not None else AM_BLOCK_START


def _cfg_business_end(config: SchedulingConfig | None) -> time:
    return config.business_end if config is not None else PM_BLOCK_END


def _cfg_max_patients(config: SchedulingConfig | None) -> int:
    return config.max_patients_per_course if config is not None else MAX_PATIENTS_PER_COURSE


# ``calc_course_total_minutes`` は ``V2Visit`` を引数に取るため、本ソルバの
# 素データ (ExistingVisit) からは直接呼べない. 代わりに同関数と **同一の計算規約**
# (service 合計 + 異住所遷移ごとに travel + buffer、同住所ペアは max(a+b, 90) に
# 底上げ) を ``_course_total_minutes_from_existing`` で再現する (下記参照).


# ---------------------------------------------------------------------------
# 入出力データ構造 (純データ / DB 非依存)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExistingVisit:
    """対象コース (office × weekday × course) の既存訪問 1 件.

    ``find_available_slots_for_candidate`` には start_time 昇順で渡す前提
    (内部でも再ソートするが、呼び出し側で揃えておくのが自然).
    """

    start_time: time
    end_time: time
    lat: float
    lng: float
    service_minutes: int
    # 同住所ペア判定 (同一患者の重複を弾く) 用の識別子. 任意.
    patient_id: str | None = None


@dataclass(frozen=True)
class EventWindow:
    """コース担当スタッフのイベント (個別業務・staff_events) 1 件分の占有窓.

    2段階提案 (PO確定 2026-07-27):
        - パスA: 全イベントを (前後 ``EVENT_BUFFER_MINUTES`` 分のバッファ込みで)
          占有としてギャップ走査に織り込む → クリーン枠のみ返す.
        - パスAが 0 件のときだけパスB: ``blocking=True`` のイベントだけ占有として
          再走査し、ソフトイベントと重なる枠に ``Slot.event_conflicts`` を付けて返す.
    ゼロ長 (start == end のメモ) は呼び出し側で除外するか、本モジュールが無視する.
    """

    start: time
    end: time
    title: str = ""
    # 🔒絶対に潰せないイベント: パスBでも占有のまま (衝突提案を出さない).
    blocking: bool = False


@dataclass(frozen=True)
class EventConflict:
    """フォールバック枠が重なったイベントの表示用情報."""

    start: time
    end: time
    title: str


@dataclass(frozen=True)
class Candidate:
    """提案対象の候補患者 1 名."""

    lat: float
    lng: float
    service_minutes: int
    # "固定" / "時間帯" / "午前" / "午後" / "終日" / None.
    time_type: str | None = None
    # time_type="固定" は preferred_start を厳密採用、
    # time_type="時間帯" は [preferred_start, preferred_end] 範囲制約.
    preferred_start: time | None = None
    preferred_end: time | None = None
    patient_id: str | None = None


@dataclass(frozen=True)
class Slot:
    """実現可能な提案スロット 1 件.

    Attributes:
        start: 提案開始時刻 (5 分刻み. 固定は希望時刻そのまま).
        end: 提案終了時刻 (= start + service_minutes. 同住所ペア時は
            ``same_address_pair`` フラグ参照. ペアの占有は別途 90 分底上げ).
        same_address_pair: 既存の単独患者と同住所 (<=100m) に「同時刻ペア」
            として入る候補なら True (移動 0 / 占有 90 分).
        near_lunch: 昼休み window (動的) の直前 / 直後に隣接して配置される
            (= 余裕が小さい) 場合の参考フラグ.
        block: "am" / "pm" — どちらの営業枠に入るか.
        reason: スロット成立の補足 (デバッグ / UI 表示用).
        warning: 実現可能だが注意が必要な場合の警告種別 (後段 API で警告表示に使う).
            現状は固定時刻で移動が ``SHORTAGE_THRESHOLD_MIN`` 分未満不足のとき
            ``"travel_time_shortage"`` を設定する (auto_allocator_v2 と同手法:
            微小不足は前 visit の早期終了で吸収可能とみなし警告付きで許容).
            空文字なら警告なし.
    """

    start: time
    end: time
    block: str
    same_address_pair: bool = False
    near_lunch: bool = False
    reason: str = ""
    warning: str = ""
    # 2段階提案パスBのみ: この枠と (バッファ込みで) 重なるソフトイベント.
    # 空タプル = クリーン枠. FE は「⚠ イベント◯◯と重なります。配置後に
    # イベントの方を調整してください」を表示する.
    event_conflicts: tuple[EventConflict, ...] = ()


# 昼休み近接 (near_lunch) 判定の余裕しきい値 (分).
_NEAR_LUNCH_MARGIN_MIN: int = 5

# イベント占有の前後バッファ (分). Layer3 の event ハード除外 (W33
# ``layer3_assignment.BUFFER_MINUTES=15``) と同じ値・同じ意味 (移動・準備の余裕).
EVENT_BUFFER_MINUTES: int = 15

# イベント擬似訪問の patient_id 接頭辞 (同住所ペア判定から除外するための目印).
_EVENT_SENTINEL_PREFIX = "__event__"


# ---------------------------------------------------------------------------
# 1. 移動 + バッファーを考慮した最早開始時刻
# ---------------------------------------------------------------------------


def compute_earliest_start_after(
    prev_end: time,
    prev_latlng: tuple[float, float],
    cand_latlng: tuple[float, float],
    *,
    same_address: bool,
    config: SchedulingConfig | None = None,
) -> time:
    """前 visit 終了後、候補を最早でいつ開始できるか (5 分刻み切上げ).

    auto_allocator_v2 ``_apply_travel_time_to_courses`` の earliest_start 計算
    (``earliest_start = _add_minutes(prev.end_time, travel_min + buffer_min)``、
    その後 ``_round_up_to_5min``) と同一規約.

    - 同住所 (``same_address=True``): 移動 0 分・バッファー 0 分.
    - 異住所: ``haversine_minutes(haversine_km(...)) + VISIT_BUFFER_MINUTES``.

    Args:
        prev_end: 直前 visit の終了時刻.
        prev_latlng: 直前 visit の (lat, lng).
        cand_latlng: 候補の (lat, lng).
        same_address: 同住所 (<=100m) なら True.

    Returns:
        5 分刻みに切り上げた最早開始 ``time``.
    """
    if same_address:
        travel_min = 0
        buffer_min = 0
    else:
        travel_min = haversine_minutes(
            haversine_km(prev_latlng[0], prev_latlng[1], cand_latlng[0], cand_latlng[1]),
            speed_kmh=_cfg_speed_kmh(config),
        )
        buffer_min = _cfg_buffer_min(config)
    earliest = _add_minutes(prev_end, travel_min + buffer_min)
    return _round_up_to_5min(earliest)


# ---------------------------------------------------------------------------
# 内部ヘルパ
# ---------------------------------------------------------------------------


def _is_same_address(lat1: float, lng1: float, lat2: float, lng2: float) -> bool:
    """``_address_bucket`` (SAME_ADDRESS_TOLERANCE=0.001≈100m) による同住所判定."""
    return _address_bucket(lat1, lng1) == _address_bucket(lat2, lng2)


def _travel_buffer_between(
    a_lat: float,
    a_lng: float,
    b_lat: float,
    b_lng: float,
    *,
    config: SchedulingConfig | None = None,
) -> int:
    """2 地点間の移動 + バッファー (分). 同住所は 0."""
    if _is_same_address(a_lat, a_lng, b_lat, b_lng):
        return 0
    return haversine_minutes(
        haversine_km(a_lat, a_lng, b_lat, b_lng), speed_kmh=_cfg_speed_kmh(config)
    ) + _cfg_buffer_min(config)


def _course_total_minutes_from_existing(
    visits: list[ExistingVisit],
    *,
    config: SchedulingConfig | None = None,
) -> int:
    """既存訪問列のコース占有合計 (分).

    基本は auto_allocator_v2 ``calc_course_total_minutes`` (V2Visit 版) と同規約だが、
    同住所ペア (異患者) の占有計算だけ入力データの違いに応じて分岐する:
        - **同 start_time** (= auto_allocator が ``_align_same_address_pair_to_same_time``
          で整合済): 本関数の入力 ExistingVisit.service_minutes は DB の ``end-start``
          (= 既に 90 分占有を反映済; ``propose_slots_service.load_week_course_buckets``
          が end-start で算出) のため、 ここで ``max(a+b, 90)`` を再適用すると 90 分を
          二重計上する. よって実占有 ``max(end) - 共有 start`` をそのまま使う.
        - **別 start_time** (未整合の同住所連続): service_minutes は実サービス時間なので
          従来どおり ``max(a.service + b.service, SAME_ADDRESS_PAIR_MIN_OCCUPANCY=90)``.
        (``i += 2`` でペア確定後は次へ; 3 人目以降は single 扱い.)
        - 隣接遷移ごとに異住所なら ``travel + VISIT_BUFFER_MINUTES`` を加算.

    注: auto_allocator は in-memory V2Visit (service_minutes=実サービス) を扱うため
    常に ``max(a+b, 90)`` で良いが、 本関数は DB read 経路で service_minutes が膨張済の
    ため上記の同 start_time 分岐が必要 (= 規約の意図は同じ、 入力差を吸収する).
    """
    if not visits:
        return 0
    sv = sorted(visits, key=lambda v: v.start_time)
    total = 0
    n = len(sv)
    i = 0
    while i < n:
        cur = sv[i]
        if i + 1 < n:
            nxt = sv[i + 1]
            if (
                _is_same_address(cur.lat, cur.lng, nxt.lat, nxt.lng)
                and cur.patient_id != nxt.patient_id
            ):
                if cur.start_time == nxt.start_time:
                    # 課題1 (二重計上是正): auto_allocator が同住所ペアを同 start_time に
                    # 整合済 (_align_same_address_pair_to_same_time) の場合、 DB の end_time
                    # は既に 90 分占有を反映している (service_minutes = end-start も底上げ済).
                    # ここで再び max(a+b, 90) すると 90 分を二重計上し過大になるため、
                    # 実占有 (max(end) - 共有 start) をそのまま使う.
                    pair_occupancy = max(
                        _time_to_min(cur.end_time), _time_to_min(nxt.end_time)
                    ) - _time_to_min(cur.start_time)
                else:
                    # 未整合 (別 start_time) の同住所連続: service 合計と 90 分の大きい方.
                    pair_occupancy = max(
                        int(cur.service_minutes) + int(nxt.service_minutes),
                        SAME_ADDRESS_PAIR_MIN_OCCUPANCY,
                    )
                total += pair_occupancy
                i += 2
                continue
        total += int(cur.service_minutes)
        i += 1

    for j in range(1, len(sv)):
        prev = sv[j - 1]
        cur = sv[j]
        if _is_same_address(prev.lat, prev.lng, cur.lat, cur.lng):
            continue
        total += haversine_minutes(
            haversine_km(prev.lat, prev.lng, cur.lat, cur.lng), speed_kmh=_cfg_speed_kmh(config)
        ) + _cfg_buffer_min(config)
    return total


def _within_operating_block(
    start: time, end: time, block: str, *, config: SchedulingConfig | None = None
) -> bool:
    """営業枠 (AM 09:30-12:00 / PM 13:00-18:00) に [start, end] が収まるか.

    Phase G-88 Step3: 営業開始境界 (AM 側下限) / 営業終了境界 (PM 側上限) のみ config 化.
    正午境界 (AM_BLOCK_END=12:00 / PM_BLOCK_START=13:00) は据え置き.
    """
    if block == "am":
        return start >= _cfg_business_start(config) and end <= AM_BLOCK_END
    return start >= PM_BLOCK_START and end <= _cfg_business_end(config)


def _time_type_allows(
    candidate: Candidate,
    start: time,
    end: time,
    *,
    config: SchedulingConfig | None = None,
) -> bool:
    """time_type 別の時刻制約を満たすか.

    auto_allocator_v2 と同手法:
        - 固定: start が preferred_start に厳密一致 (preferred_start 不在なら不可).
          移動不足の許容 (``shortage < SHORTAGE_THRESHOLD_MIN`` は警告付きで実現可能、
          それ以上は不可) はギャップ走査側 ``_scan_block`` で判定する
          (本関数は固定時刻ちょうどに置けるかの時刻一致のみ確認).
        - 時間帯: [preferred_start, preferred_end] 内に start が収まる.
        - 午前: end <= 12:00 (AM_BLOCK_END).
        - 午後: start >= 13:00 (PM_BLOCK_START).
        - 終日 / None: 営業枠内自由 (本関数は True、枠判定は別途).
    """
    tt = candidate.time_type
    if tt == "固定":
        if candidate.preferred_start is None:
            return False
        return start == candidate.preferred_start
    if tt == "時間帯":
        # preferred_start が None の場合の下限について:
        #   エンジン (auto_allocator_v2) は時間帯 visit の下限に既存 visit の
        #   ``desired_start`` (= cur.start_time) を使うが、提案フェーズでは候補が
        #   まだ未配置で ``desired_start`` が存在しない. そこで提案では営業開始の
        #   ``AM_BLOCK_START`` (09:30) を下限とする (挙動はこれで維持).
        #   上限 None も同様に営業終了の ``PM_BLOCK_END`` (18:00) を採用.
        lower = (
            candidate.preferred_start
            if candidate.preferred_start is not None
            else _cfg_business_start(config)
        )
        upper = (
            candidate.preferred_end
            if candidate.preferred_end is not None
            else _cfg_business_end(config)
        )
        return lower <= start <= upper
    if tt == "午前":
        return end <= AM_BLOCK_END
    if tt == "午後":
        return start >= PM_BLOCK_START
    # 終日 / None / 不明.
    return True


def slot_feasible(
    start: time,
    candidate: Candidate,
    *,
    lunch_window: tuple[time, time] | None,
    block: str,
    config: SchedulingConfig | None = None,
) -> bool:
    """ある開始時刻 ``start`` に候補を置けるか (時刻系の全制約).

    チェック内容 (容量は別途 ``find_available_slots_for_candidate`` で判定):
        1. 営業枠内 (AM 09:30-12:00 / PM 13:00-18:00).
        2. 18:00 超過排除 (end <= 18:00 は枠判定に含む).
        3. 昼休み回避 (lunch_window と重ならない).
        4. time_type 別制約.
    """
    end = _add_minutes(start, candidate.service_minutes)
    # 18:00 を跨ぐ / 翌日に回る異常は弾く.
    if _time_to_min(end) <= _time_to_min(start):
        return False
    if not _within_operating_block(start, end, block, config=config):
        return False
    if _lunch_overlaps(start, end, lunch_window):
        return False
    if not _time_type_allows(candidate, start, end, config=config):
        return False
    return True


def _lunch_overlaps(start: time, end: time, lunch: tuple[time, time] | None) -> bool:
    """[start, end) が lunch slot と重なるか (auto_allocator_v2 _lunch_window_overlaps と同一)."""
    if lunch is None:
        return False
    ls, le = lunch
    if start >= le:
        return False
    if end <= ls:
        return False
    return True


def _near_lunch(end: time, start: time, lunch: tuple[time, time] | None) -> bool:
    """昼休み window の直前 / 直後に近接 (余裕 < _NEAR_LUNCH_MARGIN_MIN 分) か."""
    if lunch is None:
        return False
    ls, le = lunch
    end_min = _time_to_min(end)
    start_min = _time_to_min(start)
    ls_min = _time_to_min(ls)
    le_min = _time_to_min(le)
    # AM 側: visit end が lunch start の直前.
    if 0 <= ls_min - end_min < _NEAR_LUNCH_MARGIN_MIN:
        return True
    # PM 側: visit start が lunch end の直後.
    if 0 <= start_min - le_min < _NEAR_LUNCH_MARGIN_MIN:
        return True
    return False


# ---------------------------------------------------------------------------
# イベント (staff_events) の占有化ヘルパ
# ---------------------------------------------------------------------------


def _min_to_time(minutes: int) -> time:
    """分 → time (日境界へクランプ)."""
    m = max(0, min(minutes, 23 * 60 + 59))
    return time(m // 60, m % 60)


def _buffered_event_interval(w: EventWindow) -> tuple[int, int]:
    """イベント窓を EVENT_BUFFER_MINUTES 分だけ両側へ拡張した [分, 分) 区間."""
    return (
        max(0, _time_to_min(w.start) - EVENT_BUFFER_MINUTES),
        min(23 * 60 + 59, _time_to_min(w.end) + EVENT_BUFFER_MINUTES),
    )


def _event_pseudo_visits(windows: list[EventWindow], candidate: Candidate) -> list[ExistingVisit]:
    """イベント窓をギャップ走査用の擬似訪問へ変換する.

    座標は候補と同一にする (イベントの場所は不明なため移動時間は見積もらず、
    代わりにバッファ拡張済み区間そのものを占有とする = Layer3 の
    「event ± BUFFER_MINUTES と重なればハード除外」と同じ強さになる).
    patient_id は ``__event__`` 接頭辞の目印付き (同住所ペア判定から除外).
    ゼロ長 (メモ) は無視する.
    """
    out: list[ExistingVisit] = []
    for i, w in enumerate(windows):
        if _time_to_min(w.end) <= _time_to_min(w.start):
            continue  # ゼロ長メモ / 異常値は占有にしない.
        bs, be = _buffered_event_interval(w)
        out.append(
            ExistingVisit(
                start_time=_min_to_time(bs),
                end_time=_min_to_time(be),
                lat=candidate.lat,
                lng=candidate.lng,
                service_minutes=be - bs,
                patient_id=f"{_EVENT_SENTINEL_PREFIX}{i}",
            )
        )
    return out


def _slot_overlaps_any(slot: Slot, windows: list[EventWindow]) -> bool:
    """枠 [start, end) がいずれかのイベント (バッファ込み) と重なるか."""
    s = _time_to_min(slot.start)
    e = _time_to_min(slot.end)
    for w in windows:
        bs, be = _buffered_event_interval(w)
        if s < be and e > bs:
            return True
    return False


def _conflicts_for(slot: Slot, windows: list[EventWindow]) -> tuple[EventConflict, ...]:
    """枠と (バッファ込みで) 重なるイベントの表示用リスト (生の時刻で返す)."""
    s = _time_to_min(slot.start)
    e = _time_to_min(slot.end)
    hits: list[EventConflict] = []
    for w in windows:
        bs, be = _buffered_event_interval(w)
        if s < be and e > bs:
            hits.append(EventConflict(start=w.start, end=w.end, title=w.title))
    return tuple(hits)


# ---------------------------------------------------------------------------
# 2. ギャップ走査によるスロット探索
# ---------------------------------------------------------------------------


def find_available_slots_for_candidate(
    existing_visits: list[ExistingVisit],
    candidate: Candidate,
    *,
    lunch_window: tuple[time, time] | None,
    weekday: int,
    course_capacity_used: int | None = None,
    course_minutes_used: int | None = None,
    config: SchedulingConfig | None = None,
    exclusion_sink: list[str] | None = None,
    event_windows: list[EventWindow] | None = None,
) -> list[Slot]:
    """候補をコースの空きに入れられる開始時刻を列挙する (不可なら空リスト).

    手法は auto_allocator_v2 と同一:
        - 営業枠 (AM 09:30-12:00 / PM 13:00-18:00) ごとにギャップを走査.
        - 各ギャップ [前visit.end + 移動+バッファー(候補↔前), 次visit.start −
          (候補↔次の移動+バッファー)] に候補が収まるかを 5 分刻み切上げ最早から判定.
        - 先頭 (枠開始から) / 末尾 (最終 visit 後〜枠終了) も走査.
        - 制約: 営業枠 / 昼休み回避 / 18:00 超過排除 / time_type 別 / 容量
          (visit 数 < 6 かつ total + 候補占有 <= 480).
        - 同住所: 候補が既存の単独患者と同住所 (<=100m) なら、その隣に同時刻ペア
          (移動 0 / 90 分占有) で入れる候補も追加する.

    Args:
        existing_visits: 対象コースの既存訪問列.
        candidate: 候補患者.
        lunch_window: 動的昼休み (compute_lunch_window 結果). None なら昼休み制約なし.
        weekday: 0=Mon..6=Sun (現状ロジックでは未使用だが将来の曜日別制約に備え受領).
        course_capacity_used: 既存 visit 数. None なら ``len(existing_visits)``.
        course_minutes_used: 既存コース占有 (分). None なら既存列から算出.
        exclusion_sink: P-1b (N-6「黙って消さない」) 用の除外理由レコーダ. ``None`` 以外の
            list を渡すと、候補を落とした地点で理由コード (capacity_full / travel_shortage /
            lunch_window / no_gap) を append する. **判定ロジックには一切影響しない** 純観測用
            (呼び出し側が「なぜ 0 件か」を集約するためのフック).

    Returns:
        実現可能 ``Slot`` のリスト (start 昇順). 入れられなければ空リスト.
        ``event_windows`` を渡した場合は2段階: 全イベント占有でのクリーン枠が
        1件でもあればそれを返し、0件のときだけ blocking のみ占有で再走査した
        フォールバック枠 (``event_conflicts`` タグ付き) を返す.
    """
    del weekday  # 現状未使用 (将来の曜日別制約用に signature 保持).
    sv = sorted(existing_visits, key=lambda v: v.start_time)

    used_count = course_capacity_used if course_capacity_used is not None else len(sv)
    used_minutes = (
        course_minutes_used
        if course_minutes_used is not None
        else _course_total_minutes_from_existing(sv, config=config)
    )

    # 容量: visit 数 < max_patients かつ total + 候補占有 (= service) <= 480.
    # Phase G-88 Step3: max_patients は config 化. COURSE_MAX_MINUTES は据え置き.
    # イベント擬似訪問は容量に**算入しない** (件数/分とも実訪問のみ).
    capacity_ok = (
        used_count < _cfg_max_patients(config)
        and used_minutes + int(candidate.service_minutes) <= COURSE_MAX_MINUTES
    )

    windows = [w for w in (event_windows or []) if _time_to_min(w.end) > _time_to_min(w.start)]

    def _collect(enforced: list[EventWindow]) -> list[Slot]:
        """enforced のイベントを占有としてギャップ走査 + 同住所ペアを収集する."""
        sv_aug = sorted(
            [*sv, *_event_pseudo_visits(enforced, candidate)],
            key=lambda v: v.start_time,
        )
        slots: list[Slot] = []
        if capacity_ok:
            slots.extend(
                _scan_block(
                    sv_aug,
                    candidate,
                    lunch_window,
                    "am",
                    config=config,
                    exclusion_sink=exclusion_sink,
                )
            )
            slots.extend(
                _scan_block(
                    sv_aug,
                    candidate,
                    lunch_window,
                    "pm",
                    config=config,
                    exclusion_sink=exclusion_sink,
                )
            )
        elif exclusion_sink is not None:
            # 容量上限 (件数 or 分) で am/pm 走査自体をスキップした = capacity_full.
            exclusion_sink.append("capacity_full")

        # 同住所ペア: 候補が既存の単独患者と同住所なら、その隣に同時刻で入れる.
        # (ペアは実訪問 sv のみ対象。イベント占有との重複は後段フィルタで弾く.)
        pair_slots = _same_address_pair_slots(
            sv,
            candidate,
            used_count=used_count,
            used_minutes=used_minutes,
            config=config,
        )
        slots.extend(s for s in pair_slots if not _slot_overlaps_any(s, enforced))

        # start 昇順 + (start, same_address_pair) で重複除去.
        seen: set[tuple[int, bool]] = set()
        unique: list[Slot] = []
        for s in sorted(slots, key=lambda x: (_time_to_min(x.start), x.same_address_pair)):
            key = (_time_to_min(s.start), s.same_address_pair)
            if key in seen:
                continue
            seen.add(key)
            unique.append(s)
        return unique

    if not windows:
        return _collect([])

    # パスA: 全イベントを占有としてクリーン枠を探す.
    clean = _collect(windows)
    if clean:
        return clean

    # パスB: blocking のみ占有で再走査し、ソフトイベントとの衝突をタグ付けする.
    hard = [w for w in windows if w.blocking]
    soft = [w for w in windows if not w.blocking]
    if not soft:
        # 全イベントが blocking = パスAと同一条件. 再走査しても結果は同じ.
        return []
    fallback = _collect(hard)
    return [_dataclass_replace(s, event_conflicts=_conflicts_for(s, soft)) for s in fallback]


def _existing_occupancy_end(v: ExistingVisit, others: list[ExistingVisit]) -> time:
    """既存 visit の占有終端を同住所 2 名 90 分占有込みで返す.

    課題1 (スロット配置): auto_allocator ``_same_address_pair_occupancy_end`` 相当.
    同住所・別患者・(同 start_time もしくは連続) の相手が居れば、 占有を
    ``max(各 end, anchor_start + SAME_ADDRESS_PAIR_MIN_OCCUPANCY=90)`` に底上げする.
    居なければ ``v.end_time`` をそのまま返す. ギャップ走査 (``_scan_block``) でこの
    占有終端を「前 visit からの最早開始」起点に使うことで、 同住所ペアの 90 分占有の
    最中 (例: 安永/菅原 16:00 ペア → 16:00-17:30) に候補を誤って差し込まないようにする.
    """
    if v.patient_id is not None and v.patient_id.startswith(_EVENT_SENTINEL_PREFIX):
        # イベント擬似訪問は同住所ペアの底上げ対象外 (占有はバッファ込み区間そのもの).
        return v.end_time
    members = [
        o
        for o in others
        if o is not v
        and (o.patient_id is None or v.patient_id is None or o.patient_id != v.patient_id)
        and not (o.patient_id is not None and o.patient_id.startswith(_EVENT_SENTINEL_PREFIX))
        and _is_same_address(v.lat, v.lng, o.lat, o.lng)
        and (
            o.start_time == v.start_time or o.end_time == v.start_time or v.end_time == o.start_time
        )
    ]
    if not members:
        return v.end_time
    anchor_start = min([v.start_time, *[m.start_time for m in members]], key=_time_to_min)
    floor_end = _add_minutes(anchor_start, SAME_ADDRESS_PAIR_MIN_OCCUPANCY)
    return max([v.end_time, floor_end, *[m.end_time for m in members]], key=_time_to_min)


def _scan_block(
    sv: list[ExistingVisit],
    candidate: Candidate,
    lunch_window: tuple[time, time] | None,
    block: str,
    *,
    config: SchedulingConfig | None = None,
    exclusion_sink: list[str] | None = None,
) -> list[Slot]:
    """1 営業枠 (am / pm) 内のギャップを走査し、収まる開始時刻を返す.

    ``exclusion_sink`` (P-1b) は候補を落とした地点で理由コードを append するだけの
    純観測フック. 判定ロジック (slot_feasible / 容量 / gap) には一切影響しない.

    各ギャップごとに「前 visit からの最早開始 (移動+バッファー+5分切上げ)」を
    起点に slot_feasible を試す. 候補が次 visit に被らない (= 候補↔次の移動+
    バッファーを確保して next.start までに終わる) ことも確認する.

    Phase G-88 Step3: AM 枠の開始 (business_start) / PM 枠の終了 (business_end) のみ
    config 化. 正午境界 (AM_BLOCK_END / PM_BLOCK_START) は据え置き.
    """
    block_start = _cfg_business_start(config) if block == "am" else PM_BLOCK_START
    block_end = AM_BLOCK_END if block == "am" else _cfg_business_end(config)

    # この枠に重なる既存 visit のみ対象 (start が枠内、または枠と重なる).
    in_block = [
        v
        for v in sv
        if _time_to_min(v.end_time) > _time_to_min(block_start)
        and _time_to_min(v.start_time) < _time_to_min(block_end)
    ]

    results: list[Slot] = []
    service = candidate.service_minutes

    # ギャップ境界: [枠開始, v0.start], [v0.end, v1.start], ..., [vN.end, 枠終了].
    # 先頭ギャップ.
    earliest_candidates: list[tuple[time, ExistingVisit | None, ExistingVisit | None]] = []
    if not in_block:
        earliest_candidates.append((block_start, None, None))
    else:
        # 先頭: 枠開始 から最初の visit まで.
        earliest_candidates.append((block_start, None, in_block[0]))
        # 中間 + 末尾.
        for idx, prev in enumerate(in_block):
            nxt = in_block[idx + 1] if idx + 1 < len(in_block) else None
            # 課題1: prev が同住所ペアなら 90 分占有終端を起点にする (生の end_time だと
            # ペア占有の最中に候補を差し込んでしまう).
            prev_occ_end = _existing_occupancy_end(prev, in_block)
            earliest = compute_earliest_start_after(
                prev_occ_end,
                (prev.lat, prev.lng),
                (candidate.lat, candidate.lng),
                same_address=_is_same_address(prev.lat, prev.lng, candidate.lat, candidate.lng),
                config=config,
            )
            earliest_candidates.append((earliest, prev, nxt))

    for earliest, prev, nxt in earliest_candidates:
        # 枠開始より前なら枠開始へクランプ (先頭ギャップは block_start 起点).
        start = earliest if _time_to_min(earliest) >= _time_to_min(block_start) else block_start
        # 5 分刻みに揃える (block_start は既に 5 分刻み).
        start = _round_up_to_5min(start)

        # time_type=固定 は希望時刻に強制配置する.
        # 移動が間に合わない (earliest > fixed) 場合は、エンジン
        # (auto_allocator_v2 ~4738-4742, 4778) と同手法で不足量により分岐する:
        #   * shortage < SHORTAGE_THRESHOLD_MIN (= 5 分未満): 前 visit の早期終了で
        #     吸収可能とみなし、固定時刻のまま実現可能としつつ slot に
        #     warning="travel_time_shortage" を付与する.
        #   * shortage >= SHORTAGE_THRESHOLD_MIN: 物理的に配置不可として除外する.
        # shortage はエンジンと揃えるため raw (5 分切上げ前) の earliest で算出する.
        fixed_warning = ""
        if candidate.time_type == "固定":
            if candidate.preferred_start is None:
                continue
            fixed = candidate.preferred_start
            if _time_to_min(fixed) < _time_to_min(block_start):
                continue
            if prev is not None:
                travel_buffer = _travel_buffer_between(
                    prev.lat, prev.lng, candidate.lat, candidate.lng, config=config
                )
                # 課題1: 固定時刻でも前が同住所ペアなら 90 分占有終端を起点にする.
                earliest_raw = _add_minutes(_existing_occupancy_end(prev, in_block), travel_buffer)
                shortage = _time_to_min(earliest_raw) - _time_to_min(fixed)
                if shortage > 0:
                    if shortage >= SHORTAGE_THRESHOLD_MIN:
                        # 物理不可能 → このギャップでは配置不可.
                        if exclusion_sink is not None:
                            exclusion_sink.append("travel_shortage")
                        continue
                    # 微小不足は許容 (固定時刻のまま配置、warning 付与).
                    fixed_warning = "travel_time_shortage"
            start = fixed

        end = _add_minutes(start, service)

        # 次 visit と被らないか: 候補 end + (候補↔次の移動+バッファー) <= next.start.
        if nxt is not None:
            gap_after = _travel_buffer_between(
                candidate.lat, candidate.lng, nxt.lat, nxt.lng, config=config
            )
            latest_end = _add_minutes(nxt.start_time, -gap_after)
            if _time_to_min(end) > _time_to_min(latest_end):
                if exclusion_sink is not None:
                    exclusion_sink.append("no_gap")
                continue

        if not slot_feasible(
            start, candidate, lunch_window=lunch_window, block=block, config=config
        ):
            if exclusion_sink is not None:
                # 判定は変えず、昼休み重複か否かで理由を分類する (純観測).
                exclusion_sink.append(
                    "lunch_window" if _lunch_overlaps(start, end, lunch_window) else "no_gap"
                )
            continue

        results.append(
            Slot(
                start=start,
                end=end,
                block=block,
                same_address_pair=False,
                near_lunch=_near_lunch(end, start, lunch_window),
                reason=f"{block} gap"
                + (" (head)" if prev is None else "")
                + (" (tail)" if nxt is None else "")
                + (" (移動不足許容)" if fixed_warning else ""),
                warning=fixed_warning,
            )
        )
    return results


def _same_address_pair_slots(
    sv: list[ExistingVisit],
    candidate: Candidate,
    *,
    used_count: int,
    used_minutes: int,
    config: SchedulingConfig | None = None,
) -> list[Slot]:
    """候補が既存の単独患者と同住所なら、その隣に同時刻ペアで入れるスロット.

    auto_allocator_v2 ``_align_same_address_pair_to_same_time`` 相当:
        - 候補と既存 visit が同住所 (<=100m) かつ別患者.
        - 同時刻 (既存 visit の start_time) に配置、移動 0.
        - ペア占有は ``max(a+b, 90)``. end は占有反映.
    容量はペアでも「コースに 1 名追加」「占有増分」を見る.
    """
    results: list[Slot] = []
    if used_count >= _cfg_max_patients(config):
        return results

    for v in sv:
        if v.patient_id is not None and v.patient_id == candidate.patient_id:
            continue
        if not _is_same_address(v.lat, v.lng, candidate.lat, candidate.lng):
            continue
        # 既に同住所に 2 名以上いる (ペア成立済み) 相手は skip: その相手と同住所な
        # 別 visit が存在する場合はペア飽和とみなす.
        partners = [o for o in sv if o is not v and _is_same_address(o.lat, o.lng, v.lat, v.lng)]
        if partners:
            continue

        pair_start = v.start_time
        pair_occupancy = max(
            int(v.service_minutes) + int(candidate.service_minutes),
            SAME_ADDRESS_PAIR_MIN_OCCUPANCY,
        )
        pair_end = _add_minutes(pair_start, pair_occupancy)

        # 容量: 既存 single の service を pair 占有に置換した増分で再評価.
        delta = pair_occupancy - int(v.service_minutes)
        if used_minutes + delta > COURSE_MAX_MINUTES:
            continue

        # 同時刻ペアは prev からの移動を v が既に満たしている前提 (v が配置済み).
        # ペア占有 [pair_start, pair_end] が営業枠 / 18:00 内に収まるか再確認する
        # (相方の service が短くても 90 分底上げで 18:00 を超えるケースを弾く).
        block = "am" if _time_to_min(pair_start) < _time_to_min(PM_BLOCK_START) else "pm"
        if not _within_operating_block(pair_start, pair_end, block, config=config):
            continue

        results.append(
            Slot(
                start=pair_start,
                end=pair_end,
                block=block,
                same_address_pair=True,
                near_lunch=False,
                reason="same_address pair (移動0 / 90分占有)",
            )
        )
    return results


def slot_fits_exact(
    existing_visits: list[ExistingVisit],
    candidate: Candidate,
    target_start: time,
    *,
    lunch_window: tuple[time, time] | None,
    config: SchedulingConfig | None = None,
    event_windows: list[EventWindow] | None = None,
    enforce_soft_events: bool = True,
) -> bool:
    """候補を ``target_start`` ちょうどに配置できるか (時刻系 + 前後移動制約).

    W-12a (2 名体制ペア): 主コースの slot0 候補時刻 T に、別コースで slot1 を同時刻配置
    できるかを点検するための「特定時刻 T ちょうど」判定. ``find_available_slots_for_candidate``
    は各ギャップの最早開始のみを列挙するため任意 T は出せない. 本関数は:
        1. ``slot_feasible`` (営業枠 / 昼休み / 18:00 / time_type) を T で確認.
        2. 既存訪問との前後移動制約 (travel + buffer 分離。同住所同時刻は許容) を
           ``pfv_validator._find_conflict`` と同一規則で確認.
    **容量は呼出側で別途点検する** (bucket 単位のため本関数の対象外).
    """
    block = "am" if _time_to_min(target_start) < _time_to_min(PM_BLOCK_START) else "pm"
    if not slot_feasible(
        target_start, candidate, lunch_window=lunch_window, block=block, config=config
    ):
        return False
    end = _add_minutes(target_start, candidate.service_minutes)
    # イベント占有 (2段階提案): enforce_soft_events=True なら全イベント、
    # False なら blocking のみを占有として T ちょうどの配置を弾く.
    if event_windows:
        enforced = [
            w
            for w in event_windows
            if (enforce_soft_events or w.blocking) and _time_to_min(w.end) > _time_to_min(w.start)
        ]
        probe = Slot(start=target_start, end=end, block=block)
        if _slot_overlaps_any(probe, enforced):
            return False
    p_start = _time_to_min(target_start)
    p_end = _time_to_min(end)
    proposed = ExistingVisit(
        start_time=target_start,
        end_time=end,
        lat=candidate.lat,
        lng=candidate.lng,
        service_minutes=candidate.service_minutes,
        patient_id=candidate.patient_id,
    )
    all_visits = [*existing_visits, proposed]
    for e in existing_visits:
        if (
            e.patient_id is not None
            and candidate.patient_id is not None
            and e.patient_id == candidate.patient_id
        ):
            continue
        if _is_same_address(candidate.lat, candidate.lng, e.lat, e.lng):
            continue  # 同住所同時刻は許容 (前方/後方制約と同一規則).
        travel = _travel_buffer_between(candidate.lat, candidate.lng, e.lat, e.lng, config=config)
        e_start = _time_to_min(e.start_time)
        e_occ_end = _time_to_min(_existing_occupancy_end(e, all_visits))
        if p_start >= e_start:
            # 候補が後方: 前 visit の占有終端 + 移動 が候補 start を超えたら衝突.
            if e_occ_end + travel > p_start:
                return False
        else:
            # 候補が前方: 候補 end + 移動 が 次 visit.start を超えたら衝突.
            if p_end + travel > e_start:
                return False
    return True


# 後方互換 / 明示 export.
__all__ = [
    "Candidate",
    "EventConflict",
    "EventWindow",
    "ExistingVisit",
    "Slot",
    "_course_total_minutes_from_existing",
    "compute_earliest_start_after",
    "find_available_slots_for_candidate",
    "slot_feasible",
    "slot_fits_exact",
]
