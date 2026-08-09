'use client';

/**
 * 受け入れ枠マトリックス (PC版) — P2 / P4 / P5。
 *
 * 拠点 × 曜日 × 時間帯の ○△× を 1 拠点 = 1 表で縦に積んで表示する。値は
 * effective_status (= 週別上書き ?? 常設上書き ?? 自動算出)、空きコース数を併記。
 * 由来印 (週=週別 / 常=常設)。デザイン (claude.ai/design `careflow-avail.jsx` の
 * AvailDesktop) を Tailwind + デザイントークンに移植したもの。色だけでなく ○△× の
 * 記号で判別できる (白黒印刷対応)。
 *
 * P4/P5: ``edit`` を渡すと各セルがクリックで上書き編集できる (admin/manager のみ)。
 *   - 「この週だけ」= 週別上書き (その週限り)
 *   - 「毎週（継続）」= 常設上書き (解除するまで毎週)。全拠点に一括適用も可。
 *   - コメント (notes) も保存できる。
 */
import { useState } from 'react';

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { normalizeTimeSlot, type AcceptanceStatus } from '@/lib/schemas/v2/acceptance';
import type {
  AcceptanceMatrixResponse,
  MatrixCell,
  OfficeMatrix,
} from '@/lib/schemas/v2/acceptance_matrix';
import { cn } from '@/lib/utils';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

/** セル編集 API。officeId=null は全拠点一括 (常設のみ)。timeSlot は "HH:MM:SS"。 */
export interface MatrixEditApi {
  busy: boolean;
  setWeek: (a: {
    officeId: string;
    weekday: number;
    timeSlot: string;
    status: AcceptanceStatus;
    notes: string | null;
  }) => void;
  clearWeek: (a: { officeId: string; weekday: number; timeSlot: string }) => void;
  setStanding: (a: {
    officeId: string | null;
    weekday: number;
    timeSlot: string;
    status: AcceptanceStatus;
    notes: string | null;
  }) => void;
  clearStanding: (a: { officeId: string | null; weekday: number; timeSlot: string }) => void;
}

interface StatusMeta {
  glyph: string;
  cellClass: string;
  long: string;
}

// モバイル現場ボード側 (AcceptanceFieldView の STATUS_META) と同じ配色に統一
// (PO要望 2026-07-10: ○=薄緑 / △=オレンジ / ×=簡素)。値を変えるときは両方同期すること。
const STATUS_META: Record<AcceptanceStatus, StatusMeta> = {
  available: {
    glyph: '○',
    cellClass: 'bg-[#E7F4F0] text-[#0E8472] font-bold',
    long: '受け入れ可能',
  },
  consult: {
    glyph: '△',
    cellClass: 'bg-[#FBF1DD] text-[#B5790A] font-bold',
    long: '相談ください',
  },
  unavailable: { glyph: '×', cellClass: 'text-text-muted', long: '枠なし' },
};

function weekdayClass(weekday: number): string {
  if (weekday === 5) return 'text-blue-600'; // 土
  if (weekday === 6) return 'text-rose-500'; // 日
  return 'text-text-primary';
}

export function AcceptanceMatrixLegend({ className }: { className?: string }) {
  return (
    <div className={cn('flex flex-wrap items-center gap-4 text-xs text-text-secondary', className)}>
      {(['available', 'consult', 'unavailable'] as const).map((s) => {
        const m = STATUS_META[s];
        return (
          <span key={s} className="inline-flex items-center gap-1.5">
            <span
              className={cn(
                'grid h-5 w-5 place-items-center rounded border border-border-default text-xs',
                m.cellClass,
              )}
            >
              {m.glyph}
            </span>
            {m.long}
          </span>
        );
      })}
      <span className="text-text-muted">数字 = その時間帯に入れる空きコース数</span>
    </div>
  );
}

/** ○△× の選択 + 解除ボタン行 (ポップオーバー内)。 */
function StatusPicker({
  current,
  onPick,
  onClear,
  busy,
}: {
  current: AcceptanceStatus | null | undefined;
  onPick: (s: AcceptanceStatus) => void;
  onClear: () => void;
  busy?: boolean;
}) {
  return (
    <div className="flex items-center gap-1">
      {(['available', 'consult', 'unavailable'] as const).map((s) => {
        const mm = STATUS_META[s];
        return (
          <button
            key={s}
            type="button"
            disabled={busy}
            onClick={() => onPick(s)}
            className={cn(
              'grid h-7 w-7 place-items-center rounded text-sm hover:opacity-80 disabled:opacity-50',
              mm.cellClass,
              current === s && 'ring-2 ring-inset ring-brand-primary/60',
            )}
          >
            {mm.glyph}
          </button>
        );
      })}
      <button
        type="button"
        disabled={busy}
        onClick={onClear}
        className="ml-1 rounded px-2 py-1 text-[11px] text-text-secondary hover:bg-bg-muted disabled:opacity-50"
      >
        解除
      </button>
    </div>
  );
}

