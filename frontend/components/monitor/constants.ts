/**
 * 訪問モニターの表示ヘルパ (色 / ラベル / 実効状態の派生)。
 *
 * BE は phase (時間進捗) と alert_level (要対応) を別々に返す。タイムライン /
 * 詳細パネルでは 1 つの視覚状態に畳む必要があるため、ここで ``displayStatus`` を
 * 導出する。色は tokens.css の CSS 変数参照 (var(--status-*)) を使う
 * (gantt のバー色は Tailwind purge を避けるため inline style で使う)。
 */
import type { MonitorStaffRow, MonitorVisit } from '@/lib/schemas/monitor';

export type DisplayStatus =
  | 'match'
  | 'review'
  | 'mismatch'
  | 'inprogress'
  | 'missing'
  | 'future'
  | 'awaiting';

/** phase + alert_level を 1 つの視覚状態に畳む。 */
export function displayStatus(v: Pick<MonitorVisit, 'phase' | 'alert_level'>): DisplayStatus {
  if (v.phase === 'missing') return 'missing';
  if (v.phase === 'future') return 'future';
  if (v.phase === 'awaiting') return 'awaiting';
  if (v.alert_level === 'mismatch') return 'mismatch';
  if (v.alert_level === 'review') return 'review';
  if (v.phase === 'inprogress') return 'inprogress';
  return 'match'; // done + none
}

/**
 * ステータス別の色 (CSS 変数参照)。inline style / divIcon の html style で使用可。
 *
 * 注意: Leaflet のベクターレイヤ (Polyline/Circle の pathOptions) は SVG 属性のため
 * var() 不可。divIcon の html inline style では使用可。
 */
export const STATUS_COLOR: Record<DisplayStatus, string> = {
  match: 'var(--status-match)',
  review: 'var(--status-review)',
  mismatch: 'var(--status-mismatch)',
  inprogress: 'var(--status-inprogress)',
  missing: 'var(--status-missing)',
  future: 'var(--status-future)',
  awaiting: 'var(--status-awaiting)',
};

/**
 * 予定バーのハッチパターン背景。
 * repeating-linear-gradient 内は var() 使用可。
 * Leaflet pathOptions (SVG 属性) では不可だが、divIcon の html inline style では使用可。
 */
export const PLAN_BAR_BG =
  'repeating-linear-gradient(90deg,var(--border-default),var(--border-default) 6px,var(--border-subtle) 6px,var(--border-subtle) 12px)';

/** 予定バーの枠線色。tokens.css --border-strong に対応。 */
export const PLAN_BAR_BORDER = 'var(--border-strong)';

/**
 * 未訪問バーのハッチパターン背景。
 * #ef4444 は --error の明色として直値維持 (デザインシステム補足色)。
 */
export const MISSING_BAR_BG =
  'repeating-linear-gradient(45deg,var(--error),var(--error) 5px,#ef4444 5px,#ef4444 10px)';

export const STATUS_LABEL: Record<DisplayStatus, string> = {
  match: '一致',
  review: '要確認',
  mismatch: '不一致',
  inprogress: '訪問中',
  missing: '未訪問',
  future: '予定',
  awaiting: '到着待ち',
};

/** 詳細パネルの判定見出し (アイコン + 文言)。 */
export const STATUS_JUDGE: Record<DisplayStatus, [string, string]> = {
  match: ['✓', '登録住所と一致'],
  review: ['⚠', '要確認（遅延／距離）'],
  mismatch: ['✕', '場所違いの可能性'],
  inprogress: ['●', '訪問中'],
  missing: ['⚠', '未訪問・未記録'],
  future: ['○', 'これからの予定'],
  awaiting: ['…', '到着待ち'],
};

/** アラートトレイの優先順 (未訪問 → 場所違い → 要確認)。 */
export const ALERT_RANK: Record<string, number> = {
  missing: 0,
  mismatch: 1,
  review: 2,
};

/** 要対応 (アラートトレイに載せる) か。 */
export function isAlert(v: Pick<MonitorVisit, 'alert_level'>): boolean {
  return v.alert_level === 'missing' || v.alert_level === 'mismatch' || v.alert_level === 'review';
}

/**
 * 退出忘れ (長時間 inprogress) の表示しきい値 (分) の既定。BE の
 * checkin_settings.max_inprogress_min が無いときのフォールバック。
 * 通常はモニター応答の ``thresholds.max_inprogress_min`` を渡して動的化する。
 */
