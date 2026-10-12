'use client';

import { useEffect, useLayoutEffect, useRef, useState } from 'react';

import { workLabel } from './reducer';
import type {
  PlanInfo,
  PlanSize,
  Pose,
  QuestionItem,
  StepItem,
  TimelineItem,
} from './types';

import './agent.css';

/** 押し間違い防止: 出てから 0.4 秒は押せない(設計書 §4-1) */
export const APPROVAL_GUARD_MS = 400;

export const PAGE_W: Record<PlanSize, number> = { a4: 794, a3: 1587 };
export const PAGE_H = 1123;

/* ── マスコット ─────────────────────────────────────────── */

const POSE_SRC: Record<Pose, string> = {
  wave: '/brand/rakusuke-pose-wave.png',
  think: '/brand/rakusuke-pose-think.png',
  puzzled: '/brand/rakusuke-pose-puzzled.png',
  joy: '/brand/rakusuke-pose-joy.png',
  waiting: '/brand/agent/rakusuke-pose-waiting.png',
  search: '/brand/agent/rakusuke-pose-search.png',
  laptop: '/brand/agent/rakusuke-pose-laptop.png',
  propose: '/brand/agent/rakusuke-pose-propose.png',
  bow: '/brand/agent/rakusuke-pose-bow.png',
  stamp: '/brand/agent/rakusuke-pose-stamp.png',
};

export function poseSrc(p: Pose): string {
  return POSE_SRC[p];
}

export function fmtClock(sec: number): string {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
}

