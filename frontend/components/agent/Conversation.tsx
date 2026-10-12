'use client';

import { useEffect, useRef } from 'react';

import { useAgent } from './AgentProvider';
import { EngineSettingsButton } from './EngineSettings';
import { ApprovalCard, PlanCard, QuestionCard, RunCard, WorkGroup, poseSrc } from './parts';
import type { SessionView } from './types';

/**
 * 会話の表示。らく助の文は「文字」として描く(dangerouslySetInnerHTML は使わない)。
 * key は item.id で固定(差分で描く)。
 */
export function Conversation({
  session,
  onOpenPlan,
}: {
  session: SessionView;
  onOpenPlan: (planId: string) => void;
}) {
  const { answer, decide, undo } = useAgent();
  const end = useRef<HTMLDivElement>(null);
  const conv = useRef<HTMLDivElement>(null);
  // 下に追従する: いちばん下を見ている間は、返事が伸びても・作業の行が増えても下へ付いていく。
  // 人が上へスクロールして読み返している間は動かさない(PO 2026-10-13「下へのスクロールが追従してくれると」)
  const pinned = useRef(true);
  const n = session.items.length;
  const lastKind = n > 0 ? session.items[n - 1]?.kind : undefined;
  useEffect(() => {
    if (lastKind === 'user') pinned.current = true; // 自分が送ったら必ず下へ
    if (pinned.current) end.current?.scrollIntoView?.({ block: 'end' });
  }, [n, lastKind, session.thinking]);

  useEffect(() => {
    const el = conv.current;
    if (!el) return;
    let scroller: HTMLElement | null = el.parentElement;
    while (scroller && scroller !== document.body) {
      const oy = getComputedStyle(scroller).overflowY;
      if (oy === 'auto' || oy === 'scroll') break;
      scroller = scroller.parentElement;
    }
    if (!scroller || scroller === document.body) return;
    const box = scroller;
    const onScroll = () => {
      pinned.current = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    };
    box.addEventListener('scroll', onScroll, { passive: true });
    const follow = () => {
      if (pinned.current) box.scrollTop = box.scrollHeight;
    };
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(follow) : null;
    ro?.observe(el);
    return () => {
      box.removeEventListener('scroll', onScroll);
      ro?.disconnect();
    };
  }, []);

  return (
    <div ref={conv} className="rk-conv" role="log" aria-live="polite" aria-label="相談の内容">
      {session.items.map((it) => {
        switch (it.kind) {
          case 'user':
            return (
              <div key={it.id} className="rk-msg-user">
                {it.text}
              </div>
            );
          case 'bot':
            return (
              <div key={it.id} className="rk-msg-bot">
                <span className="rk-mini-av">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src="/brand/rakusuke-icon-round.png" alt="" width={26} height={26} />
                </span>
                <div className="rk-txt">{it.text}</div>
              </div>
            );
          case 'work':
            return <WorkGroup key={it.id} item={it} />;
          case 'question':
            return <QuestionCard key={it.id} item={it} onAnswer={(q, a) => void answer(q, a)} />;
          case 'plan': {
            const p = session.plans[it.planId];
            return p ? <PlanCard key={it.id} plan={p} onOpen={onOpenPlan} /> : null;
          }
          case 'approval':
            return (
              <ApprovalCard
                key={it.id}
                item={it}
                label={session.plans[it.planId]?.label}
                onDecide={(id, d) => void decide(id, d)}
              />
            );
          case 'run':
            return <RunCard key={it.id} item={it} onUndo={(r) => void undo(r)} />;
          case 'error': {
            // signin = 持ち主がその AI にサインインしていない(人ごとの席)。login = サインインが切れた。⚙ を開くボタンを添える
            const signin = it.code === 'signin' || it.code === 'login';
            return (
              <div
                key={it.id}
                className="rk-card"
                data-tone="warn"
                role={it.level === 'warn' ? 'status' : 'alert'}
                data-code={it.code}
              >
                <h3>
                  {signin
                    ? 'サインインをお願いします'
                    : it.level === 'warn'
                      ? 'お知らせ'
                      : '申し訳ありません'}
                </h3>
                <p>{it.line}</p>
                {signin && <EngineSettingsButton label="AI のつなぎ先を開く" />}
              </div>
            );
          }
          default:
            return null;
        }
      })}
      {session.thinking && (
        <div className="rk-msg-bot">
          <span className="rk-mini-av">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={poseSrc('think')} alt="" width={26} height={26} />
          </span>
          <div className="rk-txt rk-muted">考えています…</div>
        </div>
      )}
      <div ref={end} />
    </div>
  );
}
