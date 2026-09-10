import { describe, expect, it } from 'vitest';

import {
  CONSOLE_OP_LABELS,
  INBOUND_HISTORY_OP_LABELS,
  KAIPOKE_OP_LABELS,
  PLAN_ACTUAL_OP,
  REPORTABLE_OPS,
  isPlanActualJob,
  isReportableJob,
  jobMonth,
  jobOp,
  jobOpLabel,
  opLabel,
} from '../kaipokeOps';

describe('kaipokeOps', () => {
  it('jobOp は params.op を安全に取り出す', () => {
    expect(jobOp({ params: { op: 'apply' } })).toBe('apply');
    expect(jobOp({ params: {} })).toBeNull();
    expect(jobOp({ params: { op: 123 } as unknown as Record<string, unknown> })).toBeNull();
    expect(jobOp({})).toBeNull();
    expect(jobOp(null)).toBeNull();
  });

  it('jobOpLabel は辞書のラベル・未知の op は op 名・op 無しは null', () => {
    expect(jobOpLabel({ params: { op: 'apply' } })).toBe('訪問をカイポケへ送信');
    expect(jobOpLabel({ params: { op: 'smart-apply' } })).toBe('カイポケから取込（自動判別）');
    expect(jobOpLabel({ params: { op: 'unknown-op' } })).toBe('unknown-op');
    expect(jobOpLabel({ job_type: 'push', params: {} })).toBeNull();
  });

  it('画面ごとの override が正典より優先され、未指定 op は正典に落ちる', () => {
    // 連携コンソールの履歴は手順カードの Step 番号に合わせた丸数字表記
    expect(jobOpLabel({ params: { op: 'apply' } }, CONSOLE_OP_LABELS)).toBe('④カイポケへ反映');
    expect(jobOpLabel({ params: { op: 'expand' } }, CONSOLE_OP_LABELS)).toBe('①スケジュール展開');
    expect(jobOpLabel({ params: { op: 'diff' } }, CONSOLE_OP_LABELS)).toBe('②差分を計算');
    // override に無い op は正典のラベル
    expect(jobOpLabel({ params: { op: 'apply-events' } }, CONSOLE_OP_LABELS)).toBe(
      'イベントを取込',
    );
    // 「直近の取り込み」行は従来の言い回しのまま
    expect(opLabel('smart-apply', INBOUND_HISTORY_OP_LABELS)).toBe('取り込み（自動判別）');
    expect(opLabel('replace-inbound', INBOUND_HISTORY_OP_LABELS)).toBe('取り込み（置換）');
    expect(opLabel('apply-inbound', INBOUND_HISTORY_OP_LABELS)).toBe('取り込み（差分）');
    // 正典 (ジョブ一覧の「内容」列・レポート) は override の影響を受けない
    expect(opLabel('smart-apply')).toBe('カイポケから取込（自動判別）');
    expect(opLabel(null)).toBeNull();
  });

  it('REPORTABLE_OPS は実書込の 6 op のみ', () => {
    expect([...REPORTABLE_OPS].sort()).toEqual(
      [
        'apply',
        'apply-events',
        'apply-inbound',
        'events-outbound',
        'replace-inbound',
        'smart-apply',
      ].sort(),
    );
    // プレビュー・計算系は対象外
    for (const op of ['diff', 'diff-local', 'diff-inbound', 'smart-preview', 'export', 'expand']) {
      expect(REPORTABLE_OPS.has(op)).toBe(false);
      expect(KAIPOKE_OP_LABELS[op]).toBeTruthy(); // ラベルは持つ
    }
  });

  it('予実比較のラベルは正典・コンソールとも「予実比較（月）」', () => {
    expect(jobOpLabel({ params: { op: PLAN_ACTUAL_OP } })).toBe('予実比較（月）');
    expect(jobOpLabel({ params: { op: PLAN_ACTUAL_OP } }, CONSOLE_OP_LABELS)).toBe(
      '予実比較（月）',
    );
  });

  it('予実比較は専用エンドポイントなので REPORTABLE_OPS に入れない', () => {
    expect(REPORTABLE_OPS.has(PLAN_ACTUAL_OP)).toBe(false);
    expect(isReportableJob({ status: 'completed', params: { op: PLAN_ACTUAL_OP } })).toBe(false);
  });

  it('isPlanActualJob は op だけで判定する (状態は呼び出し側)', () => {
    expect(isPlanActualJob({ status: 'completed', params: { op: PLAN_ACTUAL_OP } })).toBe(true);
    expect(isPlanActualJob({ status: 'running', params: { op: PLAN_ACTUAL_OP } })).toBe(true);
    expect(isPlanActualJob({ status: 'completed', params: { op: 'apply' } })).toBe(false);
    expect(isPlanActualJob(null)).toBe(false);
  });

  it('jobMonth は params.month (YYYY-MM) だけを返す', () => {
    expect(jobMonth({ params: { month: '2026-08' } })).toBe('2026-08');
    expect(jobMonth({ params: { month: '2026-08-01' } })).toBeNull();
    expect(
      jobMonth({ params: { month: 202608 } as unknown as Record<string, unknown> }),
    ).toBeNull();
    expect(jobMonth({ params: {} })).toBeNull();
    expect(jobMonth(null)).toBeNull();
  });

  it('isReportableJob は completed / failed かつ対象 op のときだけ true', () => {
    expect(isReportableJob({ status: 'completed', params: { op: 'apply' } })).toBe(true);
    expect(isReportableJob({ status: 'failed', params: { op: 'smart-apply' } })).toBe(true);
    expect(isReportableJob({ status: 'running', params: { op: 'apply' } })).toBe(false);
    expect(isReportableJob({ status: 'pending', params: { op: 'apply' } })).toBe(false);
    expect(isReportableJob({ status: 'completed', params: { op: 'smart-preview' } })).toBe(false);
    expect(isReportableJob({ status: 'completed', params: {} })).toBe(false);
    expect(isReportableJob(null)).toBe(false);
  });
});
