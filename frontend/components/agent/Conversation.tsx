'use client';

import { useEffect, useRef } from 'react';

import { useAgent } from './AgentProvider';
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
  const n = session.items.length;
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: 'end' });
  }, [n]);

  return (
    <div className="rk-conv" role="log" aria-live="polite" aria-label="相談の内容">
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
          case 'error':
            return (
              <div key={it.id} className="rk-card" data-tone="warn" role="alert">
                <h3>申し訳ありません</h3>
                <p>{it.line}</p>
              </div>
            );
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
