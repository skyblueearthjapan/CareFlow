'use client';

/**
 * PatientCard — 患者 1 名分のドラッグ可能なカード (W3-FE5).
 *
 * 設計仕様書 v0.9 §3.6.2 (Layer 1) で示される「患者を時刻スロットへ」の
 * ドラッグソース。プール側 / 配置済みセル側の双方で使い回す。
 *
 * dnd-kit の `useDraggable` を使い、`active.id` を経由して親の
 * `ScheduleGridV2` が drop 先を判定する。位置情報 (現在どこに配置されているか
 * = `placementId | null`) は親が保持し、本コンポーネントは表示と +1 ボタン
 * UI のみを担当する。
 *
 * Wave 20: プールカードに名前・希望時間・条件バッジを表示。
 *   - 名前 (太字)
 *   - 希望時間 (formatPreferredTimeLabel + service_minutes)
 *   - バッジ: 女性のみ / 男性のみ / 複数 / 新規 (before_start)
 */
import * as React from 'react';
import { useDraggable } from '@dnd-kit/core';
import { CSS } from '@dnd-kit/utilities';
import { Plus, User, Users, X } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { genderPalette } from '@/lib/scheduling/timeline';
import { cn } from '@/lib/utils';

export interface PatientCardData {
  id: string;
  name: string;
  /**
   * 患者の性別 (patient.sex: male/female/unknown)。指定するとプールカードが
   * タイムラインの訪問カードと同じ視覚言語 (性別ウォッシュ地 + 左色帯) になる。
   * 未指定 = 従来意匠 (後方互換)。
   */
  sex?: string | null;
  /** 表示用の追加メタ (kana / 保険など). 任意. */
  caption?: string | null;
  /**
   * Wave 18 Phase B-4: プールカードに表示する希望時間.
   * 例: "10:00 (固定)" / "09:30〜10:00 (時間帯)" / "午前". 任意.
   * `caption` の下に小さく表示する。
   */
  preferredTimeLabel?: string | null;
  /**
   * Wave 20: サービス時間 (分). 希望時間ラベルに併記する。
   * 例: preferredTimeLabel="10:00 (固定)" + serviceMinutes=30 → "10:00 (固定) / 30分"
   */
  serviceMinutes?: number | null;
  /**
   * Wave 20: 性別制限.
   * 'female_only' → 「女性のみ」バッジ, 'male_only' → 「男性のみ」バッジ.
   */
  sexRestriction?: 'female_only' | 'male_only' | null;
  /**
   * Wave 20: 2 名体制での訪問が必要かどうか.
   * true → 「複数」バッジ.
   */
  requiresMultipleStaff?: boolean | null;
  /**
   * Wave 20: 患者ステータス.
   * 'before_start' → 「新規」バッジ.
   */
  patientStatus?: string | null;
  /**
   * NG スタッフ登録件数 (`docs/plans/patient-ng-staff-design.md` §8-2 Phase 2).
   * 1 以上 → 「NGあり」バッジ。未指定 / 0 → 非表示 (= 既存挙動)。
   * 正典は `GET /patients/{id}/ng-staff`。ここは患者一覧の派生カウントを素通しする。
   */
  ngStaffCount?: number | null;
  /**
   * W37 Phase 3-B: 複数スタッフ対応患者の slot index (0 or 1).
   * - `null` / `undefined`: 通常 1 名体制 (= 既存挙動)。
   * - `0`: スロット ① (右上に「①」バッジ + 左ボーダー強調)。
   * - `1`: スロット ② (右上に「②」バッジ + 左ボーダー強調)。
   * `requiresMultipleStaff` が true でも `slotIndex` 未指定なら badge 非表示。
   */
  slotIndex?: 0 | 1 | null;
  /**
   * W37 Phase 3-B: 相方スロットが既に配置済みかどうか.
   * true のとき「① 配置済み」/「② 配置済み」バッジ (相手スロット番号) を追加表示。
   */
  partnerAssigned?: boolean | null;
  /**
   * Wave 38: 相方の現在地ラベル (例: "本店-A 15:00").
   *
   * - `partnerAssigned=true` でかつこの label が指定されたとき、
   *   "① 配置済み: 本店-A 15:00" のように配置済み行に併記する.
   * - `partnerAssigned=true` だが label が null のときはバッジのみ (= 既存挙動).
   * - 通常患者では常に null / 無視される.
   */
  partnerLocationLabel?: string | null;
  /**
   * Phase G-44: 「希望週訪問回数 vs 配置済」 の不足カウント表示用.
   *
   * - undefined / null → 表示なし (= 希望未設定 or 不要 = 既存挙動).
   * - `{ desired, actual, shortage }` を渡すと
   *   「希望 N、配置 X、不足 Y」 のラベルを controlled に出す.
   *
   * 表示判定は呼出側 (CourseDayTablePanel) が行い、 PatientCard は素朴に表示する.
   * shortage>0 のときは赤系バッジ + ラベルで視覚的強調する.
   */
  shortageInfo?: { desired: number; actual: number; shortage: number } | null;
}

