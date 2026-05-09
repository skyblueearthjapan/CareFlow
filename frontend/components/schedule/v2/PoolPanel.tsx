'use client';

/**
 * PoolPanel — 保留プール (W3-FE5).
 *
 * 設計 v0.9 §3.6.2 (Layer 1) の「未配置プール」。新規患者や時刻スロットから
 * 解除された患者をここに溜め、ドラッグでセルに配置する。配置済みカードを
 * 本パネルにドロップすると配置解除 (= weekly_pattern entry を削除予定として
 * 親 state に戻す) になる。
 *
 * 1 つの大きな drop target として `useDroppable` を取り、子に PatientCard を
 * 並べる。空のときも drop 可能であることを示すプレースホルダを描画する。
 *
 * Wave 18 Phase B-3 (希望曜日別グループ化):
 *   - `PoolGroupedByWeekday` を併記。プール内 patient を
 *     `weekly_pattern.preferred_weekdays` でセクション分けし、各セクション
 *     は折りたたみ可能 (`<details>` ベース)。
 *
 * W37 Phase 3-B (複数スタッフ対応の 2 カード化):
 *   - `requires_multiple_staff=true` 患者を slot 0 / slot 1 の 2 枚の
 *     PatientCard として描画。draggableId は `pool-patient:{id}:slot:0` /
 *     `pool-patient:{id}:slot:1` の 2 つに分かれ、カード上部に ①/② バッジを
 *     表示する。
 *   - 親から `assignedSlotsByPatient` を受け取り、片方の slot のみ配置済みの
 *     場合は未配置 slot の 1 枚だけ残す (Phase 3-C で配線、Phase 3-B では
 *     prop シグネチャのみ確定)。
 *   - `parsePoolDraggableId` を export し、Phase 3-C の place-and-fix 呼出側
 *     が draggableId からスロットを判定できるようにする。
 */
import * as React from 'react';
import { useDroppable } from '@dnd-kit/core';
import { Inbox } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import {
  WEEKDAY_KEYS,
  WEEKDAY_LABELS_JA,
  coerceWeeklyPattern,
  type WeekdayKey,
  type PatientRead,
} from '@/lib/schemas/patient';
import type { SlotIndex } from '@/lib/schemas/v2/patient_fixed_visit';

export const POOL_DROPPABLE_ID = 'pool';

// ─────────────────────────────────────────────────────────────────────────
// W37 Phase 3-B: pool draggable id helpers (multi-staff slot 0/1)
// ─────────────────────────────────────────────────────────────────────────

/**
 * プール用 draggable id のプレフィックス.
 * 既存の {@link CourseDayTablePanel} `patientDraggableId` と一致させる.
 */
const POOL_DRAGGABLE_PREFIX = 'pool-patient:';

/**
 * 複数スタッフ対応患者の slot サフィックスを組み立てる.
 *
 * @example
 *   buildPoolDraggableId('p1')           // → "pool-patient:p1"        (単独)
 *   buildPoolDraggableId('p1', 0)        // → "pool-patient:p1:slot:0" (multi)
 *   buildPoolDraggableId('p1', 1)        // → "pool-patient:p1:slot:1" (multi)
 */
export function buildPoolDraggableId(patientId: string, slotIndex?: SlotIndex | null): string {
  if (slotIndex === 0 || slotIndex === 1) {
    return `${POOL_DRAGGABLE_PREFIX}${patientId}:slot:${slotIndex}`;
  }
  return `${POOL_DRAGGABLE_PREFIX}${patientId}`;
}

/**
 * プール用 draggable id を分解する.
 *
 * - 旧形式 `pool-patient:{patient_id}` → `{ patientId, slotIndex: null }`
 *   (後方互換: 1 名体制患者および全ての既存 D&D 経路)。
 * - 新形式 `pool-patient:{patient_id}:slot:0|1` → 対応する slotIndex。
 * - プレフィックス不一致 → `null`。
 *
 * Phase 3-C の place-and-fix 呼出側はこの helper で slotIndex を取り、
 *   - `slotIndex === null` → 通常の 1 名体制 (staff_count=1)
 *   - `slotIndex === 0 | 1` → 複数スタッフ対応 (staff_count=2, 2 visit を作成)
 * を分岐する想定。
 */
