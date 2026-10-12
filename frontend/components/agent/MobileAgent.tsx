'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { useAgentMaybe, type AgentContextValue } from './AgentProvider';
import { Conversation } from './Conversation';
import { EngineSettingsButton } from './EngineSettings';
import { Composer, MascotStage, PlanViewer, poseSrc } from './parts';
import { plansOf, sessionList } from './reducer';
import { SessionList } from './SessionList';
import type { PlanInfo } from './types';

import './agent.css';

const IDEAS = ['田中さんが急にお休み', '来週は人が足りてる？', '特別訪問週間が入った'];

/** 下へなぞって閉じる(シート)/左へなぞって閉じる(ドロワー)の共通処理 */
function useSwipe(axis: 'down' | 'left', onClose: () => void, threshold = 80) {
  const start = useRef<{ x: number; y: number } | null>(null);
  return {
    onTouchStart: (e: React.TouchEvent) => {
      const t = e.touches[0];
      if (t) start.current = { x: t.clientX, y: t.clientY };
    },
    onTouchEnd: (e: React.TouchEvent) => {
      const s = start.current;
      start.current = null;
      const t = e.changedTouches[0];
      if (!s || !t) return;
      if (axis === 'down' && t.clientY - s.y > threshold) onClose();
      if (axis === 'left' && s.x - t.clientX > threshold) onClose();
    },
  };
}

