/**
 * 「月の実績」タブの表示用の純関数。
 *
 * 型は `lib/schemas/monthly-actual.ts`（画面向けに変えた形）をそのまま使う。部品は
 * この型を props で受け取るだけにし、hooks をつなぐのはタブの最上位
 * （`MonthlyActualTab.tsx`）1 ファイルだけにする。
 */

import type {
  MonthlyActualJobSummary,
  MonthlyActualList,
  MonthlyActualRow,
} from '@/lib/schemas/monthly-actual';

/** 表で「する／しない」を切り替えられない（カイポケで足す・確かめる行）。 */
export function isLockedStatus(status: string): boolean {
  return status === 'missing_in_kaipoke' || status === 'kaipoke_only';
}

/** 'HH:MM' → 分。読めなければ null。 */
export function toMinutes(t: string | null | undefined): number | null {
  if (!t) return null;
  const m = /^(\d{1,2}):(\d{2})/.exec(t);
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

/** 送る時刻 − カイポケの時刻（分）。どちらかが無ければ null。 */
export function diffMinutes(
  next: string | null | undefined,
  kp: string | null | undefined,
): number | null {
  const a = toMinutes(next);
  const b = toMinutes(kp);
  return a === null || b === null ? null : a - b;
}

/** 'HH:MM:SS' → 'HH:MM'。 */
export function hm(t: string | null | undefined): string {
  return t ? t.slice(0, 5) : '';
}

export function timeRange(
  start: string | null | undefined,
  end: string | null | undefined,
): string {
  if (!start && !end) return '—';
  return `${hm(start) || '—'}〜${hm(end) || '—'}`;
}

/** 'YYYY-MM-DD' → 'M/D'。 */
export function shortDate(d: string): string {
  const m = /^\d{4}-(\d{2})-(\d{2})/.exec(d);
  return m ? `${Number(m[1])}/${Number(m[2])}` : d;
}

/** 'YYYY-MM' → 'YYYY 年 M 月'。 */
export function monthLabel(month: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(month);
  return m ? `${m[1]} 年 ${Number(m[2])} 月` : month;
}

/** 基準日の前の月から n か月さかのぼった 'YYYY-MM' の一覧（新しい順・反映は終わった月だけ）。 */
export function recentMonths(now: Date, n = 6): string[] {
  const out: string[] = [];
  for (let i = 1; i <= n; i += 1) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    out.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`);
  }
  return out;
}

export type ChipTone = 'red' | 'amber' | 'blue' | 'ok';

/** 印のコード（`judge.py` の FLAG_*）→ チップの短い言葉と色。知らないコードは BE の言葉のまま。 */
const FLAG_CHIPS: Record<string, { label: string; tone: ChipTone }> = {
  short_stay: { label: '30 分未満→到着＋35', tone: 'amber' },
  no_departure: { label: '退出なし→到着＋35', tone: 'amber' },
  long_stay: { label: '長い滞在', tone: 'amber' },
  big_gap: { label: '1 時間以上', tone: 'red' },
  overlap: { label: '重なり', tone: 'red' },
  staff_diff: { label: '担当が違う', tone: 'red' },
  staff_count: { label: '人数', tone: 'red' },
  k2_gap: { label: '記録書Ⅱ', tone: 'amber' },
  no_arrival: { label: '到着なし', tone: 'red' },
  dup_target: { label: '同じ行を 2 件', tone: 'red' },
  kp_end_before_start: { label: 'カイポケの時刻', tone: 'amber' },
  count_diff: { label: '訪問の数が違う', tone: 'red' },
  kp_longer: { label: 'カイポケの方が長い', tone: 'red' },
  order_swap: { label: '順番が入れ替わっている', tone: 'red' },
  code_variant: { label: '同じ名前の別の方かも', tone: 'red' },
};

export interface FlagChip {
  key: string;
  label: string;
  title: string;
  tone: ChipTone;
}

/** 行の印（＋状態・合わせた時刻から出る印）をチップにする。 */
export function flagChips(
  row: Pick<MonthlyActualRow, 'flags' | 'flag_details' | 'status' | 'rk_adjusted'> &
    Partial<Pick<MonthlyActualRow, 'needs_kaipoke_check'>>,
): FlagChip[] {
  const chips: FlagChip[] = [];
  const details = row.flag_details?.length
    ? row.flag_details
    : (row.flags ?? []).map((label) => ({ code: label, label, review: false }));
  for (const f of details) {
    const known = FLAG_CHIPS[f.code];
    chips.push({
      key: f.code,
      label: known?.label ?? f.label,
      title: f.label || known?.label || f.code,
      tone: known?.tone ?? (f.review ? 'red' : 'amber'),
    });
  }
  if (row.status === 'missing_in_kaipoke') {
    chips.push({
      key: 'missing',
      label: 'カイポケに無い',
      title: 'カイポケに実績が無い',
      tone: 'blue',
    });
  } else if (row.status === 'kaipoke_only') {
    chips.push({
      key: 'kp_only',
      label: 'カイポケだけ',
      title: 'らく助に訪問が無い',
      tone: 'blue',
    });
  }
  if (row.needs_kaipoke_check) {
    chips.push({
      key: 'kp_check',
      label: 'カイポケで確かめてから',
      title: '書き換えたか分かりません。カイポケで確かめ、実績を読み取り直してください',
      tone: 'red',
    });
  }
  if (row.rk_adjusted) {
    chips.push({
      key: 'adjusted',
      label: `合わせた：${row.rk_adjusted}`,
      title: '打刻履歴で時刻を合わせた',
      tone: 'ok',
    });
  }
  return chips;
}

/** 1 件の結果の言葉。 */
export function resultLabel(
  status: string | null | undefined,
): { label: string; ok: boolean } | null {
  switch (status ?? '') {
    case '':
      return null;
    case 'queued':
      return { label: '待ち', ok: true };
    case 'success':
      return { label: '反映した', ok: true };
    case 'already_applied':
      return { label: '反映済み', ok: true };
    case 'dry_run_ok':
      return { label: '試しで進めた', ok: true };
    case 'skipped':
      return { label: '飛ばした', ok: false };
    case 'failed':
    case 'error':
      return { label: '失敗', ok: false };
    case 'unknown':
      return { label: 'カイポケで確かめる', ok: false };
    default:
      return { label: String(status), ok: false };
  }
}

/** 反映済み（本番の成功・already_applied は試しで返っても済み）。BE の `is_applied` と同じ。 */
export function isDoneRow(r: MonthlyActualRow): boolean {
  return (
    r.result_reason === 'already_applied' ||
    (r.result_dry_run === false && r.result_status === 'success')
  );
}

/** これから送る行（「する」・送れる・まだ反映していない・本番で要目視になっていない）。 */
export function pendingRows(rows: MonthlyActualRow[]): MonthlyActualRow[] {
  return rows.filter(
    (r) =>
      r.apply &&
      r.selectable &&
      !isLockedStatus(r.status) &&
      !isDoneRow(r) &&
      !(r.result_dry_run === false && r.needs_kaipoke_check),
  );
}

/** 前の反映（試しでない）で失敗した行（カイポケで確かめる行は流し直さない）。 */
export function failedRows(rows: MonthlyActualRow[]): MonthlyActualRow[] {
  return rows.filter(
    (r) => r.result_dry_run === false && r.result_status === 'failed' && !r.needs_kaipoke_check,
  );
}

export function isJobRunning(job: MonthlyActualJobSummary | null | undefined): boolean {
  return !!job?.running;
}

/** 予約だけで、まだ動いていない。 */
export function isJobScheduled(job: MonthlyActualJobSummary | null | undefined): boolean {
  return !!job?.scheduled_at && !job.running;
}

/**
 * 試しが終わり、これから送る行が 1 件以上あり、その全部が今の時刻で試しを通った
 * （`dry_run_ok` かつ `dry_run_passed`）。反映済み（already_applied）は送る行に入らない。
 */
export function dryRunAllOk(view: MonthlyActualList | null | undefined): boolean {
  const job = view?.job;
  if (!view || !job?.dry_run || job.running) return false;
  const pending = pendingRows(view.rows);
  return (
    pending.length > 0 && pending.every((r) => r.result_status === 'dry_run_ok' && r.dry_run_passed)
  );
}

export type StepNo = 1 | 2 | 3 | 4 | 5 | 6;

/**
 * いまの段。
 *  1 読み取る（カイポケの実績がまだ無い・読み取り中）
 *  2 突き合わせ（まだ流していない）
 *  3 試しに流す（試しが動いている・試しで進まなかった行がある）
 *  4 反映する（試しが全部進んだ・予約した・反映が動いている）
 *  5 確かめる（反映が終わった）
 *  6 一覧を出す（反映の後に読み取り直して確かめた）
 */
export function currentStep(view: MonthlyActualList | null | undefined): StepNo {
  if (!view?.kp_fetched_at) return 1;
  const job = view.job;
  if (isJobScheduled(job)) return 4;
  if (!job || job.dry_run === null || job.dry_run === undefined) return 2;
  if (job.dry_run) {
    if (job.running) return 3;
    return dryRunAllOk(view) ? 4 : 3;
  }
  if (job.running) return 4;
  return job.state === 'verified' ? 6 : 5;
}