export interface PatientCardProps {
  /** dnd-kit draggable id. プール用は `pool:<patient_id>`, 配置済みは
   *  `placement:<placement_id>` のように親側で組み立てる。 */
  draggableId: string;
  patient: PatientCardData;
  /** 配置済みカードでのみ表示する staff_count (1 or 2). undefined の場合は
   *  プール側カードと判断して +1 ボタンを描画しない。 */
  staffCount?: 1 | 2;
  /** +1 / -1 トグル。`staffCount` が定義されているときのみ呼ばれる。 */
  onToggleStaffCount?: () => void;
  /** 配置済みカードの「プールに戻す」(= 配置解除) ボタン. */
  onUnassign?: () => void;
  /** disabled 状態 (RBAC で staff / fix mutation 中など). */
  disabled?: boolean;
  /**
   * コンパクト表示モード (同一スロット複数エントリ時の横並び用).
   * true のとき caption / +1 ボタンを非表示にして幅を節約する。
   */
  compact?: boolean;
  /**
   * カード本体クリック (= ドラッグではない単純クリック) のハンドラ.
   * プール側カードで患者スケジュール詳細を開く導線に使う。
   * dnd-kit の activationConstraint (6px) によりドラッグ中は drag が優先され、
   * 加えて pointerdown→click の移動量が大きい場合 (= ドラッグ) は発火しない。
   * 未指定ならクリックは無反応 (従来どおり D&D 専用)。
   */
  onCardClick?: () => void;
  /**
   * DragOverlay 用ゴーストモード。true のとき draggable として機能せず
   * (id は ghost- 接頭辞・disabled)、オーバーレイいっぱい (h-full/w-full) に
   * カードと同じ内容を描く。ドラッグ中も表示情報を 1 つも落とさないための流用。
   */
  ghost?: boolean;
  /**
   * 新人同行モードでの選択状態。true のとき ring でハイライトする
   * (タイムラインの訪問カード選択と同じ視覚言語 = ring-2 ring-brand-primary)。
   */
  selected?: boolean;
}

/**
 * 1 患者カード (drag handle 兼コンテナ).
 *
 * - 高さ: ~28px (15min セル 1 つ分にちょうど収まる)
 * - hover で軽くハイライト + cursor-grab
 * - dragging 中は半透明化 (DragOverlay は親側で描画)
 */
