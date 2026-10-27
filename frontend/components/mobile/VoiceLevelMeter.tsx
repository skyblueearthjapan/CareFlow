'use client';

/**
 * 録音中の音量の目安（v2 設計 §3 P5）。
 *
 * 段階は録音機（`lib/voice/level-meter.ts`）が直近 1.6 秒の最大値で決める。ここは
 * 3 本の目盛と 1 行の文言を出すだけ。段階が無い（メーターを作れない端末・一時停止中）
 * ときは何も出さない。
 */
import { cn } from '@/lib/utils';
import { MIC_LEVEL_MESSAGES, type MicLevel } from '@/lib/voice/level-meter';

const BARS_ON: Record<MicLevel, number> = { ok: 3, quiet: 1, silent: 0 };

const TONE: Record<MicLevel, { bar: string; text: string }> = {
  ok: { bar: 'bg-success', text: 'text-success' },
  quiet: { bar: 'bg-warning', text: 'text-warning' },
  silent: { bar: 'bg-error', text: 'text-error' },
};

export function VoiceLevelMeter({ level }: { level: MicLevel | null | undefined }) {
  if (!level) return null;
  const tone = TONE[level];
  return (
    <div
      className="flex items-center justify-center gap-2"
      role="status"
      aria-live="polite"
      data-testid="voice-level-meter"
      data-level={level}
    >
      <span className="flex items-end gap-0.5" aria-hidden>
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className={cn(
              'w-1 rounded-sm',
              i === 0 ? 'h-1.5' : i === 1 ? 'h-2.5' : 'h-3.5',
              i < BARS_ON[level] ? tone.bar : 'bg-bg-muted',
            )}
          />
        ))}
      </span>
      <span className={cn('text-xs', tone.text)}>{MIC_LEVEL_MESSAGES[level]}</span>
    </div>
  );
}
