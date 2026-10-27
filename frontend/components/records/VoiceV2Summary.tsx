'use client';

/**
 * v2 の要約（設計 v2 §3 P4）: 上に「バイタル」（語られた項目だけ）と「【要確認】」の
 * 一覧、その下に本文。PC の詳細ダイアログとスマホの記録カードで共通に使う。
 *
 * 人手で直した後（`summary_edited_at` あり）は `summary_text` が正なので、本文だけを
 * 同じ読み取りで出す（AI の facts は直した本文と食い違いうるので出さない）。
 */

import { TimeMark, VoiceMarkdown, type SeekHandler } from '@/components/records/VoiceMarkdown';
import { v2Body, v2Facts } from '@/components/records/recordFormat';
import type { VisitRecordingRead } from '@/lib/queries/visit-recordings';

export function VoiceV2Summary({
  rec,
  onSeek,
  edited = false,
  vitalColumns = 3,
}: {
  rec: Pick<VisitRecordingRead, 'summary' | 'summary_text'>;
  /** 音声が無い記録では渡さない（時刻は押せない文字になる）。 */
  onSeek?: SeekHandler | null;
  /** 人手で直した後か（本文だけを出す）。 */
  edited?: boolean;
  /** バイタルの列の数（PC 3・スマホ 2）。 */
  vitalColumns?: 2 | 3;
}) {
  if (edited) {
    return <VoiceMarkdown markdown={rec.summary_text} onSeek={onSeek} testId="voice-v2-body" />;
  }
  const { vitals, needsCheck } = v2Facts(rec.summary);
  // 本文は summary_text が正（現場の「追記」は summary_text にだけ足され、
  // summary_edited_at は立たない）。空のときだけ AI の body。
  const body =
    rec.summary_text && rec.summary_text.trim() !== '' ? rec.summary_text : v2Body(rec.summary);
  return (
    <div className="space-y-3" data-testid="voice-v2-summary">
      {vitals.length > 0 && (
        <section data-testid="voice-v2-vitals">
          <h4 className="text-xs font-bold tracking-wide text-text-muted">バイタル</h4>
          <dl className={`mt-1 grid gap-1.5 ${vitalColumns === 2 ? 'grid-cols-2' : 'grid-cols-3'}`}>
            {vitals.map(([k, v]) => (
              <div key={k} className="rounded-md bg-bg-muted px-2 py-1.5">
                <dt className="text-xs text-text-muted">{k}</dt>
                <dd className="tnum text-sm font-semibold text-text-primary">{v}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
      {needsCheck.length > 0 && (
        <section
          className="rounded-md border border-warning bg-warning-bg px-3 py-2"
          data-testid="voice-v2-needs-check"
        >
          <h4 className="text-xs font-bold tracking-wide text-warning-strong">【要確認】</h4>
          <ul className="mt-1 space-y-1">
            {needsCheck.map((item, i) => (
              <li
                key={i}
                className="flex flex-wrap items-baseline gap-x-1.5 text-sm text-text-primary"
              >
                <span>{item.text}</span>
                {item.sec !== null && <TimeMark sec={item.sec} onSeek={onSeek} />}
              </li>
            ))}
          </ul>
        </section>
      )}
      <VoiceMarkdown markdown={body} onSeek={onSeek} testId="voice-v2-body" />
    </div>
  );
}
