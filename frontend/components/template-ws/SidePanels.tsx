'use client';

/**
 * 作業場の小さな部品: 最新の編集のカード・↶↷・編集の履歴・枠の詳細・枠の無い方のトレイ・下書きの切り替え。
 * 盤面に幅を回すため、下書きは見出しの行の切り替え（選ぶ＋「＋新しい下書き」）にし、
 * トレイと履歴は盤面の下に、たためる欄として置く。
 */
import { useState } from 'react';
import { Bot, ChevronDown, ChevronRight, FileText, Redo2, Undo2, User } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { fmtMin, parseHm, stepTitle } from '@/lib/templateWs/helpers';
import type { DraftSummary, DraftView, SlotCard, Step } from '@/lib/templateWs/types';

export function ActorIcon({ actor }: { actor: Step['actor'] }) {
  return actor === 'agent' ? (
    <Bot className="h-4 w-4 shrink-0 text-brand-primary" aria-label="らく助エージェント" />
  ) : (
    <User className="h-4 w-4 shrink-0 text-text-secondary" aria-label="あなた" />
  );
}

/** 最新の編集を小さな帯で（特にエージェントの編集が見える）。最適化なら［くわしく］で報告を開く。 */
export function LatestStepCard({
  step,
  onDismiss,
  onOpenReport,
}: {
  step: Step;
  onDismiss: () => void;
  onOpenReport?: (seq: number) => void;
}) {
  return (
    <div
      className="flex items-start gap-2 rounded-lg border border-info bg-info-bg px-3 py-2 text-sm"
      role="status"
      data-testid="latest-step"
      data-actor={step.actor}
    >
      <ActorIcon actor={step.actor} />
      <div className="min-w-0 flex-1">
        <div className="font-semibold text-info-strong">
          {stepTitle(step)}
          <span className="ml-2 font-normal text-text-secondary">
            変わった枠 {step.changed_slots} 件{step.params_changed ? '・もしもの条件も変わりました' : ''}
          </span>
        </div>
        {step.reason ? <div className="text-xs text-text-secondary">{step.reason}</div> : null}
      </div>
      {step.kind === 'optimise' && onOpenReport ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8"
          onClick={() => onOpenReport(step.seq)}
          data-testid="latest-report"
        >
          <FileText className="mr-1 h-4 w-4" aria-hidden />
          くわしく
        </Button>
      ) : null}
      <button type="button" className="self-center text-xs text-text-muted underline" onClick={onDismiss}>
        閉じる
      </button>
    </div>
  );
}

/** ↶ ↷（見出しの道具の行に置く）。 */
export function UndoRedo({
  view,
  disabled,
  onUndo,
  onRedo,
}: {
  view: DraftView;
  disabled: boolean;
  onUndo: () => void;
  onRedo: () => void;
}) {
  return (
    <span className="inline-flex gap-1">
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="px-2"
        disabled={disabled || !view.can_undo}
        onClick={onUndo}
        aria-label="一つ前に戻す"
        title="一つ前に戻す"
        data-testid="undo"
      >
        <Undo2 className="h-4 w-4" />
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="px-2"
        disabled={disabled || !view.can_redo}
        onClick={onRedo}
        aria-label="やり直す"
        title="やり直す"
        data-testid="redo"
      >
        <Redo2 className="h-4 w-4" />
      </Button>
    </span>
  );
}

/** 盤面の下の、たためる欄。 */
export function Fold({
  title,
  count,
  defaultOpen = false,
  testId,
  children,
}: {
  title: string;
  count?: string;
  defaultOpen?: boolean;
  testId: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="rounded-lg border border-border-default bg-bg-base" data-testid={testId}>
      <button
        type="button"
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-sm font-semibold"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        data-testid={`${testId}-toggle`}
      >
        {open ? <ChevronDown className="h-4 w-4" aria-hidden /> : <ChevronRight className="h-4 w-4" aria-hidden />}
        {title}
        {count ? <span className="ml-1 font-normal text-text-muted">{count}</span> : null}
      </button>
      {open ? <div className="border-t border-border-default px-3 py-2">{children}</div> : null}
    </section>
  );
}

