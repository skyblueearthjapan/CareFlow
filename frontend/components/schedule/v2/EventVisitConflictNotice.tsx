'use client';

/**
 * EventVisitConflictNotice — 取込イベント × 訪問の時間重なり警告 (案A・2026-08-21)。
 *
 * 取り込み自体は行われる (カイポケが正)。重なりは隠さず、
 * 「何と何が・どう重なっているか」と「次にどうすればよいか」を平易に示す。
 * 職員スケジュールタブの取込ダイアログと連携ページの両方で使う。
 */
import type { EventsInboundConflict } from '@/lib/schemas/integration';

const WEEKDAYS_JP = ['日', '月', '火', '水', '木', '金', '土'] as const;

function fmtJp(iso: string): string {
  const d = new Date(iso + 'T00:00:00');
  return `${d.getMonth() + 1}/${d.getDate()}（${WEEKDAYS_JP[d.getDay()]}）`;
}

export function EventVisitConflictNotice({
  conflicts,
}: {
  /** API 由来は zod default で必ず配列だが、テスト fixture 等の欠落にも耐える。 */
  conflicts: EventsInboundConflict[] | undefined;
}) {
  if (!conflicts || conflicts.length === 0) return null;
  return (
    <div
      className="rounded-md border border-warning/50 bg-warning-bg px-3 py-2"
      data-testid="event-visit-conflicts"
    >
      <p className="text-sm font-bold text-warning-strong">
        ⚠ 訪問と重なるイベントが {conflicts.length} 件あります
      </p>
      <ul className="mt-1.5 space-y-1">
        {conflicts.map((c, i) => (
          <li key={i} className="break-words text-xs leading-relaxed text-text-primary">
            <span className="tnum font-medium">{fmtJp(c.date)}</span>{' '}
            <span className="font-medium">{c.staffName}さん</span> — イベント「{c.eventTitle}」
            <span className="tnum">
              {c.eventStart}〜{c.eventEnd}
            </span>{' '}
            が <span className="font-medium">{c.patientName}様</span>の訪問{' '}
            <span className="tnum">
              {c.visitStart}〜{c.visitEnd}
            </span>{' '}
            と重なっています
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-text-secondary">
        イベント（休みなど）はカイポケの内容どおり取り込まれます。重なった訪問は、
        スケジュールの週・曜日タブで<b>担当の変更</b>または<b>時間の移動</b>をご検討ください。
        今後の自動スタッフ割当は、この時間帯を自動で避けるようになります。
      </p>
    </div>
  );
}
