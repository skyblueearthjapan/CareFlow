/**
 * 型の作業場 (template workspace) — バックエンド /api/v1/template-ws の型.
 * 設計: docs/plans/agent-skills/template-workspace-design-2026-10-12.md
 * actor は API では 'human' | 'agent'。画面の表示は「あなた」「らく助エージェント」。
 */

export type Actor = 'human' | 'agent';

export interface DraftSummary {
  id: string;
  name: string;
  status: 'draft' | 'committed' | 'archived';
  version: number;
  office_id: string;
  updated_at: string | null;
  created_at: string | null;
}

export interface Metrics {
  travel_min: number;
  conflicts: number;
  over_cap: number;
  no_course: number;
  unstaffed: number;
  outside_shift?: number;
  outside_window?: number;
  ng_or_sex?: number;
  manager_visits?: number;
}

export interface CourseHead {
  id: string;
  label: string;
  short: string;
  manager: boolean;
}

export interface DayCourse {
  id: string;
  label: string;
  manager: boolean;
  virtual: boolean;
  staff: string;
  shift: [number, number];
  events: [number, number][];
  cap: number;
}

export interface SlotCard {
  key: string;
  patient_id: string;
  patient: string;
  code: string | null;
  slot_index: number;
  weekday: number;
  start: number | null;
  dur: number;
  course_id: string | null;
  base_course_id: string | null;
  base_start: number | null;
  locked: boolean;
  new: boolean;
  held: boolean;
  pair: boolean;
  course_changed: boolean;
  time_changed: boolean;
  changed: boolean;
  win: [number, number];
  why: string;
  time_type: string;
  placed: boolean;
  unplaced_reason: string | null;
}

export interface DayView {
  weekday: number;
  name: string;
  courses: DayCourse[];
  slots: SlotCard[];
  notes: string[];
  metrics: { master: Metrics; draft: Metrics; changes: number; new_placed: number; new_unplaced: number };
}

export interface TrayItem {
  patient_id: string;
  code: string | null;
  name: string;
  days: string[];
  reason: string | null;
  state: 'waiting' | 'unplaced' | 'blocked';
  added: boolean;
}

export interface StepChange {
  key: string;
  patient_id: string;
  weekday: number;
  patient?: string | null;
  before: unknown;
  after: unknown;
}

export interface Step {
  seq: number;
  actor: Actor;
  actor_label: string;
  kind: 'move' | 'optimise' | 'whatif' | 'add' | 'remove' | 'commit';
  reason: string | null;
  undone: boolean;
  version_after: number;
  created_at: string | null;
  changed_slots: number;
  changes: StepChange[];
  params_changed: boolean;
}

export interface DraftView {
  id: string;
  name: string;
  status: DraftSummary['status'];
  version: number;
  office: { id: string; label: string };
  scenario: {
    staff_count: Record<string, number>;
    drop_staff: string[];
    add_patients: Record<string, unknown>;
    prefs: Record<string, unknown>;
    red: 'keep' | 'ignore';
  };
  compare: { name: string; draft_id: string | null };
  courses: CourseHead[];
  days: DayView[];
  tray: TrayItem[];
  staff: { code: string; name: string }[];
  totals: { master: Metrics; draft: Metrics } | Record<string, never>;
  steps: Step[];
  latest_step: Step | null;
  can_undo: boolean;
  can_redo: boolean;
}

export interface CommitPreview {
  draft_id: string;
  version: number;
  ready: boolean;
  blockers: string[];
  warnings: string[];
  counts: { patients: number; slots: number; course: number; time: number; new: number };
  patients: {
    patient_id: string;
    name: string;
    code: string | null;
    changes: {
      weekday: string;
      new: boolean;
      locked: boolean;
      from_course: string;
      to_course: string;
      from_start: string;
      to_start: string;
      course_changed: boolean;
      time_changed: boolean;
    }[];
  }[];
  note: string;
}

export interface WeekPreview {
  iso_year: number;
  iso_week: number;
  week_hash: string;
  counts: { course: number; time: number; add: number; skipped: number };
  skipped: { patient: string; weekday: string; reason: string }[];
  course_notes: { course: string; weekday: string; note: string }[];
  note: string;
}

export interface Job {
  id: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  error?: string;
  result?: { changed: number; version: number; notes: string[] };
}

export interface CompareResult {
  a: string;
  b: string;
  days: { weekday: string; differ: number; a: Metrics; b: Metrics }[];
  changed: {
    key: string;
    weekday: string;
    patient: string;
    red: boolean;
    a: { course: string | null; start: string | null };
    b: { course: string | null; start: string | null };
  }[];
}

export interface InputsView {
  weekday: number;
  name: string;
  rules: {
    speed_kmh: number;
    buffer_min: number;
    same_address_m: number;
    lunch_window: [number, number];
    lunch_min: number;
    lunch_target: number;
    event_buffer_min: number;
    cap_regular: number;
    time_order: string;
  };
  courses: DayCourse[];
  off_staff: string[];
  slots: {
    key: string;
    patient: string;
    code: string | null;
    start: number;
    dur: number;
    rule: 'red' | 'fixed' | 'window';
    win: [number, number];
    why: string;
    courses: string[];
  }[];
  matrix: { patients: { name: string; code: string | null }[]; minutes: number[][] };
}
