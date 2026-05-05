/**
 * WeeklyPatternEditor (W1-FE1)
 *
 * Structured editor for the v2 `patients.weekly_pattern` JSONB column.
 *
 * v2 削減:
 *   - 曜日優先度 (weekday_priority)
 *   - NG曜日 (ng_weekdays)
 *
 * v2 追加 (§3.3):
 *   - 曜日ごとの **staff_count トグル (1 名 / 2 名)**
 *   - 曜日エントリは `entries[]` に flatten され、有効化された曜日のみ送信
 *
 * Controlled component — `value` / `onChange` are required.
 */
'use client';

import * as React from 'react';

import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import {
  TIME_TYPE_OPTIONS,
  VISIT_FREQUENCY_LABELS,
  VISIT_FREQUENCY_OPTIONS,
  WEEKDAY_KEYS,
  WEEKDAY_LABELS_JA,
  type WeekdayKey,
  type WeeklyPattern,
  type WeeklyPatternEntryUI,
} from '@/lib/schemas/patient';

const SERVICE_MINUTES_PRESETS = [15, 30, 45, 60] as const;

interface WeeklyPatternEditorProps {
  value: WeeklyPattern;
  onChange: (next: WeeklyPattern) => void;
  disabled?: boolean;
  /** Card 見出し (default: 週間訪問パターン). 特別週でも同じ Editor を使うため上書きできる。 */
  title?: string;
}

export function WeeklyPatternEditor({
  value,
  onChange,
  disabled = false,
  title = '週間訪問パターン',
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
    next.sort((a, b) => WEEKDAY_KEYS.indexOf(a) - WEEKDAY_KEYS.indexOf(b));
    update('preferred_weekdays', next);
  };

  const updateEntry = (weekday: WeekdayKey, patch: Partial<WeeklyPatternEntryUI>) => {
    const nextEntries = value.entries.map((e) => (e.weekday === weekday ? { ...e, ...patch } : e));
    onChange({ ...value, entries: nextEntries });
  };

  const showTimeRange = value.time_type === '時間帯' || value.time_type === '固定';

  return (
    <div className="space-y-4 rounded-md border border-border-default bg-bg-base p-4">
      <h3 className="text-sm font-semibold text-text-primary">{title}</h3>

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

        <Field label="サービス時間 (分)" hint="1〜180">
          <div className="flex items-center gap-2">
            <Input
              type="number"
              min={1}
              max={180}
              disabled={disabled}
              value={value.service_minutes}
              onChange={(e) => {
                const n = Number(e.target.value);
                update('service_minutes', Number.isFinite(n) ? Math.min(180, Math.max(1, n)) : 30);
              }}
              className="flex-1"
            />
            <Select
              disabled={disabled}
              value={String(value.service_minutes)}
              onChange={(v) => {
                const n = Number(v);
                if (Number.isFinite(n)) update('service_minutes', n);
              }}
              options={[
                ['', '--'],
                ...SERVICE_MINUTES_PRESETS.map((m) => [String(m), `${m}分`] as const),
              ]}
              className="w-24"
            />
          </div>
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

      {/* 曜日ごとの staff_count トグル (§3.3, v2 追加) */}
      <div className="space-y-2">
        <p className="text-sm font-medium text-text-secondary">曜日別 訪問人数 (1 名 / 2 名)</p>
        <p className="text-xs text-text-muted">
          有効化した曜日のみ保存されます。「2 名」を選ぶとその曜日は 2 名体制で訪問します。
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-text-secondary">
              <tr>
                <th className="px-2 py-1 font-medium">曜日</th>
                <th className="px-2 py-1 font-medium">有効</th>
                <th className="px-2 py-1 font-medium">人数</th>
              </tr>
            </thead>
            <tbody>
              {value.entries.map((entry) => {
                const idEnabled = `entry-enabled-${entry.weekday}`;
                return (
                  <tr key={entry.weekday} className="border-t border-border-default/50">
                    <td className="px-2 py-1 font-medium text-text-primary">
                      {WEEKDAY_LABELS_JA[entry.weekday]}
                    </td>
                    <td className="px-2 py-1">
                      <Checkbox
                        id={idEnabled}
                        disabled={disabled}
                        checked={entry.enabled}
                        onCheckedChange={(c) => updateEntry(entry.weekday, { enabled: c === true })}
                      />
                    </td>
                    <td className="px-2 py-1">
                      <Select
                        disabled={disabled || !entry.enabled}
                        value={String(entry.staff_count)}
                        onChange={(v) =>
                          updateEntry(entry.weekday, {
                            staff_count: v === '2' ? 2 : 1,
                          })
                        }
                        options={[
                          ['1', '1 名'],
                          ['2', '2 名'],
                        ]}
                        className="w-24"
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
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
