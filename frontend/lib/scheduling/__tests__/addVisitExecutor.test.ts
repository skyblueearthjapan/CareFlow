/**
 * addVisitExecutor — 「＋訪問」の実行オーケストレータ (add-visit-anywhere-design.md §3-3 ⑤)。
 *
 * 観点 (§9):
 *   - 日付順に実行し、**最初の失敗で止める**（成功分 / 失敗 / 未実行の 3 分割）
 *   - 反映先ごとの payload の形 ('new' / 'week' / 'pattern')
 *   - 2 名体制は course_template_ids、コース未解決 (臨) は POST /visits
 *   - 型 (pattern) は既存行を読んでマージする
 *   - NG/性別の 422 は kind='constraint' + index で返す (acknowledge 再送の起点)
 */
import { describe, it, expect, vi } from 'vitest';

import { ApiError } from '@/lib/api-client';
import {
  buildPatternItems,
  executeAddVisitPlan,
  MOVE_COURSE_UNRESOLVED_MESSAGE,
  MOVE_SOURCE_MISSING_MESSAGE,
  MOVE_SOURCE_WRONG_WEEK_MESSAGE,
  type AddVisitExecDeps,
} from '../addVisitExecutor';
import type { AddVisitPlan, AddVisitPlanItem, VisitLite } from '../addVisitPlan';

const PATIENT = '00000000-0000-4000-8000-000000000001';
const TPL_A = '00000000-0000-4000-8000-00000000000a';
const TPL_B = '00000000-0000-4000-8000-00000000000b';
const OP_GROUP = '00000000-0000-4000-8000-0000000000ff';

function item(over: Partial<AddVisitPlanItem> = {}): AddVisitPlanItem {
  return {
    date: '2026-09-14',
    isoYear: 2026,
    isoWeek: 38,
    weekday: 0,
    startHM: '12:00',
    minutes: 35,
    officeId: 'office-honten',
    courseTemplateId: TPL_A,
    courseLabel: '稲毛A',
    isM: false,
    isOtherOffice: false,
    staffCount: 1,
    partnerCourseTemplateId: null,
    reason: null,
    scope: 'new',
    sourceVisit: null,
    noCandidateReason: null,
    ...over,
  };
}

function visitLite(over: Partial<VisitLite> = {}): VisitLite {
  return {
    id: 'v-old',
    visit_date: '2026-09-15',
    start_time: '09:30',
    end_time: '10:05',
    primary_staff_id: null,
    week_pinned: false,
    status: 'planned',
    source: 'auto',
    ...over,
  };
}

function pfvRow(over: Record<string, unknown> = {}) {
  return {
    id: 'pfv-x',
    patient_id: PATIENT,
    mode: 'normal' as const,
    created_at: '',
    updated_at: '',
    weekday: 0,
    start_time: '09:30:00',
    duration_min: 35,
    course_template_id: TPL_B,
    sub_office_id: null,
    slot_index: 0 as const,
    is_pinned: false,
    movability: 'unknown' as const,
    ...over,
  };
}

function makeDeps(over: Partial<AddVisitExecDeps> = {}): AddVisitExecDeps {
  return {
    placeAndFix: vi.fn(async () => ({
      visit: { id: 'v-new-1' },
      fixed_visit: null,
      visits: [{ id: 'v-new-1' }],
      fixed_visits: [],
      visit_group_id: null,
    })) as unknown as AddVisitExecDeps['placeAndFix'],
    moveWeekOnly: vi.fn(async () => ({ visits_moved: 1 })),
    putFixedVisits: vi.fn(async () => ({})),
    getFixedVisits: vi.fn(async () => []),
    createVisit: vi.fn(async () => ({ id: 'v-manual-1' })),
    patchVisitNote: vi.fn(async () => ({})),
    ...over,
  };
}

function plan(items: AddVisitPlanItem[]): AddVisitPlan {
  return { patientId: PATIENT, items };
}

function constraint422(): ApiError {
  return new ApiError('Unprocessable Entity', 422, {
    detail: {
      code: 'constraint_confirmation_required',
      warnings: [{ kind: 'ng_staff', patient_id: PATIENT, patient_name: '伊藤', staff_id: 's1' }],
    },
  });
}

