'use client';

/** 作業場の小さな部品: 最新の編集のカード・編集の履歴・枠の詳細・枠の無い方のトレイ・下書きの一覧。 */
import { useState } from 'react';
import { Bot, Redo2, Undo2, User } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { fmtMin, parseHm, stepTitle } from '@/lib/templateWs/helpers';
import type { DraftSummary, DraftView, SlotCard, Step } from '@/lib/templateWs/types';

export function ActorIcon({ actor }: { actor: Step['actor'] }) {
  return actor === 'agent' ? (
    <Bot className="h-3.5 w-3.5 text-brand-primary" aria-label="らく助エージェント" />
  ) : (
    <User className="h-3.5 w-3.5 text-text-secondary" aria-label="あなた" />
  );
}

/** 最新の編集を小さなカードで（特にエージェントの編集が見える）。 */
export function LatestStepCard({ step, onDismiss }: { step: Step; onDismiss: () => void }) {
  return (
    <div
      className="flex items-start gap-2 rounded border border-info bg-info-bg px-3 py-2 text-xs"
      role="status"
      data-testid="latest-step"
      data-actor={step.actor}
    >
      <ActorIcon actor={step.actor} />
      <div className="min-w-0 flex-1">
        <div className="font-semibold text-info-strong">{stepTitle(step)}</div>
        {step.reason ? <div className="text-text-secondary">{step.reason}</div> : null}
        <div className="text-text-muted">
          変わった枠 {step.changed_slots} 件{step.params_changed ? '・もしもの条件も変わりました' : ''}
        </div>
      </div>
      <button type="button" className="text-[11px] text-text-muted underline" onClick={onDismiss}>
        閉じる
      </button>
    </div>
  );
}

export function HistoryList({
  view,
  readOnly,
  busy,
  onUndo,
  onRedo,
}: {
  view: DraftView;
  readOnly: boolean;
  busy: boolean;
  onUndo: () => void;
  onRedo: () => void;
}) {
  return (
    <section className="space-y-2" data-testid="history">
      <div className="flex items-center gap-1">
        <h3 className="text-xs font-semibold">編集の履歴</h3>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="ml-auto h-7 px-2"
          disabled={readOnly || busy || !view.can_undo}
          onClick={onUndo}
          aria-label="一つ前に戻す"
          data-testid="undo"
        >
          <Undo2 className="h-3.5 w-3.5" />
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 px-2"
          disabled={readOnly || busy || !view.can_redo}
          onClick={onRedo}
          aria-label="やり直す"
          data-testid="redo"
        >
          <Redo2 className="h-3.5 w-3.5" />
        </Button>
      </div>
      {view.steps.length === 0 ? (
        <p className="text-[11px] text-text-muted">まだ編集はありません。</p>
      ) : (
        <ol className="space-y-1">
          {view.steps.map((s) => (
            <li
              key={s.seq}
              className={`flex items-start gap-1.5 text-[11px] ${s.undone ? 'text-text-muted line-through' : ''}`}
              data-testid={`step-${s.seq}`}
            >
              <ActorIcon actor={s.actor} />
              <span>
                {stepTitle(s)}
                {s.reason ? `（${s.reason}）` : ''}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
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
  const [course, setCourse] = useState(slot.course_id ?? '');
  const [time, setTime] = useState(fmtMin(slot.start).padStart(5, '0'));
  const start = parseHm(time);
  return (
    <section
      className="space-y-2 rounded border border-border-default bg-bg-base p-3 text-xs"
      data-testid="slot-detail"
    >
      <div className="flex items-center">
        <div className="font-semibold">
          {slot.patient}（{day?.name}曜）
        </div>
        <button type="button" className="ml-auto text-[11px] text-text-muted underline" onClick={onClose}>
          閉じる
        </button>
      </div>
      <div className="text-text-secondary">
        {slot.locked ? '赤ピンの枠です。曜日と時刻は変えません（コースだけ替えられます）。' : `${slot.why}。`}
        開始してよい範囲 {fmtMin(slot.win[0])}〜{fmtMin(slot.win[1])}
      </div>
      <label className="flex items-center gap-2">
        コース
        <select
          value={course}
          onChange={(e) => setCourse(e.target.value)}
          className="rounded border border-border-default bg-bg-base px-1.5 py-1"
          aria-label="コース"
          disabled={readOnly}
        >
          {(day?.courses ?? []).map((c) => (
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
          className="rounded border border-border-default bg-bg-base px-1.5 py-1"
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
    </section>
  );
}

export function Tray({ view }: { view: DraftView }) {
  if (view.tray.length === 0) return null;
  const label = { waiting: '最適化で置きます', unplaced: '置けませんでした', blocked: '置けません' } as const;
  return (
    <section className="space-y-1" data-testid="tray">
      <h3 className="text-xs font-semibold">まだ枠の無い方</h3>
      <ul className="space-y-1">
        {view.tray.map((t) => (
          <li key={t.patient_id} className="rounded border border-border-subtle bg-bg-base px-2 py-1 text-[11px]">
            <span className="font-medium">{t.name}</span>
            <span className="ml-1 text-text-muted">
              {t.days.length ? `${t.days.join('')}曜` : '曜日未定'}・{label[t.state]}
            </span>
            {t.reason ? <div className="text-text-muted">{t.reason}</div> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function DraftList({
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
  const [name, setName] = useState('');
  return (
    <aside className="flex w-52 shrink-0 flex-col gap-2" data-testid="draft-list">
      <h2 className="text-xs font-semibold">下書き</h2>
      <form
        className="flex gap-1"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) {
            onCreate(name.trim());
            setName('');
          }
        }}
      >
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="新しい下書きの名前"
          aria-label="新しい下書きの名前"
          className="min-w-0 flex-1 rounded border border-border-default bg-bg-base px-1.5 py-1 text-xs"
        />
        <Button type="submit" size="sm" disabled={busy || !name.trim()} data-testid="draft-create">
          作る
        </Button>
      </form>
      <p className="text-[11px] text-text-muted">いまの型を写して、仮の 1 週間を作ります。</p>
      <ul className="space-y-1">
        {drafts.map((d) => (
          <li
            key={d.id}
            className={`rounded border px-2 py-1 text-xs ${
              d.id === selectedId ? 'border-brand-primary bg-brand-primary-50' : 'border-border-default bg-bg-base'
            }`}
          >
            <button type="button" className="w-full text-left" onClick={() => onSelect(d.id)}>
              <span className="font-medium">{d.name}</span>
              {d.status === 'committed' ? <span className="ml-1 text-[10px] text-success">登録済み</span> : null}
            </button>
            {d.id === selectedId && d.status === 'draft' ? (
              <div className="mt-1 flex gap-2 text-[11px]">
                <button
                  type="button"
                  className="text-brand-primary underline"
                  onClick={() => {
                    const n = window.prompt('新しい名前', d.name);
                    if (n && n.trim()) onRename(d.id, n.trim());
                  }}
                >
                  名前を変える
                </button>
                <button type="button" className="text-text-muted underline" onClick={() => onArchive(d.id)}>
                  しまう
                </button>
              </div>
            ) : null}
          </li>
        ))}
        {drafts.length === 0 ? <li className="text-[11px] text-text-muted">まだ下書きはありません。</li> : null}
      </ul>
    </aside>
  );
}