export function parsePoolDraggableId(
  id: string,
): { patientId: string; slotIndex: SlotIndex | null } | null {
  if (!id.startsWith(POOL_DRAGGABLE_PREFIX)) return null;
  const rest = id.slice(POOL_DRAGGABLE_PREFIX.length);
  const match = rest.match(/^(.+):slot:([01])$/);
  if (match) {
    const slot = Number(match[2]) as SlotIndex;
    return { patientId: match[1] as string, slotIndex: slot };
  }
  return { patientId: rest, slotIndex: null };
}

// ─────────────────────────────────────────────────────────────────────────
// Wave 18 Phase B-3: PoolGroupedByWeekday
// (旧 PoolPanel / PoolPanelProps は W17 時代のシンプル版。Wave 18 B-3 で
//  PoolGroupedByWeekday に置き換え済みのため削除。現在の利用箇所はなし。)
// ─────────────────────────────────────────────────────────────────────────

/** プール患者のグループ化キー: WeekdayKey の配列を文字列化 (canonical 順)。 */
export function poolGroupKey(preferredWeekdays: WeekdayKey[] | null | undefined): string {
  const arr = Array.isArray(preferredWeekdays)
    ? [...preferredWeekdays]
        .filter((d): d is WeekdayKey => (WEEKDAY_KEYS as readonly string[]).includes(d as string))
        .sort((a, b) => WEEKDAY_KEYS.indexOf(a) - WEEKDAY_KEYS.indexOf(b))
    : [];
  if (arr.length === 0) return '__none__';
  return arr.join(',');
}

/** グループキーを表示用ラベルに変換: 'Mon,Wed,Fri' → '月・水・金 希望' / '__none__' → '希望なし' */
export function poolGroupLabel(key: string): string {
  if (key === '__none__') return '希望なし';
  const parts = key.split(',') as WeekdayKey[];
  if (parts.length === 1) return `${WEEKDAY_LABELS_JA[parts[0]!]}希望`;
  return `${parts.map((p) => WEEKDAY_LABELS_JA[p]).join('・')} 希望`;
}

/**
 * W37 Phase 3-B: 複数スタッフ対応カードの追加メタ情報.
 *
 * `PoolGroupedByWeekday` から `renderCard` に渡される。
 * 通常患者 (`requires_multiple_staff !== true`) では `slotIndex=null` で
 * 既存の 1 カード描画と同じ挙動になる (後方互換)。
 */
export interface PoolCardSlotInfo {
  /**
   * 描画対象スロット.
   * - `null`: 1 名体制 (= 既存挙動)。1 patient = 1 card。
   * - `0` / `1`: 2 名体制患者の片側スロット。同患者で 2 回 renderCard が呼ばれる。
   */
  slotIndex: SlotIndex | null;
  /**
   * 当該 patient の相方 slot がすでに配置済みかどうか.
   * Phase 3-B では `assignedSlotsByPatient` 経由で計算し、片方ドロップ後の
   * 残カードに「① 配置済み」/「② 配置済み」バッジを出すフラグ用に
   * renderCard へ渡す。通常患者 / 両方未配置 → false。
   */
  partnerAssigned: boolean;
}

