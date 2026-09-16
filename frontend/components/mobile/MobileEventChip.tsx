import { cn } from '@/lib/utils';

/**
 * 職員イベント (研修 / 会議 / 朝会 …) のチップ — スマホ版
 * (mobile-staff-schedule-design-2026-09-16.md §3 C-3)。
 *
 * 表示専用 = タップ不可。訪問カード (`MobileVisitCard`) と同じ日付グループの
 * 中に時系列で混ざるので、患者訪問 (性別ウォッシュの地色) と一目で区別が付く
 * よう緑系 (`bg-success/10 text-success`) に固定する。
 *
 * - `start_time === end_time` … 📝 メモ扱い (終了時刻は出さない)
 * - `cancelled_at != null`     … 「今週だけ外す」→ 打消線 + 「今週除外」バッジ
 * - `source === 'fixed'`       … 固定イベント (朝会) もそのまま出す
 *   (スマホは PC のような「全員（固定）」帯を作らない)
 */
export interface MobileEventChipEvent {
  id: string;
  title: string;
  /** HH:MM。 */
  start_time: string;
  /** HH:MM。 */
  end_time: string;
  /** 非 null = 今週だけ外されている。 */
  cancelled_at?: string | null;
  source?: string;
}

interface MobileEventChipProps {
  event: MobileEventChipEvent;
  /** テスト用の data-testid (既定は `mobile-event-chip-{id}`)。 */
  testId?: string;
  className?: string;
}

/**
 * "HH:MM:SS" でも "HH:MM" でも HH:MM にする。
 *
 * 型では `string` でも、旧デプロイ / 壊れた行では undefined が来うる
 * (2026-09-16 レビュー MEDIUM-7)。表示専用のチップが当日画面ごと道連れに
 * しないよう、undefined・短い文字列でも落ちずに空文字へ落とす。
 */
function shortTime(t: string | null | undefined): string {
  if (typeof t !== 'string') return '';
  return t.length >= 5 ? t.slice(0, 5) : t;
}

export function MobileEventChip({ event, testId, className }: MobileEventChipProps) {
  const start = shortTime(event.start_time);
  const end = shortTime(event.end_time);
  // 開始 === 終了 = 幅を持たない「メモ」。時刻だけ出す。
  const isMemo = start === end;
  const cancelled = event.cancelled_at != null;

  return (
    <div
      data-testid={testId ?? `mobile-event-chip-${event.id}`}
      className={cn(
        'flex items-center gap-1.5 rounded-md border border-success/30 bg-success/10 px-1.5 py-1 text-success',
        cancelled && 'opacity-60',
        className,
      )}
    >
      <span className="tnum shrink-0 text-[11px] font-semibold">
        {isMemo ? `📝 ${start}` : `${start}〜${end}`}
      </span>
      <span
        className={cn('min-w-0 flex-1 truncate text-[12px] font-bold', cancelled && 'line-through')}
      >
        {event.title || '(無題)'}
      </span>
      {cancelled && (
        <span className="shrink-0 rounded border border-success/40 px-1 text-[10px] font-semibold">
          今週除外
        </span>
      )}
    </div>
  );
}

/**
 * 休み / 時間変更 (`staff_weekly_overrides`) のバッジ。日付見出しの右に出す。
 *
 * イベントのチップと同じ「その日の働き方」の情報なので同じファイルに置く
 * (/m/today と /m/this-week の両方から使う)。
 */
export interface MobileOverrideBadgeOverride {
  type: string;
  start_time?: string | null;
  end_time?: string | null;
}

export function MobileOverrideBadge({
  override,
  testId,
}: {
  override: MobileOverrideBadgeOverride;
  testId?: string;
}) {
  const isTimeChange = override.type === '時間変更';
  const label =
    isTimeChange && override.start_time && override.end_time
      ? `⏱${shortTime(override.start_time)}〜${shortTime(override.end_time)}`
      : `🛌${override.type}`;

  return (
    <span
      data-testid={testId}
      className="shrink-0 rounded border border-warning/40 bg-warning/10 px-1.5 py-0.5 text-[11px] font-semibold text-warning"
    >
      {label}
    </span>
  );
}