/** 動いている間の経過時間(0:08 のような時計) */
export function useElapsed(active: boolean): number {
  const [s, setS] = useState(0);
  useEffect(() => {
    if (!active) {
      setS(0);
      return;
    }
    const t0 = Date.now();
    const id = setInterval(() => setS(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [active]);
  return s;
}

export function MascotStage({
  pose,
  line,
  working,
  size = 84,
}: {
  pose: Pose;
  line: string;
  working?: boolean;
  size?: number;
}) {
  const secs = useElapsed(!!working);
  return (
    <div className="rk-stage" data-testid="rk-stage">
      <div className="rk-fig" data-pose={pose} style={{ width: size, height: size }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={poseSrc(pose)} alt="" width={size} height={size} />
      </div>
      <div className="rk-say" aria-live="polite">
        {line}
        {working && (
          <>
            <span className="rk-typing" aria-hidden>
              <i />
              <i />
              <i />
            </span>
            <small>{fmtClock(secs)}</small>
          </>
        )}
      </div>
    </div>
  );
}

/* ── 作業のまとまり ─────────────────────────────────────── */

function StepRow({ s }: { s: StepItem }) {
  return (
    <div className="rk-step" data-ok={s.ok === false ? 'ng' : s.done ? 'ok' : 'run'}>
      <span className="rk-step-ic" aria-hidden>
        {!s.done ? <span className="rk-spin" /> : s.ok === false ? '⚠' : '✓'}
      </span>
      <span>
        {s.label}
        {s.result && <span className="rk-step-res">{s.result}</span>}
      </span>
      <span className="rk-step-t">{s.done && s.secs != null ? `${s.secs} 秒` : ''}</span>
    </div>
  );
}

export function WorkGroup({ item }: { item: Extract<TimelineItem, { kind: 'work' }> }) {
  const [open, setOpen] = useState(false);
  if (!item.collapsed) {
    return (
      <div className="rk-work rk-live" data-testid="rk-work-live">
        {item.steps.map((s) => (
          <StepRow key={s.id} s={s} />
        ))}
      </div>
    );
  }
  return (
    <div className="rk-work" data-open={open}>
      <button
        type="button"
        className="rk-work-sum"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="rk-ok">✓</span>
        <span>{workLabel(item)}</span>
        <span className="rk-chev" aria-hidden>
          ▸
        </span>
      </button>
      {open && (
        <div className="rk-work-list">
          {item.steps.map((s) => (
            <StepRow key={s.id} s={s} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ── 押し間違い防止つきのボタン群 ───────────────────────── */

function useGuard(ms: number): boolean {
  const [ready, setReady] = useState(ms <= 0);
  useEffect(() => {
    if (ms <= 0) return;
    const t = setTimeout(() => setReady(true), ms);
    return () => clearTimeout(t);
  }, [ms]);
  return ready;
}

export function ApprovalCard({
  item,
  label,
  onDecide,
  guardMs = APPROVAL_GUARD_MS,
}: {
  item: Extract<TimelineItem, { kind: 'approval' }>;
  label?: string;
  onDecide: (approvalId: string, d: 'approve' | 'reject') => void;
  guardMs?: number;
}) {
  const ready = useGuard(guardMs);
  const done = !!item.decision;
  return (
    <div className="rk-card rk-approve" data-testid="rk-approval" data-done={done}>
      <h3>{label ? `${label} を反映してよいですか？` : 'この案で反映してよいですか？'}</h3>
      <p className="rk-note">
        {item.note ??
          '反映すると、らく助の予定がこのとおりに変わります。カイポケへはまだ送りません。'}
      </p>
      {done ? (
        <p className="rk-decided">
          {item.decision === 'approved' && '反映をお願いされました'}
          {item.decision === 'rejected' && '反映せずに止めました'}
          {item.decision === 'expired' && 'この確認は終わりました'}
          {item.by ? `（${item.by}）` : ''}
        </p>
      ) : (
        <div className="rk-btns">
          <button
            type="button"
            className="rk-btn rk-primary"
            disabled={!ready}
            // Enter では反映しない(押し間違い防止)。クリック・タップ・Space だけ。
            onKeyDown={(e) => {
              if (e.key === 'Enter') e.preventDefault();
            }}
            onClick={() => onDecide(item.approvalId, 'approve')}
          >
            この案で反映する
          </button>
          <button
            type="button"
            className="rk-btn"
            disabled={!ready}
            onClick={() => onDecide(item.approvalId, 'reject')}
          >
            やめる
          </button>
        </div>
      )}
    </div>
  );
}

/** 反映の結果のカード。元に戻せるものは［元に戻す］(押し間違い防止つき・Enter では押さない)。 */
export function RunCard({
  item,
  onUndo,
  guardMs = APPROVAL_GUARD_MS,
}: {
  item: Extract<TimelineItem, { kind: 'run' }>;
  onUndo?: (runId: string) => void;
  guardMs?: number;
}) {
  const ready = useGuard(guardMs);
  const undo = item.runKind === 'undo';
  const title = !item.finished
    ? undo
      ? '元に戻しています'
      : '反映しています'
    : item.ok
      ? undo
        ? '元に戻しました'
        : '反映しました'
      : '途中で止まりました';
  const canUndo =
    item.finished && !undo && !!item.undoable && !!item.runId && !item.undoRequested && !!onUndo;
  return (
    <div
      className="rk-card"
      data-testid="rk-run"
      data-tone={!item.finished ? 'run' : item.ok ? 'done' : 'warn'}
    >
      <h3>{title}</h3>
      <p>{item.line}</p>
      {!item.finished && <progress max={item.total} value={item.done} aria-label="反映の進み" />}
      {item.undoRequested && <p className="rk-decided">元に戻すことをお願いしました</p>}
      {canUndo && (
        <div className="rk-btns">
          <button
            type="button"
            className="rk-btn"
            disabled={!ready}
            onKeyDown={(e) => {
              if (e.key === 'Enter') e.preventDefault();
            }}
            onClick={() => item.runId && onUndo?.(item.runId)}
          >
            元に戻す
          </button>
        </div>
      )}
    </div>
  );
}

export function QuestionCard({
  item,
  onAnswer,
  guardMs = APPROVAL_GUARD_MS,
}: {
  item: Extract<TimelineItem, { kind: 'question' }>;
  onAnswer: (questionId: string, answers: string[]) => void;
  guardMs?: number;
}) {
  const ready = useGuard(guardMs);
  const [picked, setPicked] = useState<string[]>([]);
  const answered = item.answers != null;
  const q: QuestionItem | undefined = item.questions[0];
  if (!q) return null;
  return (
    <div className="rk-card rk-ask" data-testid="rk-question">
      <h3>{q.header ?? '確認させてください'}</h3>
      <p>{q.question}</p>
      {answered ? (
        <p className="rk-decided">
          {item.answers!.join('、')}
          {item.by ? `（${item.by}が答えました）` : ''}
        </p>
      ) : (
        <div className="rk-choices">
          {q.options.map((o) => (
            <button
              key={o.label}
              type="button"
              className="rk-btn rk-choice"
              disabled={!ready}
              data-on={picked.includes(o.label)}
              onClick={() =>
                q.multiSelect
                  ? setPicked((p) => (p.includes(o.label) ? p.filter((x) => x !== o.label) : [...p, o.label]))
                  : onAnswer(item.questionId, [o.label])
              }
            >
              {o.label}
              {o.description && <small>{o.description}</small>}
            </button>
          ))}
          {q.multiSelect && (
            <button
              type="button"
              className="rk-btn rk-primary"
              disabled={!ready || picked.length === 0}
              onClick={() => onAnswer(item.questionId, picked)}
            >
              これで決める
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* ── 案(A4 / A3)の表示 ───────────────────────────────────── */

/**
 * 案の HTML は iframe srcdoc + sandbox=""(スクリプト不可・不透明オリジン)で出す。
 * allow-scripts / allow-same-origin は付けない(設計書 §1-9)。
 * 幅に合わせる時は transform: scale で A4=794px / A3=1587px を縮める。
 */
export function PlanFrame({
  html,
  size,
  fit = true,
  title,
}: {
  html: string;
  size: PlanSize;
  fit?: boolean;
  title: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [avail, setAvail] = useState(PAGE_W[size]);
  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const measure = () => setAvail(el.clientWidth || PAGE_W[size]);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [size]);
  const w = PAGE_W[size];
  const scale = fit ? Math.min(1, Math.max(0.2, avail / w)) : 1;
  return (
    <div ref={wrap} className="rk-pv-scroll" data-testid="rk-plan-scroll">
      <div className="rk-pv-sizer" style={{ width: w * scale, height: PAGE_H * scale }}>
        <iframe
          title={title}
          srcDoc={html}
          sandbox=""
          referrerPolicy="no-referrer"
          width={w}
          height={PAGE_H}
          style={{ transform: `scale(${scale})`, transformOrigin: '0 0' }}
          className="rk-paper"
        />
      </div>
      <span className="rk-sr" data-testid="rk-plan-scale">
        {Math.round(scale * 100)}%
      </span>
    </div>
  );
}

export function PlanCard({
  plan,
  onOpen,
}: {
  plan: PlanInfo;
  onOpen: (planId: string) => void;
}) {
  return (
    <div className="rk-card rk-plan">
      <h3>
        <span className="rk-badge">{plan.label ?? '案'}</span>
        {plan.title}
      </h3>
      <ul>
        {plan.summary.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
      {plan.blockers && plan.blockers.length > 0 && (
        <p className="rk-note">反映の前に確かめたいこと: {plan.blockers.join('、')}</p>
      )}
      <div className="rk-btns">
        <button type="button" className="rk-btn" onClick={() => onOpen(plan.plan_id)}>
          案を開く
        </button>
      </div>
    </div>
  );
}

export interface PlanViewerProps {
  plans: PlanInfo[];
  selectedId: string;
  onSelect: (id: string) => void;
  pendingApprovalId?: string;
  onDecide: (approvalId: string, d: 'approve' | 'reject') => void;
  onRevise: (planId: string, text: string) => void;
  loadHtml: (planId: string, size: PlanSize) => Promise<string>;
  onPrint?: (planId: string, size: PlanSize) => void;
  onClose?: () => void;
  guardMs?: number;
}

export function PlanViewer({
  plans,
  selectedId,
  onSelect,
  pendingApprovalId,
  onDecide,
  onRevise,
  loadHtml,
  onPrint,
  onClose,
  guardMs = APPROVAL_GUARD_MS,
}: PlanViewerProps) {
  const plan = plans.find((p) => p.plan_id === selectedId) ?? plans[0];
  const [size, setSize] = useState<PlanSize>('a4');
  const [fit, setFit] = useState(true);
  const [html, setHtml] = useState<Record<string, string>>({});
  const [revise, setRevise] = useState(false);
  const [text, setText] = useState('');
  const ready = useGuard(guardMs);
  const key = plan ? `${plan.plan_id}:${size}` : '';
  const given = plan?.html?.[size];

  useEffect(() => {
    if (!plan || given != null || html[key] != null) return;
    let live = true;
    loadHtml(plan.plan_id, size)
      .then((h) => live && setHtml((m) => ({ ...m, [key]: h })))
      .catch(() => live && setHtml((m) => ({ ...m, [key]: '' })));
    return () => {
      live = false;
    };
  }, [plan, given, html, key, loadHtml, size]);

  if (!plan) return null;
  const tabs = plans.filter((p) => !plan.group || p.group === plan.group);
  const body = given ?? html[key];
  const old = plan.status === 'superseded' || plan.status === 'stale';
  const canAct = !!pendingApprovalId && !old;

  return (
    <section className="rk-pv" aria-label="案の欄">
      <div className="rk-pv-head">
        <span className="rk-badge">案</span>
        <b>{plan.title}</b>
        {onClose && (
          <button type="button" className="rk-hbtn" onClick={onClose}>
            閉じる
          </button>
        )}
      </div>
      <div className="rk-pv-bar">
        <div className="rk-tabs" role="tablist">
          {tabs.map((p) => (
            <button
              key={p.plan_id}
              type="button"
              role="tab"
              aria-selected={p.plan_id === plan.plan_id}
              className="rk-tab"
              data-on={p.plan_id === plan.plan_id}
              onClick={() => onSelect(p.plan_id)}
            >
              {p.label ?? '案'}
            </button>
          ))}
        </div>
        <div className="rk-seg">
          <button type="button" data-on={size === 'a4'} onClick={() => setSize('a4')}>
            A4 縦(一覧)
          </button>
          <button type="button" data-on={size === 'a3'} onClick={() => setSize('a3')}>
            A3 横(時間割)
          </button>
        </div>
        <div className="rk-seg">
          <button type="button" data-on={fit} onClick={() => setFit(true)}>
            幅に合わせる
          </button>
          <button type="button" data-on={!fit} onClick={() => setFit(false)}>
            100%
          </button>
        </div>
        {onPrint && (
          <button type="button" className="rk-hbtn" onClick={() => onPrint(plan.plan_id, size)}>
            印刷用に開く
          </button>
        )}
      </div>
      <p className="rk-pv-lock">
        らく助のサーバーが決まった様式で作った案です。反映するまで予定は変わりません。
      </p>
      {old && <p className="rk-pv-old">これは前の案です。反映は最新の案からできます。</p>}
      {body ? (
        <PlanFrame html={body} size={size} fit={fit} title={plan.title} />
      ) : (
        <div className="rk-pv-scroll rk-pv-wait">案を読み込んでいます…</div>
      )}
      <div className="rk-pv-act">
        {revise ? (
          <form
            className="rk-revise"
            onSubmit={(e) => {
              e.preventDefault();
              if (!text.trim()) return;
              onRevise(plan.plan_id, text.trim());
              setText('');
              setRevise(false);
            }}
          >
            <textarea
              aria-label="直してほしい所"
              value={text}
              rows={2}
              placeholder="合わせてほしい所を書いてください"
              onChange={(e) => setText(e.target.value)}
            />
            <button type="submit" className="rk-btn rk-primary" disabled={!text.trim()}>
              送る
            </button>
            <button type="button" className="rk-btn" onClick={() => setRevise(false)}>
              戻る
            </button>
          </form>
        ) : (
          <>
            <span className="rk-pv-note">
              {canAct ? 'この案で反映してよいですか？' : '反映の確認待ちではありません'}
            </span>
            <button
              type="button"
              className="rk-btn"
              disabled={!canAct || !ready}
              onClick={() => pendingApprovalId && onDecide(pendingApprovalId, 'reject')}
            >
              やめる
            </button>
            <button type="button" className="rk-btn" onClick={() => setRevise(true)}>
              直してほしい所を書く
            </button>
            <button
              type="button"
              className="rk-btn rk-primary"
              disabled={!canAct || !ready}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.preventDefault();
              }}
              onClick={() => pendingApprovalId && onDecide(pendingApprovalId, 'approve')}
            >
              この案で反映する
            </button>
          </>
        )}
      </div>
    </section>
  );
}

/* ── 入力欄 ──────────────────────────────────────────────── */

export function Composer({
  onSend,
  onStop,
  busy,
  attach,
  onClearAttach,
  mobile,
  ideas,
}: {
  onSend: (text: string) => void;
  onStop?: () => void;
  busy?: boolean;
  attach?: string;
  onClearAttach?: () => void;
  /** スマホ: Enter は改行。送るのは ➤ ボタン */
  mobile?: boolean;
  ideas?: string[];
}) {
  const [text, setText] = useState('');
  const composing = useRef(false);
  const submit = () => {
    const t = text.trim();
    if (!t) return;
    onSend(t);
    setText('');
  };
  return (
    <div className="rk-foot">
      {ideas && ideas.length > 0 && !busy && (
        <div className="rk-ideas">
          {ideas.map((i) => (
            <button key={i} type="button" className="rk-idea" onClick={() => onSend(i)}>
              {i}
            </button>
          ))}
        </div>
      )}
      {attach && (
        <div className="rk-attach">
          <span>{attach}</span>
          <button type="button" aria-label="外す" onClick={onClearAttach}>
            ✕
          </button>
        </div>
      )}
      <div className="rk-compose">
        <textarea
          aria-label="らく助に相談"
          rows={1}
          value={text}
          placeholder="らく助に相談する…"
          onChange={(e) => setText(e.target.value)}
          onCompositionStart={() => (composing.current = true)}
          onCompositionEnd={() => (composing.current = false)}
          onKeyDown={(e) => {
            // 変換の確定中は送らない。PC は Ctrl+Enter(Mac は Cmd+Enter)で送る。
            if (e.nativeEvent.isComposing || composing.current) return;
            if (!mobile && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {busy && onStop ? (
          <button type="button" className="rk-stop" onClick={onStop}>
            止める
          </button>
        ) : null}
        <button
          type="button"
          className="rk-send"
          aria-label="送る"
          disabled={!text.trim()}
          onClick={submit}
        >
          ➤
        </button>
      </div>
      {!mobile && <p className="rk-hint">Ctrl+Enter で送ります</p>}
    </div>
  );
}
