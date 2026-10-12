/**
 * らく助エージェント: 画面用の共通イベント(設計書 §1-5)と画面の状態の型。
 * 中継(agent-relay)と mockRelay は同じ形のイベントを流す。
 */

export type AgentState =
  | 'idle'
  | 'starting'
  | 'working'
  | 'waiting_answer'
  | 'waiting_approval'
  | 'applying'
  | 'error'
  | 'rate_limited'
  | 'queued';

export type Pose =
  | 'wave'
  | 'waiting'
  | 'think'
  | 'search'
  | 'laptop'
  | 'propose'
  | 'bow'
  | 'stamp'
  | 'puzzled'
  | 'joy';

export interface QuestionOption {
  label: string;
  description?: string;
}
export interface QuestionItem {
  question: string;
  header?: string;
  options: QuestionOption[];
  multiSelect?: boolean;
}

export type PlanSize = 'a4' | 'a3';

export type PlanStatus =
  | 'draft'
  | 'approved'
  | 'applying'
  | 'applied'
  | 'failed'
  | 'rejected'
  | 'stale'
  | 'superseded';

export type Device = 'pc' | 'mobile';
export type RunKind = 'apply' | 'undo';

export interface PlanInfo {
  plan_id: string;
  version: number;
  title: string;
  summary: string[];
  counts?: Record<string, number>;
  undoable?: boolean;
  ready?: boolean;
  blockers?: string[];
  status?: PlanStatus;
  /** 中継が返す HTML の道(画面は relay.planHtml で取る。直接は開かない) */
  html_urls?: Partial<Record<PlanSize, string>>;
  /** 案の HTML(試験用に直接入れられる。ふつうは relay.planHtml で取る) */
  html?: Partial<Record<PlanSize, string>>;
  /** 「案 1 / 案 2」の並び(同じ依頼の別案をまとめる) */
  group?: string;
  label?: string;
}

interface Base {
  seq: number;
  ts?: string;
  session_id?: string;
  turn_id?: number;
}

/** 中継(agent/relay/events.py)が流すイベント。形はこの 1 か所で決める。 */
export type AgentEvent =
  | (Base & { type: 'status'; state: AgentState; pose?: Pose; line?: string; resets_at?: string })
  | (Base & {
      type: 'user.message';
      text: string;
      user_id?: string;
      device?: Device;
      plan_ref?: number;
    })
  | (Base & { type: 'message.delta'; text: string })
  | (Base & { type: 'message'; text: string })
  | (Base & { type: 'thinking'; phase: 'start' | 'end' })
  | (Base & { type: 'step.start'; step_id: string; label: string })
  | (Base & { type: 'step.end'; step_id: string; label?: string; ok: boolean; secs?: number })
  | (Base & {
      type: 'steps.summary';
      count?: number;
      failed?: number;
      secs?: number;
      label?: string;
    })
  | (Base & { type: 'progress'; text: string })
  | (Base & { type: 'question'; question_id: string; questions: QuestionItem[] })
  | (Base & {
      type: 'question.answered';
      question_id: string;
      answers: Record<string, string>;
      user_id?: string;
      device?: Device;
    })
  | (Base & {
      type: 'plan';
      plan_id: string;
      version: number;
      title: string;
      summary: string[];
      counts?: Record<string, number>;
      undoable?: boolean;
      ready?: boolean;
      blockers?: string[];
      html_urls?: Partial<Record<PlanSize, string>>;
      status: PlanStatus;
    })
  | (Base & { type: 'approval.request'; kind: RunKind; version: number; plan_id: string })
  | (Base & {
      type: 'approval.result';
      version: number;
      kind: RunKind;
      decision: 'approve' | 'reject' | 'expired';
      user_id?: string;
      device?: Device;
      expires_at?: string;
      run_id?: string;
    })
  | (Base & {
      type: 'run.progress';
      run_kind: RunKind;
      i: number;
      total: number;
      label?: string;
      ok?: boolean;
    })
  | (Base & {
      type: 'run.result';
      run_kind: RunKind;
      run_id: string;
      ok: boolean;
      applied: number;
      total: number;
      error?: string | null;
      undoable?: boolean;
      backup?: string | null;
    })
  | (Base & { type: 'error'; code?: string; message: string; retry?: boolean })
  | (Base & { type: 'turn.end'; secs?: number; ok?: boolean; reason?: string });

export interface StepItem {
  id: string;
  label: string;
  done: boolean;
  ok?: boolean;
  secs?: number;
  result?: string;
}

export type TimelineItem =
  | { kind: 'user'; id: string; text: string; planRef?: string }
  | { kind: 'bot'; id: string; text: string; streaming: boolean }
  | { kind: 'work'; id: string; steps: StepItem[]; collapsed: boolean; secs: number }
  | {
      kind: 'question';
      id: string;
      questionId: string;
      questions: QuestionItem[];
      answers?: string[];
      by?: string;
    }
  | { kind: 'plan'; id: string; planId: string }
  | {
      kind: 'approval';
      id: string;
      /** 画面の中だけの鍵(`v<版>`)。承認番号(ap_…)ではない。承認番号は中継の中だけにある */
      approvalId: string;
      planId: string;
      version: number;
      note?: string;
      decision?: 'approved' | 'rejected' | 'expired';
      by?: string;
    }
  | {
      kind: 'run';
      id: string;
      runKind: RunKind;
      runId?: string;
      /** 元に戻すを頼んだ(承認した)あと */
      undoRequested?: boolean;
      ok?: boolean;
      done: number;
      total: number;
      line: string;
      undoable?: boolean;
      finished: boolean;
    }
  | { kind: 'error'; id: string; line: string; retry: boolean };

export interface SessionSummary {
  id: string;
  title: string;
  /** 最後に動いた時刻(ISO) */
  last_active_at: string;
  /** 「決めてほしいもの」に入るか */
  needs_you?: boolean;
  pinned?: boolean;
}

export interface SessionView extends SessionSummary {
  state: AgentState;
  pose: Pose;
  line: string;
  items: TimelineItem[];
  plans: Record<string, PlanInfo>;
  planOrder: string[];
  lastSeq: number;
  loaded: boolean;
  thinking: boolean;
}

export interface AgentStore {
  sessions: Record<string, SessionView>;
  order: string[];
  currentId: string | null;
  connection: 'ok' | 'lost';
}