export interface PoolGroupedByWeekdayProps {
  /** 表示対象 patient (parent でフィルタ済み)。 */
  patients: PatientRead[];
  /**
   * patient → カード React 要素のレンダラ。親が `PatientCard` (draggableId 付き)
   * を組み立てる。グループごとにマウントされるため key は patient.id を推奨。
   *
   * W37 Phase 3-B: 第 2 引数 `slotInfo` を受け取り、
   *   - 通常患者: `{ slotIndex: null, partnerAssigned: false }` で 1 回呼ばれる。
   *   - 複数スタッフ対応患者: `{ slotIndex: 0, ... }` と `{ slotIndex: 1, ... }`
   *     で 2 回呼ばれる (片方既配置なら未配置 slot 1 回のみ)。
   * 第 2 引数は任意で、旧シグネチャ `(patient) => ReactNode` のままでも動作する。
   */
  renderCard: (patient: PatientRead, slotInfo: PoolCardSlotInfo) => React.ReactNode;
  /** プール全体の disabled (RBAC)。drop target も無効化。 */
  disabled?: boolean;
  /**
   * W37 Phase 3-B: 既配置スロットマップ (patient_id → 配置済み slot index 集合).
   *
   * - 当該患者のキーが存在しない / 空集合 → 全 slot 未配置 (= 既存挙動)。
   * - `Set([0])` → slot 0 配置済み (slot 1 のみカード表示 + 「① 配置済み」バッジ)。
   * - `Set([1])` → slot 1 配置済み (slot 0 のみカード表示 + 「② 配置済み」バッジ)。
   * - `Set([0, 1])` → 両方配置済み (= プールに何も表示しない / 親側の
   *   `placedPatientIds` でも除外されているはず)。
   *
   * Phase 3-B 時点では未指定 (= undefined) を許容し、未指定なら全患者の全 slot
   * が未配置とみなして両カードを描画する。Phase 3-C で `CourseDayTablePanel`
   * 側に weekVisits → assignedSlotsByPatient の構築ロジックを実装する。
   */
  assignedSlotsByPatient?: Map<string, Set<SlotIndex>>;
}

/**
 * 患者を `weekly_pattern.preferred_weekdays` でセクション分けして表示する。
 * 1 セクション = 1 グループ (例: 「月希望」「月・水・金 希望」「希望なし」)。
 * 各セクションは `<details open>` で折りたたみ可。
 *
 * drop target は `PoolPanel` と同じ `POOL_DROPPABLE_ID` を 1 つだけ取る
 * (親の onDragEnd でセル → プール戻しを判定)。
 */
/**
 * W37 Phase 3-B: 1 patient あたり描画すべき slot 一覧と相方の状況を計算する.
 *
 * 戻り値配列 1 要素 = 1 カード分。
 *   - 通常患者 (`requires_multiple_staff !== true`):
 *       `[{ slotIndex: null, partnerAssigned: false }]` (常に 1 カード)。
 *   - 複数スタッフ対応患者:
 *       両 slot 未配置 → `[{slot:0, partnerAssigned:false}, {slot:1, ...:false}]`
 *       slot 0 のみ配置 → `[{slot:1, partnerAssigned:true}]` (= ② 1 枚 + バッジ)
 *       slot 1 のみ配置 → `[{slot:0, partnerAssigned:true}]` (= ① 1 枚 + バッジ)
 *       両配置 → `[]` (空。親側 placedPatientIds でも除外される想定)。
 */
function computeCardSlots(
  patient: PatientRead,
  assignedSlots: Set<SlotIndex> | undefined,
): PoolCardSlotInfo[] {
  const requiresMulti =
    (patient as { requires_multiple_staff?: boolean | null }).requires_multiple_staff === true;
  if (!requiresMulti) {
    return [{ slotIndex: null, partnerAssigned: false }];
  }
  const has0 = assignedSlots?.has(0) ?? false;
  const has1 = assignedSlots?.has(1) ?? false;
  if (has0 && has1) return [];
  if (has0) return [{ slotIndex: 1, partnerAssigned: true }];
  if (has1) return [{ slotIndex: 0, partnerAssigned: true }];
  return [
    { slotIndex: 0, partnerAssigned: false },
    { slotIndex: 1, partnerAssigned: false },
  ];
}

