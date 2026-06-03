/**
 * WeeklyPatternEditor (W3-C)
 *
 * Structured editor for the `patients.weekly_pattern` JSONB column.
 * Replaces the Wave 1 textarea (free-form JSON) with a typed UI that
 * matches the `WeeklyPattern` interface in `lib/schemas/patient.ts`.
 *
 * Controlled component — `value` / `onChange` are required.
 */
'use client';

import * as React from 'react';

import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import {
  SERVICE_MINUTES_OPTIONS,
  TIME_TYPE_OPTIONS,
  VISIT_FREQUENCY_LABELS,
  VISIT_FREQUENCY_OPTIONS,
  WEEKDAY_KEYS,
  WEEKDAY_LABELS_JA,
  type WeekdayKey,
  type WeeklyPattern,
} from '@/lib/schemas/patient';

interface WeeklyPatternEditorProps {
  value: WeeklyPattern;
  onChange: (next: WeeklyPattern) => void;
  disabled?: boolean;
}

export function WeeklyPatternEditor({
  value,
  onChange,
  disabled = false,
}: WeeklyPatternEditorProps) {
  const update = React.useCallback(
    <K extends keyof WeeklyPattern>(key: K, next: WeeklyPattern[K]) => {
      onChange({ ...value, [key]: next });
    },
    [onChange, value],
  );

  const togglePreferred = (day: WeekdayKey) => {
    const has = value.preferred_weekdays.includes(day);
    const next = has
      ? value.preferred_weekdays.filter((d) => d !== day)
      : [...value.preferred_weekdays, day];
    // Re-order to canonical Mon..Sun for stable display.
    next.sort((a, b) => WEEKDAY_KEYS.indexOf(a) - WEEKDAY_KEYS.indexOf(b));
    update('preferred_weekdays', next);
  };

  const showTimeRange = value.time_type === '時間帯' || value.time_type === '固定';

  // サービス時間プルダウン候補 (5 分刻み 15〜180)。
  // 既存値が候補に無い (将来の非 5 分値など) 場合は防御的にその値を差し込み、
  // 編集時に値が勝手に変わらないようにする。現データは全て 5 の倍数なので通常は不要。
  const serviceMinutesOptions = React.useMemo(() => {
    const current = value.service_minutes;
    if (Number.isFinite(current) && !SERVICE_MINUTES_OPTIONS.includes(current)) {
      return [...SERVICE_MINUTES_OPTIONS, current].sort((a, b) => a - b);
    }
    return SERVICE_MINUTES_OPTIONS;
  }, [value.service_minutes]);

  return (
    <div className="space-y-4 rounded-md border border-border-default bg-bg-base p-4">
      <h3 className="text-sm font-semibold text-text-primary">希望訪問パターン</h3>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Field label="週あたり訪問回数" hint="1〜7">
          <Input
            type="number"
            min={1}
            max={7}
            disabled={disabled}
            value={value.frequency_per_week}
            onChange={(e) => {
              const n = Number(e.target.value);
              update('frequency_per_week', Number.isFinite(n) ? Math.min(7, Math.max(1, n)) : 1);
            }}
          />
        </Field>

        <Field label="訪問頻度">
          <Select
            disabled={disabled}
            value={value.visit_frequency ?? ''}
            onChange={(v) =>
              update(
                'visit_frequency',
                v === '' ? null : (v as (typeof VISIT_FREQUENCY_OPTIONS)[number]),
              )
            }
            options={[
              ['', '--'],
              ...VISIT_FREQUENCY_OPTIONS.map((k) => [k, VISIT_FREQUENCY_LABELS[k]] as const),
            ]}
          />
        </Field>

        <Field label="訪問週" hint='例: "1,3"'>
          <Input
            type="text"
            disabled={disabled}
            value={value.visit_weeks ?? ''}
            placeholder="1,3"
            onChange={(e) => {
              const v = e.target.value.trim();
              update('visit_weeks', v === '' ? null : v);
            }}
          />
        </Field>
      </div>

      <Field label="希望曜日">
        <div className="flex flex-wrap gap-3 pt-1">
          {WEEKDAY_KEYS.map((day) => {
            const id = `preferred-${day}`;
            const checked = value.preferred_weekdays.includes(day);
            return (
              <label
                key={day}
                htmlFor={id}
                className="inline-flex items-center gap-1.5 text-sm text-text-primary"
              >
                <Checkbox
                  id={id}
                  disabled={disabled}
                  checked={checked}
                  onCheckedChange={() => togglePreferred(day)}
                />
                {WEEKDAY_LABELS_JA[day]}
              </label>
            );
          })}
        </div>
      </Field>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Field label="サービス時間 (分)" hint="5分刻み・基本35分">
          <Select
            disabled={disabled}
            value={String(value.service_minutes)}
            onChange={(v) => {
              const n = Number(v);
              if (Number.isFinite(n)) update('service_minutes', Math.min(180, Math.max(1, n)));
            }}
            options={serviceMinutesOptions.map((m) => [String(m), `${m}分`] as const)}
          />
        </Field>

        <Field label="時間タイプ">
          <Select
            disabled={disabled}
            value={value.time_type}
            onChange={(v) => update('time_type', v as (typeof TIME_TYPE_OPTIONS)[number])}
            options={TIME_TYPE_OPTIONS.map((t) => [t, t] as const)}
          />
        </Field>

        {showTimeRange ? (
          <>
            <Field label="希望開始時刻">
              <Input
                type="time"
                disabled={disabled}
                value={value.preferred_start ?? ''}
                onChange={(e) => {
                  const v = e.target.value;
                  update('preferred_start', v === '' ? null : v);
                }}
              />
            </Field>
            <Field label="希望終了時刻">
              <Input
                type="time"
                disabled={disabled}
                value={value.preferred_end ?? ''}
                onChange={(e) => {
                  const v = e.target.value;
                  update('preferred_end', v === '' ? null : v);
                }}
              />
            </Field>
          </>
        ) : null}
      </div>
    </div>
  );
}

interface FieldProps {
  label: string;
  hint?: string;
  children: React.ReactNode;
}

function Field({ label, hint, children }: FieldProps) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium text-text-secondary">
        {label}
        {hint ? <span className="ml-2 text-xs font-normal text-text-muted">{hint}</span> : null}
      </span>
      {children}
    </label>
  );
}

interface SelectProps {
  value: string;
  onChange: (v: string) => void;
  options: ReadonlyArray<readonly [string, string]>;
  disabled?: boolean;
  className?: string;
}

function Select({ value, onChange, options, disabled, className }: SelectProps) {
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className={`flex h-10 w-full rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary focus-visible:outline-none focus-visible:border-brand-primary focus-visible:ring-2 focus-visible:ring-brand-primary-light disabled:cursor-not-allowed disabled:opacity-50 ${className ?? ''}`}
    >
      {options.map(([v, l]) => (
        <option key={v} value={v}>
          {l}
        </option>
      ))}
    </select>
  );
}