export function PatientCard({
  draggableId,
  patient,
  staffCount,
  onToggleStaffCount,
  onUnassign,
  disabled = false,
  compact = false,
  onCardClick,
  ghost = false,
  selected = false,
}: PatientCardProps) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    // ghost は DragOverlay 内の描画専用。実 draggable と id が衝突しないよう接頭辞を付け
    // disabled で登録だけに留める (hooks は無条件呼び出しが必要なため分岐しない)。
    id: ghost ? `ghost-overlay:${draggableId}` : draggableId,
    disabled: disabled || ghost,
    data: { patientId: patient.id, staffCount },
  });

  // クリック / ドラッグ判別:
  //  (1) pointerdown 位置を記録し、click 時の移動量が PointerSensor の
  //      activationConstraint (6px) を超えていたらドラッグ扱いで無反応にする。
  //  (2) TouchSensor は delay ベース (250ms 長押し) のため移動量が小さくても
  //      ドラッグが成立しうる。drop 後 isDragging は false に戻るので、isDragging を
  //      ref で記憶し、直後の click を抑制する (タッチ長押しドラッグの誤爆防止)。
  const pointerDownPos = React.useRef<{ x: number; y: number } | null>(null);
  const draggedRef = React.useRef(false);
  React.useEffect(() => {
    if (isDragging) draggedRef.current = true;
  }, [isDragging]);
  const handleClick = React.useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!onCardClick) return;
      // 直前にドラッグが成立していたら click を無視してフラグを戻す。
      if (draggedRef.current) {
        draggedRef.current = false;
        pointerDownPos.current = null;
        return;
      }
      const start = pointerDownPos.current;
      pointerDownPos.current = null;
      if (start && (Math.abs(e.clientX - start.x) > 6 || Math.abs(e.clientY - start.y) > 6)) {
        return; // ポインタ移動がしきい値超 = ドラッグ
      }
      onCardClick();
    },
    [onCardClick],
  );

  const isPlaced = staffCount !== undefined;

  // W37 Phase 3-B: 複数スタッフ対応のスロット表示 (style 計算より先に判定が要る)。
  const slotIndexEarly = patient.slotIndex;
  const isMultiSlotCardEarly = slotIndexEarly === 0 || slotIndexEarly === 1;

  // タイムラインの訪問カードと同じ視覚言語 (性別ウォッシュ地 + 左色帯 + ink 文字色)。
  // sex が渡されたプールカードのみ適用 (配置済みカード/旧呼び出しは従来意匠のまま)。
  // 2名体制スロットカードは左帯 = brand-primary が識別マークなので、枠色は性別で
  // 上書きせず地色のみ性別ウォッシュにする (inline style は class に勝つため)。
  const pal = !isPlaced && patient.sex !== undefined ? genderPalette(patient.sex) : null;

  const style: React.CSSProperties = {
    transform: ghost ? undefined : CSS.Translate.toString(transform),
    opacity: !ghost && isDragging ? 0.4 : 1,
    ...(pal
      ? isMultiSlotCardEarly
        ? { background: pal.bg, color: pal.ink }
        : {
            background: pal.bg,
            borderColor: pal.ln,
            borderLeftColor: pal.bar,
            color: pal.ink,
          }
      : {}),
  };

  // Wave 20: バッジ表示フラグ
  const femaleOnly = patient.sexRestriction === 'female_only';
  const maleOnly = patient.sexRestriction === 'male_only';
  const multi = patient.requiresMultipleStaff === true;
  const isNew = patient.patientStatus === 'before_start';
  // NG スタッフあり (§8-2 Phase 2). 件数は出さず「あり/なし」だけを示す。
  const hasNgStaff = (patient.ngStaffCount ?? 0) > 0;
  const hasBadges = femaleOnly || maleOnly || multi || isNew || hasNgStaff;

  // W37 Phase 3-B: 複数スタッフ対応のスロット表示
  // slotIndex が 0/1 のときのみ ①/② バッジ + 左ボーダー強調を出す。
  const slotIndex = patient.slotIndex;
  const isMultiSlotCard = slotIndex === 0 || slotIndex === 1;
  const slotMark = slotIndex === 0 ? '①' : slotIndex === 1 ? '②' : null;
  const partnerSlotMark = slotIndex === 0 ? '②' : slotIndex === 1 ? '①' : null;
  const partnerAssigned = patient.partnerAssigned === true;
  const slotTooltip = isMultiSlotCard ? `2 名体制 (slot ${(slotIndex as 0 | 1) + 1}/2)` : undefined;

  // Wave 20: 希望時間 + サービス時間の組み立て
  const timeText = patient.preferredTimeLabel
    ? patient.serviceMinutes
      ? `${patient.preferredTimeLabel} / ${patient.serviceMinutes}分`
      : patient.preferredTimeLabel
    : null;

  return (
    <div
      ref={setNodeRef}
      style={style}
      title={slotTooltip}
      data-slot-index={isMultiSlotCard ? slotIndex : undefined}
      data-accompaniment-selected={selected ? 'true' : undefined}
      data-testid={isMultiSlotCard ? `patient-card-slot-${slotIndex}-${patient.id}` : undefined}
      className={cn(
        'group flex items-start justify-between gap-1 border px-2 py-1 text-xs',
        'select-none touch-none',
        // 性別ウォッシュ適用時はタイムラインカードと同じ丸み/左帯/影ホバー
        // (inline style の background が hover:bg-* に勝つため hover は影で表現)。
        pal
          ? 'rounded-lg border-l-[3px] shadow-[var(--shadow-xs)] transition-shadow hover:shadow-[var(--shadow-md)]'
          : cn(
              'rounded',
              isPlaced
                ? 'border-brand-primary/40 bg-brand-primary/5 text-text-primary'
                : 'border-border-default bg-bg-base text-text-primary hover:bg-bg-muted',
            ),
        // W37 Phase 3-B: 2 名体制カードはペアで同色の左ボーダーを強調表示
        isMultiSlotCard && 'border-l-4 border-l-brand-primary',
        // 新人同行モードの選択ハイライト (タイムラインカードと同言語)。
        selected && 'z-[1] ring-2 ring-brand-primary',
        ghost
          ? 'h-full w-full cursor-grabbing shadow-[var(--shadow-md)]'
          : disabled
            ? 'cursor-not-allowed opacity-60'
            : 'cursor-grab active:cursor-grabbing',
      )}
      {...listeners}
      {...attributes}
      onPointerDownCapture={(e) => {
        pointerDownPos.current = { x: e.clientX, y: e.clientY };
      }}
      onClick={onCardClick ? handleClick : undefined}
      data-clickable={onCardClick ? 'true' : undefined}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-1">
          {staffCount === 2 ? (
            <Users className="h-3 w-3 shrink-0 text-brand-primary" aria-hidden />
          ) : (
            <User className="h-3 w-3 shrink-0 text-text-muted" aria-hidden />
          )}
          <span className={cn('truncate', pal ? 'font-bold' : 'font-medium')} title={patient.name}>
            {patient.name}
          </span>
          {/* W37 Phase 3-B: スロット番号 (①/②) を氏名直後に表示 */}
          {slotMark ? (
            <span
              className="shrink-0 text-brand-primary"
              data-testid={`patient-card-slot-mark-${patient.id}`}
              aria-label={`スロット ${(slotIndex as 0 | 1) + 1}`}
            >
              {slotMark}
            </span>
          ) : null}
          {patient.caption && !compact ? (
            <span className="truncate text-text-muted">({patient.caption})</span>
          ) : null}
        </div>
        {/* Wave 18 Phase B-4 / Wave 20: 希望時間 + サービス時間 (プールカード) */}
        {timeText && !compact ? (
          <span
            className="tnum truncate pl-4 text-[10px] text-text-muted"
            data-testid={`patient-card-preferred-time-${patient.id}`}
          >
            {timeText}
          </span>
        ) : null}
        {/* Wave 20: 条件バッジ */}
        {hasBadges && !compact ? (
          <div
            className="flex flex-wrap gap-1 pl-4"
            data-testid={`patient-card-badges-${patient.id}`}
          >
            {femaleOnly && (
              <Badge
                variant="warning"
                className="h-4 px-1 text-[10px]"
                data-testid={`patient-card-badge-female-only-${patient.id}`}
              >
                女性のみ
              </Badge>
            )}
            {maleOnly && (
              <Badge
                variant="warning"
                className="h-4 px-1 text-[10px]"
                data-testid={`patient-card-badge-male-only-${patient.id}`}
              >
                男性のみ
              </Badge>
            )}
            {multi && (
              <Badge
                variant="info"
                className="h-4 px-1 text-[10px]"
                data-testid={`patient-card-badge-multi-${patient.id}`}
              >
                複数
              </Badge>
            )}
            {isNew && (
              <Badge
                variant="default"
                className="h-4 px-1 text-[10px]"
                data-testid={`patient-card-badge-new-${patient.id}`}
              >
                新規
              </Badge>
            )}
            {hasNgStaff && (
              <Badge
                variant="warning"
                className="h-4 px-1 text-[10px]"
                data-testid={`patient-card-badge-ng-staff-${patient.id}`}
                title="この患者には NG スタッフが設定されています（患者マスタで確認）"
              >
                NGあり
              </Badge>
            )}
          </div>
        ) : null}
        {/*
          Phase G-44: 「希望 N、配置 X、不足 Y」 表示.
          - shortageInfo が指定された patient (= 希望未設定でない) のみ表示.
          - shortage>0 → 赤系バッジ + 強調. shortage=0 → 控えめな緑表示.
          - 配置済みカード (isPlaced=true) でも表示する (= 「配置済だが不足あり」 = 別曜日に追加要).
          - compact モードでも表示 (= ステータス把握が UX 上重要).
         */}
        {!isPlaced && patient.shortageInfo ? (
          <div
            className="flex flex-wrap items-center gap-1 pl-4"
            data-testid={`patient-card-shortage-${patient.id}`}
            data-shortage={patient.shortageInfo.shortage}
          >
            {patient.shortageInfo.shortage > 0 ? (
              <Badge
                variant="warning"
                className="h-4 border-error/40 bg-error/10 px-1 text-[10px] text-error"
                data-testid={`patient-card-shortage-badge-${patient.id}`}
              >
                不足 {patient.shortageInfo.shortage}
              </Badge>
            ) : null}
            <span
              className="tnum text-[10px] text-text-muted"
              data-testid={`patient-card-shortage-label-${patient.id}`}
            >
              希望 {patient.shortageInfo.desired} / 配置 {patient.shortageInfo.actual}
            </span>
          </div>
        ) : null}
        {/*
          W37 Phase 3-B / Wave 38: 相方スロットが既に配置済みであることを示すバッジ.
          - 自身が slot 1 の残カードなら「① 配置済み」を表示。
          - 自身が slot 0 の残カードなら「② 配置済み」を表示。
          - compact 表示でも見せたいので別 row で出す。
          - Wave 38: partnerLocationLabel が指定されたら "{slotMark} 配置済み: {label}"
            の形でセル位置 + 時刻も併記する (例: "① 配置済み: 本店-A 15:00")。
        */}
        {isMultiSlotCard && partnerAssigned && partnerSlotMark ? (
          <div
            className="flex flex-wrap items-center gap-1 pl-4"
            data-testid={`patient-card-partner-assigned-${patient.id}`}
          >
            <Badge variant="info" className="h-4 px-1 text-[10px]">
              {partnerSlotMark} 配置済み
            </Badge>
            {patient.partnerLocationLabel ? (
              <span
                className="truncate text-[10px] text-text-muted"
                data-testid={`patient-card-partner-location-${patient.id}`}
                title={patient.partnerLocationLabel}
              >
                {patient.partnerLocationLabel}
              </span>
            ) : null}
          </div>
        ) : null}
      </div>

      {isPlaced ? (
        <div className="flex shrink-0 items-center gap-0.5">
          {/* +1 トグル: compact モードでは非表示 (幅を節約) */}
          {onToggleStaffCount && !compact ? (
            <button
              type="button"
              className={cn(
                'rounded px-1 py-0.5 text-[10px] font-semibold leading-none',
                staffCount === 2
                  ? 'bg-brand-primary text-white'
                  : 'bg-bg-muted text-text-secondary hover:bg-brand-primary/20',
              )}
              onClick={(e) => {
                e.stopPropagation();
                onToggleStaffCount();
              }}
              onPointerDown={(e) => e.stopPropagation()}
              disabled={disabled}
              aria-label={staffCount === 2 ? '1 名体制に戻す' : '2 名体制にする'}
              title={staffCount === 2 ? '1 名体制に戻す' : '2 名体制にする'}
            >
              {staffCount === 2 ? '×2' : '+1'}
            </button>
          ) : null}
          {onUnassign ? (
            <button
              type="button"
              className="rounded p-0.5 text-text-muted hover:bg-error/10 hover:text-error"
              onClick={(e) => {
                e.stopPropagation();
                onUnassign();
              }}
              onPointerDown={(e) => e.stopPropagation()}
              disabled={disabled}
              aria-label="プールに戻す"
              title="プールに戻す"
            >
              <X className="h-3 w-3" />
            </button>
          ) : null}
        </div>
      ) : (
        <Plus
          className="h-3 w-3 shrink-0 text-text-muted opacity-0 group-hover:opacity-100"
          aria-hidden
        />
      )}
    </div>
  );
}