describe('executeAddVisitPlan — 順序と停止', () => {
  it('日付順に実行する（計画の並びに依らない）', async () => {
    const seen: string[] = [];
    const deps = makeDeps({
      placeAndFix: vi.fn(async (req) => {
        seen.push(String(req.weekday));
        return {
          visit: { id: `v-${req.weekday}` },
          fixed_visit: null,
          visits: [{ id: `v-${req.weekday}` }],
          fixed_visits: [],
          visit_group_id: null,
        };
      }) as unknown as AddVisitExecDeps['placeAndFix'],
    });
    const res = await executeAddVisitPlan(
      plan([
        item({ date: '2026-09-19', weekday: 5 }),
        item({ date: '2026-09-14', weekday: 0 }),
        item({ date: '2026-09-17', weekday: 3 }),
      ]),
      deps,
      { opGroupId: OP_GROUP },
    );
    expect(seen).toEqual(['0', '3', '5']);
    expect(res.done).toHaveLength(3);
    expect(res.failed).toBeUndefined();
    expect(res.skipped).toHaveLength(0);
  });

  it('最初の失敗で止め、成功分・失敗・未実行を分けて返す', async () => {
    let n = 0;
    const deps = makeDeps({
      placeAndFix: vi.fn(async () => {
        n += 1;
        if (n === 2) throw new Error('boom');
        return {
          visit: { id: `v${n}` },
          fixed_visit: null,
          visits: [{ id: `v${n}` }],
          fixed_visits: [],
          visit_group_id: null,
        };
      }) as unknown as AddVisitExecDeps['placeAndFix'],
    });
    const res = await executeAddVisitPlan(
      plan([
        item({ date: '2026-09-14' }),
        item({ date: '2026-09-15' }),
        item({ date: '2026-09-16' }),
      ]),
      deps,
      { opGroupId: OP_GROUP },
    );
    expect(res.done.map((d) => d.item.date)).toEqual(['2026-09-14']);
    expect(res.failed?.item.date).toBe('2026-09-15');
    expect(res.failed?.index).toBe(1);
    expect(res.failed?.kind).toBe('error');
    expect(res.skipped.map((s) => s.date)).toEqual(['2026-09-16']);
    // 3 件目は呼ばれていない。
    expect(deps.placeAndFix).toHaveBeenCalledTimes(2);
  });

  it('startIndex から再開する（acknowledge 再送）', async () => {
    const deps = makeDeps();
    const res = await executeAddVisitPlan(
      plan([item({ date: '2026-09-14' }), item({ date: '2026-09-15' })]),
      deps,
      { opGroupId: OP_GROUP, startIndex: 1, acknowledgeIndex: 1 },
    );
    expect(deps.placeAndFix).toHaveBeenCalledTimes(1);
    expect(res.done.map((d) => d.item.date)).toEqual(['2026-09-15']);
    expect(
      (deps.placeAndFix as unknown as { mock: { calls: [Record<string, unknown>][] } }).mock
        .calls[0][0].acknowledge_constraint_warnings,
    ).toBe(true);
  });

  it('onProgress が index/total を通知する', async () => {
    const onProgress = vi.fn();
    await executeAddVisitPlan(
      plan([item({ date: '2026-09-14' }), item({ date: '2026-09-15' })]),
      makeDeps(),
      { opGroupId: OP_GROUP, onProgress },
    );
    expect(onProgress.mock.calls).toEqual([
      [0, 2],
      [1, 2],
    ]);
  });
});