/**
 * W37 Phase 3-B: 当該グループの「内 N 名 2 名対応」のサブカウント文字列を組み立てる.
 * 0 名なら null (バッジ非表示)。
 */
function multiStaffSubCountLabel(items: PatientRead[]): string | null {
  let n = 0;
  for (const p of items) {
    if ((p as { requires_multiple_staff?: boolean | null }).requires_multiple_staff === true) n++;
  }
  if (n === 0) return null;
  return `内 2名対応 ${n} 名`;
}

export function PoolGroupedByWeekday({
  patients,
  renderCard,
  disabled = false,
  assignedSlotsByPatient,
}: PoolGroupedByWeekdayProps) {
  const { isOver, setNodeRef } = useDroppable({
    id: POOL_DROPPABLE_ID,
    disabled,
    data: { kind: 'pool' },
  });

  const grouped = React.useMemo(() => {
    const m = new Map<string, PatientRead[]>();
    for (const p of patients) {
      const wp = coerceWeeklyPattern(p.weekly_pattern);
      const key = poolGroupKey(wp.preferred_weekdays);
      const arr = m.get(key) ?? [];
      arr.push(p);
      m.set(key, arr);
    }
    // 出力順: 単曜日 (Mon..Sun) → 複数曜日 (alphabetical) → 希望なし
    const keys = [...m.keys()].sort((a, b) => {
      if (a === '__none__') return 1;
      if (b === '__none__') return -1;
      const aLen = a.split(',').length;
      const bLen = b.split(',').length;
      if (aLen !== bLen) return aLen - bLen;
      return a.localeCompare(b);
    });
    return keys.map((key) => ({ key, items: m.get(key) ?? [] }));
  }, [patients]);

  return (
    <Card className="p-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="flex items-center gap-1 text-sm font-semibold text-text-primary">
          <Inbox className="h-4 w-4" aria-hidden />
          保留プール (希望曜日別)
        </h2>
        <span className="tnum text-xs text-text-muted">{patients.length} 名</span>
      </div>
      <div
        ref={setNodeRef}
        data-pool="true"
        data-testid="pool-grouped-by-weekday"
        className={cn(
          'min-h-[64px] rounded border border-dashed p-2 transition-colors',
          isOver && !disabled
            ? 'border-brand-primary/60 bg-brand-primary/10'
            : 'border-border-default bg-bg-muted/40',
        )}
      >
        {patients.length === 0 ? (
          <p className="py-3 text-center text-xs text-text-muted">
            プールは空です。配置を解除した患者がここに戻ります。
          </p>
        ) : (
          <div className="space-y-2">
            {grouped.map(({ key, items }) => {
              const subCount = multiStaffSubCountLabel(items);
              return (
                <details
                  key={key}
                  open
                  className="rounded border border-border-default/60 bg-bg-base"
                  data-testid={`pool-group-${key}`}
                >
                  <summary className="cursor-pointer select-none rounded px-2 py-1 text-xs font-semibold text-text-secondary hover:bg-bg-muted">
                    {poolGroupLabel(key)}
                    <span className="tnum ml-2 text-[10px] font-normal text-text-muted">
                      ({items.length})
                    </span>
                    {subCount ? (
                      <span
                        className="tnum ml-1 text-[10px] font-normal text-brand-primary"
                        data-testid={`pool-group-multi-subcount-${key}`}
                      >
                        、{subCount}
                      </span>
                    ) : null}
                  </summary>
                  <div className="space-y-1 p-2">
                    {items.map((p) => {
                      const assigned = assignedSlotsByPatient?.get(p.id);
                      const slots = computeCardSlots(p, assigned);
                      return slots.map((info) => (
                        <React.Fragment
                          key={info.slotIndex === null ? p.id : `${p.id}:slot:${info.slotIndex}`}
                        >
                          {renderCard(p, info)}
                        </React.Fragment>
                      ));
                    })}
                  </div>
                </details>
              );
            })}
          </div>
        )}
      </div>
    </Card>
  );
}
