/**
 * カイポケ連携ジョブの op 辞書 — 表示ラベルと「報告書の対象か」の単一ソース。
 *
 * これまで FE の 2 箇所 (KaipokeConsole の JOB_OP_LABELS / useInbound の
 * INBOUND_APPLY_OP_LABELS) に散っていた辞書をここへ集約する
 * (sync-result-report-design.md §5「op ラベル辞書を lib/kaipokeOps.ts に一本化」)。
 *
 * REPORTABLE_OPS = 連携結果レポート (GET …/jobs/{id}/report) の対象 op。
 * 実書込を行う 6 op のみ。プレビュー・export・expand は対象外 (ボタンを出さない)。
 */

/** params.op → 現場の言葉 (正典)。ジョブ一覧の「内容」列とレポートで使う。
 *  画面ごとの言い回しは下の override マップで差し替える (PO に見えている文言は変えない)。 */
export const KAIPOKE_OP_LABELS: Readonly<Record<string, string>> = {
  // 送信 (らく助 → カイポケ)
  apply: '訪問をカイポケへ送信',
  'events-outbound': 'イベントをカイポケへ送信',
  // 取込 (カイポケ → らく助)
  'apply-inbound': 'カイポケの差分を取込',
  'smart-apply': 'カイポケから取込（自動判別）',
  'replace-inbound': 'カイポケから置換取込',
  'apply-events': 'イベントを取込',
  // 計算・プレビュー (実書込なし)
  'diff-local': '差分計算',
  'diff-inbound': '取込差分計算',
  'events-preview': 'イベント取込プレビュー',
  'smart-preview': '取込プレビュー',
  // RPA 単体オペ
  export: 'カイポケ現況の取得',
  expand: '月間展開',
  diff: '差分計算(RPA)',
  'login-test': '接続テスト',
};

/** 連携コンソール「直近のジョブ履歴」用の言い回し。
 *  すぐ上の手順カード (WeeklyApplyControls) が Step 01/02/04・案内文が「①の…」「②で…」
 *  なので、履歴も同じ丸数字で対応が取れるようにする (2026-08-09 の表記を維持)。 */
export const CONSOLE_OP_LABELS: Readonly<Record<string, string>> = {
  expand: '①スケジュール展開',
  diff: '②差分を計算',
  apply: '④カイポケへ反映',
};

/** 「直近の取り込み」行の言い回し (取り込みカード内なので「取り込み（…）」で足りる)。 */
export const INBOUND_HISTORY_OP_LABELS: Readonly<Record<string, string>> = {
  'smart-apply': '取り込み（自動判別）',
  'replace-inbound': '取り込み（置換）',
  'apply-inbound': '取り込み（差分）',
};

/** 連携結果レポートを出せる op (実書込の 6 op)。 */
export const REPORTABLE_OPS: ReadonlySet<string> = new Set([
  'apply',
  'apply-inbound',
  'smart-apply',
  'replace-inbound',
  'apply-events',
  'events-outbound',
]);

/** ジョブの最小形 (KaipokeJob / LiveSnapshot.latestJob のどちらでも受けられる)。 */
export interface KaipokeJobLike {
  status?: string | null;
  job_type?: string | null;
  params?: Record<string, unknown> | null;
}

/** params.op を安全に取り出す (unknown レコードなので型を絞る)。 */
export function jobOp(job: KaipokeJobLike | null | undefined): string | null {
  const op = job?.params?.op;
  return typeof op === 'string' && op.length > 0 ? op : null;
}

/** op の表示ラベル。overrides → 正典 → op 名そのまま の順。op が無ければ null。 */
export function opLabel(
  op: string | null | undefined,
  overrides?: Readonly<Record<string, string>>,
): string | null {
  if (!op) return null;
  return overrides?.[op] ?? KAIPOKE_OP_LABELS[op] ?? op;
}

/** ジョブの表示ラベル。op が無ければ null (呼び出し側で job_type にフォールバック)。 */
export function jobOpLabel(
  job: KaipokeJobLike | null | undefined,
  overrides?: Readonly<Record<string, string>>,
): string | null {
  return opLabel(jobOp(job), overrides);
}

/** レポートボタンを出すか = 完了済み (成功/失敗) かつ対象 op。 */
export function isReportableJob(job: KaipokeJobLike | null | undefined): boolean {
  if (!job) return false;
  if (job.status !== 'completed' && job.status !== 'failed') return false;
  const op = jobOp(job);
  return op != null && REPORTABLE_OPS.has(op);
}