describe("executeAddVisitPlan — scope 'new'", () => {
  it('place-and-fix を fix_pattern=false で叩く', async () => {
    const deps = makeDeps();
    const res = await executeAddVisitPlan(plan([item()]), deps, { opGroupId: OP_GROUP });
    expect(deps.placeAndFix).toHaveBeenCalledWith({
      patient_id: PATIENT,
      course_template_id: TPL_A,
      iso_year: 2026,
      iso_week: 38,
      weekday: 0,
      start_time: '12:00',
      duration_min: 35,
      staff_count: 1,
      fix_pattern: false,
      op_group_id: OP_GROUP,
    });
    expect(res.done[0]).toMatchObject({ kind: 'new', visitIds: ['v-new-1'] });
  });

  it('2 名体制は course_template_ids で 2 コースを渡す', async () => {
    const deps = makeDeps();
    await executeAddVisitPlan(
      plan([item({ staffCount: 2, partnerCourseTemplateId: TPL_B })]),
      deps,
      { opGroupId: OP_GROUP },
    );
    const req = (deps.placeAndFix as unknown as { mock: { calls: [Record<string, unknown>][] } })
      .mock.calls[0][0];
    expect(req.course_template_ids).toEqual([TPL_A, TPL_B]);
    expect(req.course_template_id).toBeUndefined();
    expect(req.staff_count).toBe(2);
  });

  it('コース未解決 (臨) は POST /visits へ落ちる', async () => {
    const deps = makeDeps();
    const res = await executeAddVisitPlan(
      plan([item({ courseTemplateId: null, courseLabel: '臨（コースなし）' })]),
      deps,
      { opGroupId: OP_GROUP },
    );
    expect(deps.placeAndFix).not.toHaveBeenCalled();
    expect(deps.createVisit).toHaveBeenCalledWith({
      patient_id: PATIENT,
      visit_date: '2026-09-14',
      start_time: '12:00',
      end_time: '12:35',
      type: 'regular',
      status: 'planned',
      source: 'manual_week',
      course_id: null,
      primary_staff_id: null,
    });
    expect(res.done[0]?.kind).toBe('new_manual');
  });

  it('L4: 臨 の M 配置理由は作成時の note に載せる (後追い PATCH をしない)', async () => {
    const deps = makeDeps();
    await executeAddVisitPlan(
      plan([
        item({
          courseTemplateId: null,
          courseLabel: '臨（コースなし）',
          isM: true,
          reason: '空きが無いため',
        }),
      ]),
      deps,
      { opGroupId: OP_GROUP },
    );
    expect(deps.createVisit).toHaveBeenCalledWith(
      expect.objectContaining({ note: 'M配置理由: 空きが無いため' }),
    );
    expect(deps.patchVisitNote).not.toHaveBeenCalled();
  });

  it('M を選んだ日の理由は note に残す（M でない / 空欄は残さない）', async () => {
    const deps = makeDeps();
    await executeAddVisitPlan(plan([item({ isM: true, reason: '空きが無いため' })]), deps, {
      opGroupId: OP_GROUP,
    });
    expect(deps.patchVisitNote).toHaveBeenCalledWith('v-new-1', 'M配置理由: 空きが無いため');

    const deps2 = makeDeps();
    await executeAddVisitPlan(plan([item({ isM: false, reason: '空きが無いため' })]), deps2, {
      opGroupId: OP_GROUP,
    });
    expect(deps2.patchVisitNote).not.toHaveBeenCalled();

    const deps3 = makeDeps();
    await executeAddVisitPlan(plan([item({ isM: true, reason: '  ' })]), deps3, {
      opGroupId: OP_GROUP,
    });
    expect(deps3.patchVisitNote).not.toHaveBeenCalled();
  });
});