export function HistoryList({
  view,
  onOpenReport,
}: {
  view: DraftView;
  onOpenReport?: (seq: number) => void;
}) {
  return (
    <div data-testid="history">
      {view.steps.length === 0 ? (
        <p className="text-xs text-text-muted">まだ編集はありません。</p>
      ) : (
        <ol className="space-y-1">
          {view.steps.map((s) => (
            <li
              key={s.seq}
              className={`flex items-start gap-1.5 text-xs ${s.undone ? 'text-text-muted line-through' : ''}`}
              data-testid={`step-${s.seq}`}
            >
              <ActorIcon actor={s.actor} />
              <span className="min-w-0 flex-1">
                {stepTitle(s)}
                {s.reason ? `（${s.reason}）` : ''}
                <span className="ml-1 text-text-muted">{s.changed_slots} 件</span>
              </span>
              {s.kind === 'optimise' && onOpenReport ? (
                <button
                  type="button"
                  className="shrink-0 text-brand-primary underline hover:no-underline"
                  onClick={() => onOpenReport(s.seq)}
                  data-testid={`step-report-${s.seq}`}
                >
                  くわしく
                </button>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/** ブロックを押したときの詳細: コースと時刻を選んで動かす（決まりに合うかはバックエンドが確かめる）。 */
export function SlotDetail({
  slot,
  view,
  readOnly,
  busy,
  onMove,
  onClose,
}: {
  slot: SlotCard;
  view: DraftView;
  readOnly: boolean;
  busy: boolean;
  onMove: (courseId: string, start: number) => void;
  onClose: () => void;
}) {
  const day = view.days.find((d) => d.weekday === slot.weekday);
  // 全拠点の下書きでは、その方の拠点のコースだけ（別の拠点のコースへは動かせない）
  const courses = (day?.courses ?? []).filter((c) => !slot.office_id || !c.office_id || c.office_id === slot.office_id);
  const [course, setCourse] = useState(slot.course_id ?? '');
  const [time, setTime] = useState(fmtMin(slot.start).padStart(5, '0'));
  const start = parseHm(time);
  return (
    <section
      className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-border-default bg-bg-base px-3 py-2 text-sm"
      data-testid="slot-detail"
    >
      <div className="font-semibold">
        {slot.patient}（{day?.name}曜）
      </div>
      <div className="min-w-0 flex-1 text-xs text-text-secondary">
        {slot.locked ? '赤ピンの枠です。曜日と時刻は変えません（コースだけ替えられます）。' : `${slot.why}。`}
        開始してよい範囲 {fmtMin(slot.win[0])}〜{fmtMin(slot.win[1])}
      </div>
      <label className="flex items-center gap-2">
        コース
        <select
          value={course}
          onChange={(e) => setCourse(e.target.value)}
          className="h-9 rounded border border-border-default bg-bg-base px-2"
          aria-label="コース"
          disabled={readOnly}
        >
          {courses.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}（{c.staff}）
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2">
        開始
        <input
          type="time"
          step={300}
          value={time}
          onChange={(e) => setTime(e.target.value)}
          disabled={readOnly || slot.locked}
          className="h-9 rounded border border-border-default bg-bg-base px-2"
          aria-label="開始の時刻"
        />
      </label>
      <Button
        type="button"
        size="sm"
        disabled={readOnly || busy || !course || start == null}
        onClick={() => start != null && onMove(course, start)}
        data-testid="slot-move"
      >
        この枠を動かす
      </Button>
      <button type="button" className="text-xs text-text-muted underline" onClick={onClose}>
        閉じる
      </button>
    </section>
  );
}

export function Tray({ view }: { view: DraftView }) {
  if (view.tray.length === 0) return <p className="text-xs text-text-muted">いまは、ありません。</p>;
  const label = { waiting: '最適化で置きます', unplaced: '置けませんでした', blocked: '置けません' } as const;
  return (
    <ul className="grid gap-1 sm:grid-cols-2" data-testid="tray">
      {view.tray.map((t) => (
        <li key={t.patient_id} className="rounded border border-border-default bg-bg-base px-2 py-1 text-xs">
          <span className="font-medium">{t.name}</span>
          <span className="ml-1 text-text-muted">
            {view.all_offices && t.office ? `${t.office}・` : ''}
            {t.days.length ? `${t.days.join('')}曜` : '曜日未定'}・{label[t.state]}
          </span>
          {t.reason ? <div className="text-text-muted">{t.reason}</div> : null}
        </li>
      ))}
    </ul>
  );
}

/** 下書きの切り替え（見出しの行）: 選ぶ・＋新しい下書き・名前を変える・しまう。 */
export function DraftSwitcher({
  drafts,
  selectedId,
  onSelect,
  onCreate,
  onRename,
  onArchive,
  busy,
}: {
  drafts: DraftSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: (name: string) => void;
  onRename: (id: string, name: string) => void;
  onArchive: (id: string) => void;
  busy: boolean;
}) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const cur = drafts.find((d) => d.id === selectedId) ?? null;
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="draft-list">
      {drafts.length > 0 ? (
        <select
          value={selectedId ?? ''}
          onChange={(e) => onSelect(e.target.value)}
          className="h-9 max-w-[16rem] rounded border border-border-default bg-bg-base px-2 text-sm"
          aria-label="下書き"
          data-testid="draft-select"
        >
          {drafts.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
              {d.status === 'committed' ? '（登録済み）' : ''}
            </option>
          ))}
        </select>
      ) : (
        <span className="text-sm text-text-muted">まだ下書きはありません。</span>
      )}
      {cur && cur.status === 'draft' ? (
        <>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              const n = window.prompt('新しい名前', cur.name);
              if (n && n.trim()) onRename(cur.id, n.trim());
            }}
          >
            名前を変える
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => onArchive(cur.id)}>
            しまう
          </Button>
        </>
      ) : null}
      {adding ? (
        <form
          className="flex items-center gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) {
              onCreate(name.trim());
              setName('');
              setAdding(false);
            }
          }}
        >
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="新しい下書きの名前"
            aria-label="新しい下書きの名前"
            autoFocus
            className="h-9 w-44 rounded border border-border-default bg-bg-base px-2 text-sm"
          />
          <Button type="submit" size="sm" disabled={busy || !name.trim()} data-testid="draft-create">
            作る
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setAdding(false)}>
            やめる
          </Button>
        </form>
      ) : (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setAdding(true)}
          title="いまの型を写して、仮の 1 週間を作ります"
          data-testid="draft-new"
        >
          ＋新しい下書き
        </Button>
      )}
    </div>
  );
}
