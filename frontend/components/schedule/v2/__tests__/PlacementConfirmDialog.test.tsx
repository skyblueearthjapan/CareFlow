/**
 * PlacementConfirmDialog — 「配置の確認」モーダル
 * (`docs/plans/dnd-all-views-design-2026-09-08.md` §2-2 / §4)。
 *
 * 検証:
 *   1. 異曜日のときだけ警告文言を出す (同曜日 / プールカードでは出さない)
 *   2. 既定の開始時刻の優先順 = ⭐ 前回配置 → 患者の希望開始 → 09:00 (+ 出所の注記)
 *   3. コース候補 0 件 = 注記 + 確定不可 / 1 件 = テキスト表示 / 2 件以上 = select
 *   4. 「やめる」は onConfirm を呼ばない (曜日ゲートを外した以上ここが唯一の砦)
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// TIME_OPTIONS だけが欲しい (本体は Popover / react-query を引き込むので差し替える)。
vi.mock('../cockpit/VisitActionMenu', () => ({
  TIME_OPTIONS: Array.from({ length: 44 }, (_, i) => {
    const v = 8 * 60 + i * 15;
    return `${String(Math.floor(v / 60)).padStart(2, '0')}:${String(v % 60).padStart(2, '0')}`;
  }),
}));

vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    disabled,
    ...rest
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    [k: string]: unknown;
  }) => (
    <button onClick={onClick} disabled={disabled} {...rest}>
      {children}
    </button>
  ),
}));

// Radix Portal は jsdom で扱いにくいので素朴な div に差し替える (他の盤面テストと同じ作法)。
vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ children, open }: { children: React.ReactNode; open?: boolean }) =>
    open ? <div data-testid="dialog-root">{children}</div> : null,
  DialogContent: ({ children, ...rest }: { children: React.ReactNode; [k: string]: unknown }) => (
    <div {...rest}>{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children, id }: { children: React.ReactNode; id?: string }) => (
    <p id={id}>{children}</p>
  ),
}));

import {
  PlacementConfirmDialog,
  PlacementConflictConfirmDialog,
  type PlacementConfirmDialogProps,
  type PlacementCourseOption,
} from '../PlacementConfirmDialog';

const PATIENT_ID = '22222222-2222-4222-8222-222222222222';
const MARK_ID = '33333333-3333-4333-8333-333333333333';
const TPL_A = '00000000-0000-4000-8000-0000000000aa';
const TPL_B = '00000000-0000-4000-8000-0000000000bb';

const OPT_A: PlacementCourseOption = { templateId: TPL_A, label: 'A', officeName: '稲毛' };
const OPT_B: PlacementCourseOption = { templateId: TPL_B, label: 'B', officeName: '稲毛' };

function renderDialog(over: Partial<PlacementConfirmDialogProps> = {}) {
  const onConfirm = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <PlacementConfirmDialog
      open
      onOpenChange={onOpenChange}
      subject={{
        kind: 'special',
        patientName: '中尾 要太',
        patientId: PATIENT_ID,
        markId: MARK_ID,
        ticketWeekday: 3, // 木
        serviceMinutes: 45,
        requiresMultipleStaff: false,
      }}
      target={{
        weekday: 3,
        staffName: '宇田川 優莉',
        courseOptions: [OPT_A],
        defaultTemplateId: TPL_A,
      }}
      defaultStart={{}}
      onConfirm={onConfirm}
      {...over}
    />,
  );
  return { onConfirm, onOpenChange };
}

describe('PlacementConfirmDialog — 曜日の警告', () => {
  it('チケットと同じ曜日なら警告を出さない', () => {
    renderDialog();
    expect(screen.queryByTestId('pcd-warning')).toBeNull();
    expect(screen.getByTestId('pcd-target').textContent).toContain('木曜');
  });

  it('違う曜日へ落としたときだけ「本当によろしいですか？」を出す', () => {
    renderDialog({
      target: {
        weekday: 0,
        staffName: '宇田川 優莉',
        courseOptions: [OPT_A],
        defaultTemplateId: TPL_A,
      },
    });
    const warn = screen.getByTestId('pcd-warning');
    expect(warn.textContent).toContain(
      'これは木曜日の予定ですが、月曜日に配置して本当によろしいですか？',
    );
    expect(warn.textContent).toContain('追加枠 ○ も月曜日へ移ります');
  });

  it('プールカード (曜日束縛なし) では警告を出さない', () => {
    renderDialog({
      subject: {
        kind: 'pool',
        patientName: '鈴木 花子',
        patientId: PATIENT_ID,
        serviceMinutes: 60,
        requiresMultipleStaff: false,
      },
      target: { weekday: 1, staffName: null, courseOptions: [OPT_A], defaultTemplateId: TPL_A },
    });
    expect(screen.queryByTestId('pcd-warning')).toBeNull();
    // 「（担当なし）」行はスタッフ名の代わりにその旨を出す。
    expect(screen.getByTestId('pcd-target').textContent).toContain('（担当なし）');
  });
});

describe('PlacementConfirmDialog — 既定の開始時刻', () => {
  it('⭐ の前回配置時刻が最優先 (秒つきでも HH:MM に丸める)', () => {
    renderDialog({ defaultStart: { lastPlacement: '14:00:00', preferred: '10:30' } });
    expect((screen.getByTestId('pcd-time-select') as HTMLSelectElement).value).toBe('14:00');
    expect(screen.getByTestId('pcd-time-source').textContent).toContain('前回の配置時刻');
  });

  it('前回配置が無ければ患者の希望開始', () => {
    renderDialog({ defaultStart: { lastPlacement: null, preferred: '10:30' } });
    expect((screen.getByTestId('pcd-time-select') as HTMLSelectElement).value).toBe('10:30');
    expect(screen.getByTestId('pcd-time-source').textContent).toContain('患者の希望開始');
  });

  it('どちらも無ければ 09:00', () => {
    renderDialog({ defaultStart: {} });
    expect((screen.getByTestId('pcd-time-select') as HTMLSelectElement).value).toBe('09:00');
    expect(screen.getByTestId('pcd-time-source').textContent).toContain('既定');
  });

  // 枠外の希望をそのまま既定にすると、そのまま「配置する」を押されて弾かれる。
  it('9:00 より早い希望開始は 09:00 に寄せる (08:30 → 09:00)', () => {
    renderDialog({ defaultStart: { preferred: '08:30' } });
    expect((screen.getByTestId('pcd-time-select') as HTMLSelectElement).value).toBe('09:00');
    const note = screen.getByTestId('pcd-time-source').textContent ?? '';
    expect(note).toContain('患者の希望開始');
    expect(note).toContain('9:00〜18:00 に収まる 09:00 に寄せました');
  });

  it('所要ぶんが 18:00 を超える希望開始は収まる最終枠へ寄せる (60分 / 17:45 → 17:00)', () => {
    renderDialog({
      subject: {
        kind: 'pool',
        patientName: '鈴木 花子',
        patientId: PATIENT_ID,
        serviceMinutes: 60,
        requiresMultipleStaff: false,
      },
      defaultStart: { preferred: '17:45' },
    });
    expect((screen.getByTestId('pcd-time-select') as HTMLSelectElement).value).toBe('17:00');
  });

  it('15 分刻みでない希望開始も最も近い候補に寄せる (09:07 → 09:00)', () => {
    renderDialog({ defaultStart: { preferred: '09:07' } });
    expect((screen.getByTestId('pcd-time-select') as HTMLSelectElement).value).toBe('09:00');
  });

  it('候補は 9:00〜18:00 (8 時台・18 時超は出さない)', () => {
    renderDialog();
    const opts = Array.from(
      (screen.getByTestId('pcd-time-select') as HTMLSelectElement).options,
    ).map((o) => o.value);
    expect(opts[0]).toBe('09:00');
    expect(opts[opts.length - 1]).toBe('18:00');
    expect(opts).not.toContain('08:45');
    expect(opts).not.toContain('18:15');
  });
});

describe('PlacementConfirmDialog — コース候補 0 / 1 / 2 件', () => {
  it('0 件: 受け皿の M すら無いことを告げて配置不可', () => {
    const { onConfirm } = renderDialog({
      target: { weekday: 3, staffName: '宇田川 優莉', courseOptions: [], defaultTemplateId: null },
    });
    expect(screen.getByTestId('pcd-course-none').textContent).toContain(
      '受け皿になるコース（M）が見つかりません',
    );
    expect(screen.getByTestId('pcd-confirm')).toBeDisabled();
    fireEvent.click(screen.getByTestId('pcd-confirm'));
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('別拠点で候補外になったときは理由を出す (M に入ることを明示)', () => {
    renderDialog({
      target: {
        weekday: 3,
        staffName: '宇田川 優莉',
        courseOptions: [{ templateId: 'tpl-M', label: 'M（担当なし枠）', officeName: '稲毛' }],
        defaultTemplateId: 'tpl-M',
        crossOfficeExcluded: true,
      },
    });
    expect(screen.getByTestId('pcd-cross-office').textContent).toContain(
      'この職員の木曜のコースは別拠点のため候補外です。担当なし枠(M)に入ります',
    );
  });

  it('拠点跨ぎでないときは理由行を出さない', () => {
    renderDialog();
    expect(screen.queryByTestId('pcd-cross-office')).toBeNull();
  });

  it('1 件: select を出さずテキストで見せ、そのまま配置できる', () => {
    const { onConfirm } = renderDialog({ defaultStart: { preferred: '11:00' } });
    expect(screen.queryByTestId('pcd-course-select')).toBeNull();
    expect(screen.getByTestId('pcd-course-text').textContent).toContain('稲毛 A');
    fireEvent.click(screen.getByTestId('pcd-confirm'));
    expect(onConfirm).toHaveBeenCalledWith({ courseTemplateId: TPL_A, startHM: '11:00' });
  });

  it('2 件以上: select で選ぶまで配置できない', () => {
    const { onConfirm } = renderDialog({
      target: {
        weekday: 3,
        staffName: '宇田川 優莉',
        courseOptions: [OPT_A, OPT_B],
        defaultTemplateId: null,
      },
    });
    const select = screen.getByTestId('pcd-course-select') as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(['', TPL_A, TPL_B]);
    expect(screen.getByTestId('pcd-confirm')).toBeDisabled();
    fireEvent.change(select, { target: { value: TPL_B } });
    fireEvent.change(screen.getByTestId('pcd-time-select'), { target: { value: '13:45' } });
    fireEvent.click(screen.getByTestId('pcd-confirm'));
    expect(onConfirm).toHaveBeenCalledWith({ courseTemplateId: TPL_B, startHM: '13:45' });
  });
});

describe('PlacementConfirmDialog — やめる', () => {
  it('「やめる」は閉じるだけで配置しない', () => {
    const { onConfirm, onOpenChange } = renderDialog();
    fireEvent.click(screen.getByTestId('pcd-cancel'));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});

describe('PlacementConflictConfirmDialog (409 の二段目)', () => {
  function renderConflict() {
    const onConfirm = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <PlacementConflictConfirmDialog
        open
        onOpenChange={onOpenChange}
        weekdayLabel="木"
        onConfirm={onConfirm}
      />,
    );
    return { onConfirm, onOpenChange };
  }

  it('「そちらを配置する」で確定する (window.confirm は使わない)', () => {
    const { onConfirm } = renderConflict();
    expect(screen.getByTestId('pcd-conflict').textContent).toContain(
      '木曜には既に追加枠（○）があります。そちらを配置しますか？',
    );
    fireEvent.click(screen.getByTestId('pcd-conflict-ok'));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('「やめる」では確定しない', () => {
    const { onConfirm, onOpenChange } = renderConflict();
    fireEvent.click(screen.getByTestId('pcd-conflict-cancel'));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