describe("executeAddVisitPlan — scope 'week'", () => {
  it('元の曜日・時刻から移動先へ visit-move-week-only を叩く', async () => {
    const deps = makeDeps();
    const res = await executeAddVisitPlan(
      plan([
        item({
          scope: 'week',
          // 2026-09-15 は火曜。
          sourceVisit: visitLite({ visit_date: '2026-09-15', start_time: '09:30' }),
        }),
      ]),
      deps,
      { opGroupId: OP_GROUP },
    );
    expect(deps.moveWeekOnly).toHaveBeenCalledWith({
      iso_year: 2026,
      iso_week: 38,
      patient_id: PATIENT,
      old_weekday: 1,
      old_start_time: '09:30',
      new_weekday: 0,
      new_start_time: '12:00',
      new_course_template_id: TPL_A,
      op_group_id: OP_GROUP,
    });
    expect(res.done[0]).toMatchObject({ kind: 'week', visitIds: [] });
  });

  it('C1: visits_moved が 0 なら成功にしない (BE は 200 を返す)', async () => {
    const deps = makeDeps({ moveWeekOnly: vi.fn(async () => ({ visits_moved: 0 })) });
    const res = await executeAddVisitPlan(
      plan([
        item({
          scope: 'week',
          sourceVisit: visitLite({ visit_date: '2026-09-15', start_time: '09:30' }),
        }),
      ]),
      deps,
      { opGroupId: OP_GROUP },
    );
    expect(res.done).toHaveLength(0);
    expect(res.failed?.kind).toBe('error');
    expect(res.failed?.message).toBe(MOVE_SOURCE_MISSING_MESSAGE);
  });

  it('M4: 移動先コースが決まらなければ送らない (コース据え置きを作らない)', async () => {
    const deps = makeDeps();
    const res = await executeAddVisitPlan(
      plan([
        item({
          scope: 'week',
          courseTemplateId: null,
          sourceVisit: visitLite({ visit_date: '2026-09-15' }),
        }),
      ]),
      deps,
      { opGroupId: OP_GROUP },
    );
    expect(deps.moveWeekOnly).not.toHaveBeenCalled();
    expect(res.failed?.message).toBe(MOVE_COURSE_UNRESOLVED_MESSAGE);
  });

  it('L1: 動かす元が対象週の外なら送らない', async () => {
    const deps = makeDeps();
    const res = await executeAddVisitPlan(
      // 2026-09-08 は 1 週前 (W37)。item は W38。
      plan([
        item({
          scope: 'week',
          sourceVisit: visitLite({ visit_date: '2026-09-08', start_time: '09:30' }),
        }),
      ]),
      deps,
      { opGroupId: OP_GROUP },
    );
    expect(deps.moveWeekOnly).not.toHaveBeenCalled();
    expect(res.failed?.message).toBe(MOVE_SOURCE_WRONG_WEEK_MESSAGE);
  });

  it('元が無い週は新規追加 (place-and-fix) に落ちる', async () => {
    const deps = makeDeps();
    const res = await executeAddVisitPlan(
      plan([item({ scope: 'week', sourceVisit: null })]),
      deps,
      {
        opGroupId: OP_GROUP,
      },
    );
    expect(deps.moveWeekOnly).not.toHaveBeenCalled();
    expect(deps.placeAndFix).toHaveBeenCalledTimes(1);
    expect(res.done[0]?.kind).toBe('new');
  });
});

describe("executeAddVisitPlan — scope 'pattern'", () => {
  it('既存行を読んで同曜日 slot 0 を置換し pattern_and_week で PUT する', async () => {
    const deps = makeDeps({
      getFixedVisits: vi.fn(async () => [
        {
          id: 'pfv-1',
          patient_id: PATIENT,
          mode: 'normal' as const,
          created_at: '',
          updated_at: '',
          weekday: 0,
          start_time: '09:30:00',
          duration_min: 35,
          course_template_id: TPL_B,
          sub_office_id: null,
          slot_index: 0 as const,
          is_pinned: true,
          movability: 'locked' as const,
        },
        {
          id: 'pfv-2',
          patient_id: PATIENT,
          mode: 'normal' as const,
          created_at: '',
          updated_at: '',
          weekday: 3,
          start_time: '14:00:00',
          duration_min: 60,
          course_template_id: TPL_B,
          sub_office_id: null,
          slot_index: 0 as const,
          is_pinned: false,
          movability: 'unknown' as const,
        },
      ]),
    });
    await executeAddVisitPlan(plan([item({ scope: 'pattern' })]), deps, { opGroupId: OP_GROUP });
    const [patientId, body] = (
      deps.putFixedVisits as unknown as { mock: { calls: [string, Record<string, unknown>][] } }
    ).mock.calls[0];
    expect(patientId).toBe(PATIENT);
    expect(body.mode).toBe('normal');
    expect(body.change_scope).toBe('pattern_and_week');
    expect(body.iso_year).toBe(2026);
    expect(body.iso_week).toBe(38);
    // 月曜 (置換) + 木曜 (据え置き) の 2 行。
    expect(body.items).toEqual([
      {
        weekday: 0,
        start_time: '12:00',
        duration_min: 35,
        course_template_id: TPL_A,
        sub_office_id: null,
        slot_index: 0,
        // M2: 元の枠が赤ピン (locked) なら引き継ぐ。
        is_pinned: true,
        movability: 'locked',
      },
      {
        weekday: 3,
        start_time: '14:00',
        duration_min: 60,
        course_template_id: TPL_B,
        sub_office_id: null,
        slot_index: 0,
        is_pinned: false,
        movability: 'unknown',
      },
    ]);
  });

  it('他拠点 (要確認) を選んだときだけ sub_office_id を刻む', async () => {
    const deps = makeDeps();
    await executeAddVisitPlan(
      plan([item({ scope: 'pattern', isOtherOffice: true, officeId: 'office-tsuga' })]),
      deps,
      { opGroupId: OP_GROUP },
    );
    const body = (
      deps.putFixedVisits as unknown as { mock: { calls: [string, Record<string, unknown>][] } }
    ).mock.calls[0][1];
    expect((body.items as Record<string, unknown>[])[0].sub_office_id).toBe('office-tsuga');
  });
});