/** 通知・バナーには人の名前を入れない(PERSONA §5)。別の相談が確認待ちになった時だけ出す。 */
function AttentionBanner({ a, onOpen }: { a: AgentContextValue; onOpen: (id: string) => void }) {
  const [dismissed, setDismissed] = useState<string[]>([]);
  const target = Object.values(a.store.sessions).find(
    (s) => s.needs_you && s.id !== a.store.currentId && !dismissed.includes(s.id),
  );
  useEffect(() => {
    if (!target) return;
    const t = setTimeout(() => setDismissed((d) => [...d, target.id]), 8000);
    return () => clearTimeout(t);
  }, [target]);
  if (!target) return null;
  return (
    <button
      type="button"
      className="rk-banner"
      role="status"
      onClick={() => {
        setDismissed((d) => [...d, target.id]);
        onOpen(target.id);
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/brand/rakusuke-icon-round.png" alt="" width={28} height={28} />
      <span>らく助：確認をお願いします</span>
      <b>開く ›</b>
    </button>
  );
}

function Drawer({
  a,
  open,
  onClose,
  onPick,
}: {
  a: AgentContextValue;
  open: boolean;
  onClose: () => void;
  onPick: (id: string) => void;
}) {
  const router = useRouter();
  const swipe = useSwipe('left', onClose);
  if (!open) return null;
  return (
    <>
      <div className="rk-scrim" data-testid="rk-drawer-scrim" onClick={onClose} />
      <aside className="rk-drawer" role="dialog" aria-label="相談の一覧" {...swipe}>
        <div className="rk-d-head">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand/rakusuke-icon-round.png" alt="" width={32} height={32} />
          <b>らく助に相談</b>
          <EngineSettingsButton />
          <button type="button" className="rk-hbtn" aria-label="閉じる" onClick={onClose}>
            ✕
          </button>
        </div>
        <SessionList
          sessions={sessionList(a.store)}
          currentId={a.store.currentId}
          onSelect={(id) => {
            onPick(id);
            onClose();
          }}
          onNew={() => {
            void a.newSession().then((id) => onPick(id));
            onClose();
          }}
          onRename={(id, t) => void a.rename(id, t)}
        />
        <div className="rk-d-foot">
          <button type="button" className="rk-btn" onClick={() => router.push('/m/home')}>
            ホームへ戻る
          </button>
        </div>
      </aside>
    </>
  );
}

/** /m/agent — 相談の入口(あいさつ・新しい相談・最近の相談)。下タブあり。 */
export function MobileAgentHome() {
  const a = useAgentMaybe();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  if (!a) return <p className="rk-muted rk-pad">この画面は今はご利用いただけません。</p>;
  const go = (id: string) => {
    a.select(id);
    router.push(`/m/agent/${id}`);
  };
  const start = async (text?: string) => {
    const id = await a.newSession();
    if (text) await a.send(text);
    router.push(`/m/agent/${id}`);
  };
  return (
    <div className="rk-mhome">
      <AttentionBanner a={a} onOpen={go} />
      <header className="rk-mtop">
        <button type="button" className="rk-hbtn" aria-label="相談の一覧" onClick={() => setOpen(true)}>
          ☰{a.waitingCount > 0 && <span className="rk-pip" aria-label="確認待ちがあります" />}
        </button>
        <div className="rk-title">
          <b>相談</b>
          <span className="rk-muted">らく助に何でも頼めます(管理者だけ)</span>
        </div>
      </header>
      <MascotStage
        pose="wave"
        line="こんにちは、らく助です。今日はどんなお手伝いをしましょう？"
        size={72}
      />
      <button type="button" className="rk-btn rk-primary rk-big" onClick={() => void start()}>
        ＋ 新しい相談
      </button>
      <h4 className="rk-sec">たとえば</h4>
      <div className="rk-hideas">
        {IDEAS.map((i) => (
          <button key={i} type="button" className="rk-idea" onClick={() => void start(i)}>
            {i}
          </button>
        ))}
      </div>
      <Drawer a={a} open={open} onClose={() => setOpen(false)} onPick={go} />
    </div>
  );
}

/** /m/agent/[sessionId] — 会話。全画面(下タブは出さない)。 */
export function MobileAgentConversation({ sessionId }: { sessionId: string }) {
  const a = useAgentMaybe();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [sheet, setSheet] = useState<string | null>(null);
  const [attach, setAttach] = useState<string | null>(null);
  const select = a?.select;
  useEffect(() => {
    select?.(sessionId);
  }, [select, sessionId]);
  const swipe = useSwipe('down', () => setSheet(null));

  if (!a) return <p className="rk-muted rk-pad">この画面は今はご利用いただけません。</p>;
  const cur = a.store.sessions[sessionId];
  if (!cur) {
    return (
      <div className="rk-mconv">
        <p className="rk-muted rk-pad">相談を読み込んでいます…</p>
      </div>
    );
  }
  const busy = ['working', 'starting', 'applying'].includes(cur.state);
  const plans: PlanInfo[] = plansOf(cur);
  const latest = plans[plans.length - 1];
  const pending = cur.items.find(
    (i) => i.kind === 'approval' && !i.decision && i.planId === sheet,
  );
  const attachPlan = attach ? cur.plans[attach] : undefined;
  const go = (id: string) => {
    a.select(id);
    router.push(`/m/agent/${id}`);
  };

  return (
    <div className="rk-mconv" data-testid="rk-mconv">
      <AttentionBanner a={a} onOpen={go} />
      <header className="rk-mtop">
        <button type="button" className="rk-hbtn" aria-label="相談の一覧" onClick={() => setOpen(true)}>
          ☰{a.waitingCount > 0 && <span className="rk-pip" aria-label="確認待ちがあります" />}
        </button>
        <div className="rk-title">
          <b>{cur.title}</b>
          <span className="rk-chip" data-st={busy ? 'work' : cur.needs_you ? 'ask' : 'idle'}>
            <span className="rk-dot" data-st={busy ? 'work' : cur.needs_you ? 'ask' : 'idle'} />
            {busy ? 'お仕事中' : cur.needs_you ? 'ご確認をお願いします' : '待機中'}
          </span>
        </div>
        {latest && (
          <button type="button" className="rk-pill-btn" onClick={() => setSheet(latest.plan_id)}>
            案 {plans.length}
          </button>
        )}
      </header>
      {a.store.connection === 'lost' && (
        <p className="rk-lost" role="status">
          つながっていません。最後に受け取った内容です。
        </p>
      )}
      {a.diskWarning && (
        <p className="rk-disk" role="status">
          {a.diskWarning}
        </p>
      )}
      <div className="rk-mstage">
        <MascotStage pose={cur.pose} line={cur.line} working={busy} size={56} />
      </div>
      <div className="rk-mscroll">
        <Conversation session={cur} onOpenPlan={setSheet} />
      </div>
      <Composer
        mobile
        busy={busy}
        onStop={() => void a.stop()}
        onSend={(t) => {
          void a.send(t, attach ?? undefined);
          setAttach(null);
        }}
        attach={attachPlan ? `${attachPlan.label ?? '案'} について` : undefined}
        onClearAttach={() => setAttach(null)}
        ideas={cur.items.length === 0 ? IDEAS : undefined}
      />

      <Drawer a={a} open={open} onClose={() => setOpen(false)} onPick={go} />

      {sheet && (
        <>
          <div className="rk-scrim" data-testid="rk-sheet-scrim" onClick={() => setSheet(null)} />
          <section className="rk-sheet" role="dialog" aria-label="案">
            <div className="rk-grab" {...swipe}>
              <i />
            </div>
            <div className="rk-sheet-head">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={poseSrc('propose')} alt="" width={28} height={28} />
              <button
                type="button"
                className="rk-btn"
                onClick={() => {
                  setAttach(sheet);
                  setSheet(null);
                }}
              >
                これについて頼む
              </button>
              <button type="button" className="rk-btn" onClick={() => setSheet(null)}>
                会話に戻る ⌄
              </button>
            </div>
            <PlanViewer
              plans={plans}
              selectedId={sheet}
              onSelect={setSheet}
              pendingApprovalId={pending && pending.kind === 'approval' ? pending.approvalId : undefined}
              onDecide={(id, d) => {
                void a.decide(id, d);
              }}
              onRevise={(id, text) => {
                void a.send(text, id);
                setSheet(null);
              }}
              loadHtml={a.planHtml}
            />
          </section>
        </>
      )}
    </div>
  );
}