export const MAX_INPROGRESS_MIN = 240;

/**
 * 退出未記録のまま長時間 inprogress (退出忘れの可能性) か。
 *
 * ``maxInprogressMin`` はモニター応答 (``thresholds.max_inprogress_min``) の値を渡す。
 * 省略時は既定 240 にフォールバックする (ハードコード排除)。
 */
export function isLongInprogress(
  v: Pick<MonitorVisit, 'phase' | 'departure' | 'stay_minutes'>,
  maxInprogressMin: number = MAX_INPROGRESS_MIN,
): boolean {
  return (
    v.phase === 'inprogress' &&
    v.departure == null &&
    v.stay_minutes != null &&
    v.stay_minutes > maxInprogressMin
  );
}

/** 退出忘れ (長時間 inprogress) の理由文言。 */
export const LONG_INPROGRESS_REASON = '長時間訪問中（退出未記録の可能性）';

/**
 * 2 名体制 (visit_group_id) を 1 論理訪問に重複排除したグループ。
 *
 * - ``visit_group_id`` が同じ visit (2 スタッフ分) を 1 件に束ねる。null は visit.id 単位。
 * - ``representative`` は worst(alert_level) のメンバ (表示・KPI バケット判定に使う)。
 * - ``staffNames`` はグループに関与した全スタッフ名 (重複排除)。
 */
export interface VisitGroup {
  key: string;
  representative: MonitorVisit;
  members: MonitorVisit[];
  staffNames: string[];
  worstAlertLevel: string;
  isPair: boolean;
}

/** alert_level の worst 度 (小さいほど重大)。none は最大値で末尾。 */
function alertSeverity(level: string): number {
  return ALERT_RANK[level] ?? 9;
}

/** スタッフ行をまたいで visit_group_id 単位に重複排除する。 */
export function groupVisits(rows: MonitorStaffRow[]): VisitGroup[] {
  const order: string[] = [];
  const map = new Map<string, { members: MonitorVisit[]; staffNames: string[] }>();
  for (const row of rows) {
    for (const v of row.visits) {
      const key = v.visit_group_id ?? v.visit_id;
      let g = map.get(key);
      if (!g) {
        g = { members: [], staffNames: [] };
        map.set(key, g);
        order.push(key);
      }
      g.members.push(v);
      const name = row.staff_name;
      if (name && !g.staffNames.includes(name)) g.staffNames.push(name);
    }
  }
  return order.map((key) => {
    const { members, staffNames } = map.get(key)!;
    const representative = members.reduce((worst, v) =>
      alertSeverity(v.alert_level) < alertSeverity(worst.alert_level) ? v : worst,
    );
    return {
      key,
      representative,
      members,
      staffNames,
      worstAlertLevel: representative.alert_level,
      isPair: members.length > 1 || representative.visit_group_id != null,
    };
  });
}

/** "HH:MM:SS" / "HH:MM" → 分 (タイムライン座標計算用)。 */
export function hmToMinutes(hm: string): number {
  const [h, m] = hm.split(':');
  return Number(h) * 60 + Number(m);
}

/** 距離 (m) を人間可読に: <1km は 10m 丸め、>=1km は km。 */
export function formatDistance(m: number | null | undefined): string {
  if (m == null) return '—';
  return m < 1000 ? `${Math.round(m / 10) * 10}m` : `${(m / 1000).toFixed(1)}km`;
}

/** ISO 文字列 → "HH:MM" (JST 表示)。 */
export function isoToHm(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return new Intl.DateTimeFormat('ja-JP', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Tokyo',
  }).format(d);
}

/** ISO 文字列 → "M/D HH:MM" (JST 表示)。確認済みの日時表示用。 */
export function isoToYmdHm(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return new Intl.DateTimeFormat('ja-JP', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Tokyo',
  }).format(d);
}

/** タイムラインの時間軸範囲 (8:00–19:00)。 */
export const TL_START_MIN = 8 * 60;
export const TL_END_MIN = 19 * 60;
export const TL_SPAN_MIN = TL_END_MIN - TL_START_MIN;

/** 分 → タイムライン上の左 % 座標 (0..100 にクランプ; 範囲外バーは端に寄る)。 */
export function minutesToPct(min: number): number {
  const pct = ((min - TL_START_MIN) / TL_SPAN_MIN) * 100;
  return Math.max(0, Math.min(100, pct));
}