describe('executeAddVisitPlan — 制約確認 422', () => {
  it("kind='constraint' と index を返す（acknowledge 再送の起点）", async () => {
    const deps = makeDeps({
      placeAndFix: vi.fn(async () => {
        throw constraint422();
      }) as unknown as AddVisitExecDeps['placeAndFix'],
    });
    const res = await executeAddVisitPlan(
      plan([item({ date: '2026-09-14' }), item({ date: '2026-09-15' })]),
      deps,
      { opGroupId: OP_GROUP },
    );
    expect(res.failed?.kind).toBe('constraint');
    expect(res.failed?.index).toBe(0);
    expect(res.failed?.detail?.warnings).toHaveLength(1);
    expect(res.skipped).toHaveLength(1);
  });

  it('M1: 2 件連続の 422 は 1 件ずつ確認する（後続を黙って ack しない）', async () => {
    const calls: Record<string, unknown>[] = [];
    const deps = makeDeps({
      placeAndFix: vi.fn(async (req: Record<string, unknown>) => {
        calls.push(req);
        if (!req.acknowledge_constraint_warnings) throw constraint422();
        return {
          visit: { id: 'v' },
          fixed_visit: null,
          visits: [{ id: 'v' }],
          fixed_visits: [],
          visit_group_id: null,
        };
      }) as unknown as AddVisitExecDeps['placeAndFix'],
    });
    const p = plan([item({ date: '2026-09-14' }), item({ date: '2026-09-15' })]);

    // 1 件目で 422。
    const r1 = await executeAddVisitPlan(p, deps, { opGroupId: OP_GROUP });
    expect(r1.failed?.index).toBe(0);

    // 1 件目だけ ack して再開 → 1 件目は通り、2 件目はまた 422 で止まる。
    const r2 = await executeAddVisitPlan(p, deps, {
      opGroupId: OP_GROUP,
      startIndex: 0,
      acknowledgeIndex: 0,
    });
    expect(r2.done).toHaveLength(1);
    expect(r2.failed?.index).toBe(1);
    expect(calls[1]?.acknowledge_constraint_warnings).toBe(true);
    expect(calls[2]?.acknowledge_constraint_warnings).toBeUndefined();

    // 2 件目を ack。
    const r3 = await executeAddVisitPlan(p, deps, {
      opGroupId: OP_GROUP,
      startIndex: 1,
      acknowledgeIndex: 1,
    });
    expect(r3.failed).toBeUndefined();
    expect(calls[3]?.acknowledge_constraint_warnings).toBe(true);
  });
});

describe('buildPatternItems', () => {
  it('該当曜日 slot が無ければ追加する', () => {
    const out = buildPatternItems([], {
      weekday: 2,
      start_time: '12:00',
      duration_min: 35,
      course_template_id: TPL_A,
      sub_office_id: null,
      slot_index: 0,
      is_pinned: false,
      movability: 'unknown',
    });
    expect(out).toHaveLength(1);
    expect(out[0]?.weekday).toBe(2);
  });

  it('M2: 置き換える枠の movability を引き継ぐ (is_pinned はそのミラー)', () => {
    const out = buildPatternItems([pfvRow({ weekday: 0, movability: 'locked', is_pinned: true })], {
      weekday: 0,
      start_time: '12:00',
      duration_min: 35,
      course_template_id: TPL_A,
      sub_office_id: null,
      slot_index: 0,
      is_pinned: false,
      movability: 'unknown',
    });
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({
      start_time: '12:00',
      movability: 'locked',
      is_pinned: true,
    });
  });

  it('M2: 元が自由枠なら自由枠のまま (勝手にピンを立てない)', () => {
    const out = buildPatternItems(
      [pfvRow({ weekday: 0, movability: 'day_flexible', is_pinned: false })],
      {
        weekday: 0,
        start_time: '12:00',
        duration_min: 35,
        course_template_id: TPL_A,
        sub_office_id: null,
        slot_index: 0,
        is_pinned: false,
        movability: 'unknown',
      },
    );
    expect(out[0]).toMatchObject({ movability: 'day_flexible', is_pinned: false });
  });
});
