'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { AgentProvider, useAgent } from './AgentProvider';
import { Conversation } from './Conversation';
import { EngineSettingsButton } from './EngineSettings';
import { Composer, MascotStage, PlanViewer, poseSrc } from './parts';
import { plansOf, sessionList } from './reducer';
import { SessionList } from './SessionList';
import type { AgentRelay } from './relay';
import { useScreenStore } from './screenContext';
import type { PlanInfo } from './types';
import { useAgentEnabled } from './useAgentEnabled';
import { useResizableWidth } from './useResizableWidth';

import './agent.css';

type Size = 'mini' | 'compact' | 'tall' | 'wide';

const POS_KEY = 'rk-agent-pos';
interface Pos {
  right: number;
  bottom: number;
}
const HOME: Pos = { right: 16, bottom: 16 };

/** 浮いている小窓の幅（左の端をつかんで変える・覚える）。広い表示は案を見るので、画面いっぱいまで。 */
const minusGutter = (vw: number) => vw - 32;

function loadPos(): Pos {
  try {
    const raw = window.localStorage.getItem(POS_KEY);
    if (!raw) return HOME;
    const p = JSON.parse(raw) as Partial<Pos>;
    if (typeof p.right === 'number' && typeof p.bottom === 'number') {
      return { right: Math.max(0, p.right), bottom: Math.max(0, p.bottom) };
    }
  } catch {
    /* 壊れていたら右下 */
  }
  return HOME;
}
function savePos(p: Pos | null) {
  try {
    if (p) window.localStorage.setItem(POS_KEY, JSON.stringify(p));
    else window.localStorage.removeItem(POS_KEY);
  } catch {
    /* 保存できなくても動く */
  }
}

/**
 * PC の小窓。管理者 かつ 試験の許可リスト かつ 機能の旗 rakusuke_agent が有効な時だけ描く
 * (どれかが外れていれば null)。AppShell に 1 つ置く。
 */
export function AgentWidget({ relay }: { relay?: AgentRelay }) {
  const enabled = useAgentEnabled();
  if (!enabled) return null;
  return (
    <AgentProvider device="pc" relay={relay}>
      <WidgetInner />
    </AgentProvider>
  );
}