function MatrixCellView({
  cell,
  closed,
  hhmm,
  officeId,
  weekday,
  edit,
}: {
  cell: MatrixCell | undefined;
  closed: boolean;
  hhmm: string;
  officeId: string;
  weekday: number;
  edit?: MatrixEditApi;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [allOffices, setAllOffices] = useState(false);

  if (closed) {
    return (
      <div className="grid h-9 place-items-center border-l border-t border-border-default/60 bg-bg-muted text-[10px] text-text-muted">
        休
      </div>
    );
  }

  const status = cell?.effective_status ?? 'unavailable';
  const meta = STATUS_META[status];
  const src = cell?.source ?? 'auto';
  const mark = src === 'week_override' ? '週' : src === 'manual_standing' ? '常' : null;
  const count =
    src === 'auto' && cell
      ? status === 'available'
        ? cell.metrics.available_course_count
        : status === 'consult'
          ? cell.metrics.consult_course_count
          : 0
      : 0;
  const title = cell
    ? `${cell.metrics.reasons.join(' / ')}｜空き ${cell.metrics.available_course_count}コース・残 ${cell.metrics.remaining_patients_total}名・${cell.metrics.remaining_minutes_total}分${cell.note ? `｜${cell.note}` : ''}`
    : undefined;

  const body = (
    <div
      className={cn(
        'relative grid h-9 place-items-center border-l border-t border-border-default/60 text-base',
        meta.cellClass,
        src === 'week_override' && 'ring-1 ring-inset ring-brand-primary/50',
      )}
    >
      <span className="inline-flex items-baseline gap-px leading-none">
        <span>{meta.glyph}</span>
        {count > 0 && <span className="text-[10px] font-bold">{count}</span>}
      </span>
      {mark && (
        <span className="absolute right-0.5 top-0 text-[7px] leading-none text-text-muted">
          {mark}
        </span>
      )}
    </div>
  );

  if (!edit) {
    return <div title={title}>{body}</div>;
  }

  const ts = `${hhmm}:00`;
  const notes = () => (note.trim() ? note.trim() : null);
  const close = () => setOpen(false);
  const onOpenChange = (o: boolean) => {
    if (o) {
      setNote(cell?.note ?? '');
      setAllOffices(false);
    }
    setOpen(o);
  };

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          title={title}
          aria-label={`${hhmm} の受け入れ枠を編集`}
          className="block w-full cursor-pointer"
        >
          {body}
        </button>
      </PopoverTrigger>
      <PopoverContent align="center" className="w-64 p-3">
        <div className="mb-2 text-[11px] font-medium text-text-secondary">
          {WEEKDAY_LABELS[weekday]}曜 {hhmm} の受け入れ枠を上書き
        </div>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="コメント（任意。例: 全体会議）"
          className="mb-3 w-full rounded border border-border-default bg-bg-base px-2 py-1 text-xs"
        />

        {/* この週だけ */}
        <div className="mb-3">
          <div className="mb-1 text-[11px] font-bold text-text-primary">
            この週だけ
            {cell?.week_status && (
              <span className="ml-1 text-[10px] font-normal text-text-muted">
                (現在 {STATUS_META[cell.week_status].glyph})
              </span>
            )}
          </div>
          <StatusPicker
            current={cell?.week_status}
            busy={edit.busy}
            onPick={(s) => {
              edit.setWeek({ officeId, weekday, timeSlot: ts, status: s, notes: notes() });
              close();
            }}
            onClear={() => {
              edit.clearWeek({ officeId, weekday, timeSlot: ts });
              close();
            }}
          />
        </div>

        {/* 毎週（継続） */}
        <div className="border-t border-border-default pt-2">
          <div className="mb-1 text-[11px] font-bold text-text-primary">
            毎週（継続）
            {cell?.manual_status && (
              <span className="ml-1 text-[10px] font-normal text-text-muted">
                (現在 {STATUS_META[cell.manual_status].glyph})
              </span>
            )}
          </div>
          <StatusPicker
            current={cell?.manual_status}
            busy={edit.busy}
            onPick={(s) => {
              edit.setStanding({
                officeId: allOffices ? null : officeId,
                weekday,
                timeSlot: ts,
                status: s,
                notes: notes(),
              });
              close();
            }}
            onClear={() => {
              edit.clearStanding({ officeId: allOffices ? null : officeId, weekday, timeSlot: ts });
              close();
            }}
          />
          <label className="mt-2 flex items-center gap-1.5 text-[11px] text-text-secondary">
            <input
              type="checkbox"
              checked={allOffices}
              onChange={(e) => setAllOffices(e.target.checked)}
            />
            全拠点に一括適用
          </label>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function OfficeMatrixTable({
  office,
  slots,
  edit,
}: {
  office: OfficeMatrix;
  slots: string[];
  edit?: MatrixEditApi;
}) {
  const dayByWeekday = new Map(office.days.map((d) => [d.weekday, d]));
  const cellLookup = new Map<number, Map<string, MatrixCell>>();
  for (const d of office.days) {
    const m = new Map<string, MatrixCell>();
    for (const c of d.cells) m.set(normalizeTimeSlot(c.time_slot), c);
    cellLookup.set(d.weekday, m);
  }
  const operatingSet = new Set(office.operating_weekdays);

  return (
    <div className="space-y-2">
      <div className="flex items-baseline gap-2">
        <span className="h-5 w-2 rounded bg-brand-primary" aria-hidden="true" />
        <h3 className="font-serif text-lg font-bold text-text-primary">{office.office_name}</h3>
        {office.city_names.length > 0 && (
          <span className="text-xs text-text-secondary">{office.city_names.join('・')}</span>
        )}
        {!office.week_generated && (
          <span className="rounded bg-bg-muted px-1.5 py-0.5 text-[10px] text-text-muted">
            週未生成
          </span>
        )}
      </div>

      {/* 設定漏れの明示 (PO 要望 2026-08-10): なぜ○×が出ないのかを具体的に案内する。 */}
      {office.setup_state && (
        <div
          className="rounded-md border border-amber-500/60 bg-amber-50 px-3 py-2 text-xs text-amber-900"
          data-testid={`acceptance-setup-${office.office_id}`}
        >
          {office.setup_state === 'assignment_pending' ? (
            <>
              ⚠ ○×を計算できません。スケジュール画面で
              <span className="mx-1 font-bold">「自動スタッフ割当」</span>
              を実行してください（この工程がコースの確定を兼ねており、実行するまで
              受け入れ枠の対象になりません）。
            </>
          ) : (
            <>
              ⚠ ○×を計算できません。この週のスケジュールが未生成です。スケジュール画面で
              <span className="mx-1 font-bold">「週を生成」</span>
              を実行してください。
            </>
          )}
        </div>
      )}

      <div className="overflow-x-auto">
        <div
          className="min-w-[420px] overflow-hidden rounded-lg border border-border-default bg-bg-base"
          style={{ display: 'grid', gridTemplateColumns: '56px repeat(7, minmax(38px, 1fr))' }}
        >
          {/* ヘッダ行 */}
          <div className="border-b border-border-default bg-bg-muted" />
          {WEEKDAY_LABELS.map((label, w) => (
            <div
              key={w}
              className={cn(
                'grid h-9 place-items-center border-b border-l border-border-default bg-bg-muted font-serif text-sm font-bold',
                weekdayClass(w),
              )}
            >
              {label}
            </div>
          ))}

          {/* 本体行 (時間帯ごと) */}
          {slots.map((slot) => {
            const hhmm = normalizeTimeSlot(slot);
            return (
              <div key={slot} style={{ display: 'contents' }}>
                <div className="tnum grid h-9 place-items-center bg-bg-muted text-[11px] text-text-secondary">
                  {hhmm}
                </div>
                {WEEKDAY_LABELS.map((_, w) => {
                  const day = dayByWeekday.get(w);
                  const cell = cellLookup.get(w)?.get(hhmm);
                  const closed = day?.office_closed ?? !operatingSet.has(w);
                  return (
                    <MatrixCellView
                      key={w}
                      cell={cell}
                      closed={closed}
                      hhmm={hhmm}
                      officeId={office.office_id}
                      weekday={w}
                      edit={closed ? undefined : edit}
                    />
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export function AcceptanceMatrixBoard({
  data,
  edit,
}: {
  data: AcceptanceMatrixResponse;
  edit?: MatrixEditApi;
}) {
  if (data.offices.length === 0) {
    return <p className="text-sm text-text-secondary">表示できる拠点がありません。</p>;
  }
  return (
    <div className="space-y-8">
      {data.offices.map((office) => (
        <OfficeMatrixTable key={office.office_id} office={office} slots={data.slots} edit={edit} />
      ))}
    </div>
  );
}
