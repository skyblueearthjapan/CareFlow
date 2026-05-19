'use client';

/**
 * Internal form fields shared by OverrideAddDialog + OverrideEditDialog.
 * Owns validation/state; parent owns submit / mutation wiring.
 */
import { useEffect, useMemo, useState } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import {
  OVERRIDE_TYPE_VALUES,
  overrideCreateSchema,
  overrideTypeUsesTime,
  type OverrideCreate,
  type OverrideType,
} from '@/lib/schemas/staff-overrides';

export interface OverrideFormState {
  date: string;
  type: OverrideType;
  start_time: string;
  end_time: string;
  note: string;
}

export const emptyOverrideForm = (): OverrideFormState => ({
  date: '',
  type: '休み',
  start_time: '',
  end_time: '',
  note: '',
});

interface OverrideFormProps {
  state: OverrideFormState;
  onChange: (next: OverrideFormState) => void;
  errors: Partial<Record<keyof OverrideFormState | '_form', string>>;
  /** Disable inputs while a mutation is in-flight. */
  disabled?: boolean;
}

export function OverrideForm({ state, onChange, errors, disabled }: OverrideFormProps) {
  const showTime = overrideTypeUsesTime(state.type);

  return (
    <div className="space-y-4">
      {errors._form && (
        <Alert variant="destructive">
          <AlertTitle>入力エラー</AlertTitle>
          <AlertDescription>{errors._form}</AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="override-date">日付</Label>
          <Input
            id="override-date"
            type="date"
            value={state.date}
            onChange={(e) => onChange({ ...state, date: e.target.value })}
            required
            disabled={disabled}
          />
          {errors.date && <p className="text-xs text-red-600">{errors.date}</p>}
        </div>

        <div className="space-y-1">
          <Label htmlFor="override-type">種別</Label>
          <Select
            value={state.type}
            onValueChange={(v) => onChange({ ...state, type: v as OverrideType })}
            disabled={disabled}
          >
            <SelectTrigger id="override-type" aria-label="種別を選択">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {OVERRIDE_TYPE_VALUES.map((t) => (
                <SelectItem key={t} value={t}>
                  {t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {errors.type && <p className="text-xs text-red-600">{errors.type}</p>}
        </div>
      </div>

      {showTime && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="override-start">開始時刻</Label>
            <Input
              id="override-start"
              type="time"
              value={state.start_time}
              onChange={(e) => onChange({ ...state, start_time: e.target.value })}
              disabled={disabled}
              aria-label="変更後の開始時刻"
            />
            {errors.start_time && <p className="text-xs text-red-600">{errors.start_time}</p>}
          </div>
          <div className="space-y-1">
            <Label htmlFor="override-end">終了時刻</Label>
            <Input
              id="override-end"
              type="time"
              value={state.end_time}
              onChange={(e) => onChange({ ...state, end_time: e.target.value })}
              disabled={disabled}
              aria-label="変更後の終了時刻"
            />
            {errors.end_time && <p className="text-xs text-red-600">{errors.end_time}</p>}
          </div>
        </div>
      )}

      <div className="space-y-1">
        <Label htmlFor="override-note">備考</Label>
        <Textarea
          id="override-note"
          value={state.note}
          onChange={(e) => onChange({ ...state, note: e.target.value })}
          rows={3}
          maxLength={500}
          placeholder="任意"
          disabled={disabled}
        />
        {errors.note && <p className="text-xs text-red-600">{errors.note}</p>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers — convert form state ↔ schema payload + run validation
// ---------------------------------------------------------------------------

export function formStateToCreate(state: OverrideFormState): OverrideCreate {
  const usesTime = overrideTypeUsesTime(state.type);
  return {
    date: state.date,
    type: state.type,
    start_time: usesTime && state.start_time ? state.start_time : null,
    end_time: usesTime && state.end_time ? state.end_time : null,
    note: state.note ? state.note : null,
  };
}

export function validateOverrideForm(state: OverrideFormState): {
  ok: boolean;
  data?: OverrideCreate;
  errors: Partial<Record<keyof OverrideFormState | '_form', string>>;
} {
  const payload = formStateToCreate(state);
  const result = overrideCreateSchema.safeParse(payload);
  if (result.success) {
    return { ok: true, data: result.data, errors: {} };
  }
  const errs: Partial<Record<keyof OverrideFormState | '_form', string>> = {};
  for (const issue of result.error.issues) {
    const key = issue.path[0];
    if (typeof key === 'string' && key in (state as unknown as Record<string, unknown>)) {
      errs[key as keyof OverrideFormState] = issue.message;
    } else {
      errs._form = issue.message;
    }
  }
  return { ok: false, errors: errs };
}

/** Hook helper: keep the local form state in sync when the parent passes a
 * fresh `initial` (e.g. opening the edit dialog for a different row). */
export function useOverrideFormState(initial: OverrideFormState, resetSignal: unknown) {
  const [state, setState] = useState<OverrideFormState>(initial);
  const [errors, setErrors] = useState<Partial<Record<keyof OverrideFormState | '_form', string>>>(
    {},
  );

  // Re-seed when the dialog re-opens or the target row changes.
  useEffect(() => {
    setState(initial);
    setErrors({});
    // We intentionally depend on the resetSignal (open + id) rather than
    // `initial` directly to avoid resetting on every parent re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetSignal]);

  return useMemo(() => ({ state, setState, errors, setErrors }), [state, errors]);
}