function WidgetInner() {
  const a = useAgent();
  const { current, waitingCount } = a;
  const [size, setSize] = useState<Size>('mini');
  const [prevSize, setPrevSize] = useState<Size>('compact');
  const [pos, setPos] = useState<Pos>(HOME);
  const [dragging, setDragging] = useState(false);
  const [listOpen, setListOpen] = useState(false);
  const [planId, setPlanId] = useState<string | null>(null);
  const root = useRef<HTMLElement>(null);
  const drag = useRef<{ x: number; y: number; p: Pos } | null>(null);
  const compactW = useResizableWidth({ storageKey: 'rk-agent-w-compact', defaultWidth: 400, min: 300 });
  const tallW = useResizableWidth({ storageKey: 'rk-agent-w-tall', defaultWidth: 460, min: 300 });
  const wideW = useResizableWidth({ storageKey: 'rk-agent-w-wide', defaultWidth: 1240, min: 760, max: minusGutter });

  useEffect(() => setPos(loadPos()), []);

  // 型の作業場(§11-2): 右の列があればそこへ収まる。↘ で右下の丸へ、丸を押すと列へ戻る。
  // ページを離れたら(列が消えたら)右下の丸にたたむ。会話は同じもの(同じ Provider の中)。
  const dockEl = useScreenStore((s) => s.dock);
  const dockLabel = useScreenStore((s) => s.dockLabel);
  const setDocked = useScreenStore((s) => s.setDocked);
  const [inDock, setInDock] = useState(false);
  useEffect(() => {
    setInDock(!!dockEl);
    setPlanId(null);
    setSize(dockEl ? 'compact' : 'mini');
  }, [dockEl]);
  const docked = inDock && !!dockEl && size !== 'mini';
  useEffect(() => {
    setDocked(docked);
    return () => setDocked(false);
  }, [docked, setDocked]);

  const busy = current ? ['working', 'starting', 'applying'].includes(current.state) : false;
  const attention = waitingCount > 0;

  // タブの題名: 「(1) らく助」。勝手に開かない・フォーカスを奪わない。
  useEffect(() => {
    const base = document.title.replace(/^\(\d+\) /, '');
    const n = waitingCount + (busy ? 1 : 0);
    document.title = n > 0 && size === 'mini' ? `(${n}) ${base}` : base;
    return () => {
      document.title = document.title.replace(/^\(\d+\) /, '');
    };
  }, [waitingCount, busy, size]);

  const plans: PlanInfo[] = current ? plansOf(current) : [];
  const pending = current?.items.find(
    (i) => i.kind === 'approval' && !i.decision && i.planId === planId,
  );

  const openPlan = useCallback(
    (id: string) => {
      // 列の中では A4 の案の欄は使わない(盤面が案)。案を開くときだけ、浮かぶ小窓の広い形へ出る
      setInDock(false);
      setPlanId(id);
      setSize((s) => {
        if (s !== 'wide') setPrevSize(s === 'mini' ? 'compact' : s);
        return 'wide';
      });
    },
    [],
  );
  const closePlan = () => {
    setPlanId(null);
    setSize(prevSize);
  };

  // 動かせる: 見出しをつかんで。右下基準で覚える。ダブルクリックで右下へ。
  const onDown = (e: React.PointerEvent) => {
    if (size === 'wide' || (e.target as HTMLElement).closest('button')) return;
    drag.current = { x: e.clientX, y: e.clientY, p: pos };
    setDragging(true);
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const w = root.current?.offsetWidth ?? 400;
    const h = root.current?.offsetHeight ?? 560;
    setPos({
      right: Math.min(Math.max(0, d.p.right - (e.clientX - d.x)), window.innerWidth - w),
      bottom: Math.min(Math.max(0, d.p.bottom - (e.clientY - d.y)), window.innerHeight - h),
    });
  };
  const onUp = () => {
    if (!drag.current) return;
    drag.current = null;
    setDragging(false);
    savePos(pos);
  };
  const resetPos = () => {
    setPos(HOME);
    savePos(null);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Escape') return;
    if (size === 'wide') closePlan();
    else if (busy) void a.stop();
  };

  const diskLine = a.diskWarning ? (
    <p className="rk-disk" role="status">
      {a.diskWarning}
    </p>
  ) : null;

  const composer = (
    <Composer
      busy={busy}
      onStop={() => void a.stop()}
      onSend={(t) => void a.send(t, docked ? undefined : (planId ?? undefined))}
      ideas={current ? undefined : ['田中さんが急にお休み', '来週は人が足りてる？', '特別訪問週間が入った']}
    />
  );

  const body = listOpen ? (
    <div className="rk-body">
      <SessionList
        sessions={sessionList(a.store)}
        currentId={a.store.currentId}
        onSelect={(id) => {
          a.select(id);
          setListOpen(false);
        }}
        onNew={() => {
          void a.newSession();
          setListOpen(false);
        }}
        onRename={(id, t) => void a.rename(id, t)}
      />
    </div>
  ) : (
    <div className="rk-body">
      <MascotStage
        pose={current?.pose ?? 'wave'}
        line={
          current?.line ??
          'こんにちは、らく助です。お休みの反映や、人が足りているかの確認など、何でも言ってくださいね。'
        }
        working={busy}
      />
      {current && <Conversation session={current} onOpenPlan={openPlan} />}
    </div>
  );

  const chip = (
    <span className="rk-chip" data-st={busy ? 'work' : attention ? 'ask' : 'idle'}>
      <span className="rk-dot" data-st={busy ? 'work' : attention ? 'ask' : 'idle'} />
      {busy ? 'お仕事中' : attention ? 'ご確認をお願いします' : 'ご用件をどうぞ'}
    </span>
  );

  if (docked && dockEl) {
    return createPortal(
      <section className="rk-agent" data-size="dock" aria-label="らく助エージェント" onKeyDown={onKeyDown}>
        <div className="rk-chatcol">
          <header className="rk-head">
            <button
              type="button"
              className="rk-hbtn"
              aria-label="相談の一覧"
              onClick={() => setListOpen((o) => !o)}
            >
              ☰
            </button>
            <div className="rk-title">
              <b>{current?.title ?? 'らく助'}</b>
              {chip}
              <span className="rk-ctx" data-testid="rk-ctx">
                一緒に見ているもの：型の作業場{dockLabel ? '・' : ''}
                {dockLabel ? <b>{dockLabel}</b> : null}
              </span>
            </div>
            <div className="rk-hbtns">
              <EngineSettingsButton label="" />
              <button
                type="button"
                className="rk-hbtn"
                aria-label="右下にたたむ"
                title="右下にたたむ（会話はそのまま続きます）"
                onClick={() => {
                  setInDock(false);
                  setSize('mini');
                }}
              >
                ↘
              </button>
            </div>
          </header>
          {a.store.connection === 'lost' && (
            <p className="rk-lost" role="status">
              つながっていません。最後に受け取った内容です。
            </p>
          )}
          {diskLine}
          {body}
          {composer}
        </div>
      </section>,
      dockEl,
    );
  }

  if (size === 'mini') {
    return (
      <button
        type="button"
        className="rk-agent rk-pill"
        data-size="mini"
        style={{ right: pos.right, bottom: pos.bottom }}
        aria-label="らく助を開く"
        onClick={() => {
          if (dockEl) setInDock(true);
          setSize('compact');
        }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={poseSrc(current?.pose ?? 'wave')} alt="" width={40} height={40} />
        <span className="rk-pill-line">{current?.line ?? 'ご用件をどうぞ'}</span>
        {(busy || attention) && <span className="rk-dot" data-st={attention ? 'ask' : 'work'} />}
      </button>
    );
  }

  const wide = size === 'wide';
  const sized = size === 'wide' ? wideW : size === 'tall' ? tallW : compactW;
  const style = wide
    ? { right: HOME.right, bottom: HOME.bottom, width: sized.width }
    : { right: pos.right, bottom: pos.bottom, width: sized.width };

  return (
    <section
      ref={root}
      className="rk-agent"
      data-size={size}
      data-dragging={dragging}
      style={style}
      aria-label="らく助エージェント"
      onKeyDown={onKeyDown}
    >
      <div
        className="rk-resize"
        {...sized.handleProps}
        aria-label="小窓の幅"
        data-testid="rk-float-resize"
      />
      <div className="rk-chatcol">
        <header
          className="rk-head"
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onDoubleClick={resetPos}
        >
          <button
            type="button"
            className="rk-hbtn"
            aria-label="相談の一覧"
            onClick={() => setListOpen((o) => !o)}
          >
            ☰
          </button>
          <div className="rk-title">
            <b>{current?.title ?? 'らく助'}</b>
            {chip}
          </div>
          <div className="rk-hbtns">
            <EngineSettingsButton />
            {size !== 'wide' && (
              <button
                type="button"
                className="rk-hbtn"
                onClick={() => setSize(size === 'tall' ? 'compact' : 'tall')}
              >
                {size === 'tall' ? '小さく' : '広げる'}
              </button>
            )}
            <button type="button" className="rk-hbtn" aria-label="たたむ" onClick={() => setSize('mini')}>
              ー
            </button>
          </div>
        </header>

        {a.store.connection === 'lost' && (
          <p className="rk-lost" role="status">
            つながっていません。最後に受け取った内容です。
          </p>
        )}

        {diskLine}
        {body}
        {composer}
      </div>

      {wide && planId && current && (
        <PlanViewer
          plans={plans}
          selectedId={planId}
          onSelect={setPlanId}
          pendingApprovalId={pending && pending.kind === 'approval' ? pending.approvalId : undefined}
          onDecide={(id, d) => void a.decide(id, d)}
          onRevise={(id, text) => void a.send(text, id)}
          loadHtml={a.planHtml}
          onPrint={(id, s) =>
            void a.printUrl(id, s).then((u) => u && window.open(u, '_blank', 'noopener'))
          }
          onClose={closePlan}
        />
      )}
    </section>
  );
}
