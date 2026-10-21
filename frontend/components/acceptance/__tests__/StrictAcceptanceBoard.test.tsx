/**
 * StrictAcceptanceBoard — 受け入れ枠 (PC) の画面 (設計書 §5-1・§7-4)。
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { makePayload } from '@/lib/__tests__/fixtures/acceptanceStrictPayload';

import { StrictAcceptanceBoard } from '../StrictAcceptanceBoard';

function cell(label: RegExp) {
  return screen.getByRole('button', { name: label });
}

describe('StrictAcceptanceBoard', () => {
  beforeEach(() => window.localStorage.clear());

  it('チップを押すと結果が変わる (問い合わせ直さない)', () => {
    render(<StrictAcceptanceBoard data={makePayload()} />);
    expect(cell(/^火曜 15時台 × /)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /管理M/ }));
    expect(cell(/^火曜 15時台 ○ /)).toBeInTheDocument();
  });

  it('まとめボタンと休みの切り替え', () => {
    render(<StrictAcceptanceBoard data={makePayload()} />);
    fireEvent.click(screen.getByRole('button', { name: '全員' }));
    expect(screen.getByRole('button', { name: /新人T/ })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: '職員だけ' }));
    expect(screen.getByRole('button', { name: /管理M/ })).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(screen.getByRole('button', { name: '無いとする' }));
    expect(cell(/^月曜 11時台 ○ /)).toBeInTheDocument();
  });

  it('週数を切り替えると決まりの文章が変わる・祝日で減った曜日に印', () => {
    render(<StrictAcceptanceBoard data={makePayload()} />);
    expect(screen.getByText(/4 週のうち 3 週で入れられる人がいました/)).toBeInTheDocument();
    expect(screen.getByText('（3 週で判定）')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '6 週' }));
    expect(screen.getByText(/6 週のうち 4〜5 週で入れられる人がいました/)).toBeInTheDocument();
    expect(screen.getByText('（5 週で判定）')).toBeInTheDocument();
  });

  it('毎週の上書きに「手」の印・食い違いは別の印・押すと自動の判定を並べる', () => {
    render(<StrictAcceptanceBoard data={makePayload()} />);
    expect(screen.getAllByTestId('manual')).toHaveLength(1);
    expect(screen.getAllByTestId('manual-conflict')).toHaveLength(1);
    fireEvent.click(cell(/^月曜 14時台 ○ .*手で上書き/));
    const panel = screen.getByLabelText('マスの内訳');
    expect(
      within(panel).getByText(/自動の判定は × 0\/3 週。手で ○ にしています/),
    ).toBeInTheDocument();
    expect(within(panel).getByText(/月曜の午後は受ける/)).toBeInTheDocument();
  });

  it('マスの内訳: 週ごと・祝日・休みの理由', () => {
    render(<StrictAcceptanceBoard data={makePayload()} />);
    fireEvent.click(cell(/^月曜 9時台/));
    const panel = screen.getByLabelText('マスの内訳');
    expect(within(panel).getByText('祝日（スポーツの日）のため数えていません')).toBeInTheDocument();
    expect(within(panel).getAllByText('職員B：有給（らく助）').length).toBeGreaterThan(0);
    expect(within(panel).getByText('実績の入力待ちのため予定で見ています')).toBeInTheDocument();
  });

  it('管理者の上書きと取り直し', () => {
    const set = vi.fn();
    const clear = vi.fn();
    const start = vi.fn();
    render(
      <StrictAcceptanceBoard
        data={makePayload()}
        edit={{ busy: false, set, clear }}
        refresh={{
          job: {
            id: 'j',
            status: 'running',
            done: 3,
            total: 6,
            current: { kind: 'actual_csv', period_start: '2026-10-01' },
            failed: [],
          },
          starting: false,
          start,
        }}
      />,
    );
    expect(screen.getByText('3 / 6 本・10 月の実績を取っています')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'カイポケから取り直す' })).toBeDisabled();
    fireEvent.click(cell(/^火曜 10時台/));
    fireEvent.click(screen.getByRole('button', { name: '手で △ にする' }));
    expect(set).toHaveBeenCalledWith({ weekday: 1, hour: 10, status: 'consult', note: null });
    fireEvent.click(cell(/^月曜 9時台/));
    fireEvent.click(screen.getByRole('button', { name: '手の上書きを外す' }));
    expect(clear).toHaveBeenCalledWith({ weekday: 0, hour: 9 });
  });

  // 「取り直す」(カイポケから取り直す) は設計の文言なので除く。
  it('材料の表示と文言 (「直す」「修正」を使わない)', () => {
    const { container } = render(
      <StrictAcceptanceBoard
        data={makePayload()}
        refresh={{ job: makePayload().sources.last_job, starting: false, start: vi.fn() }}
      />,
    );
    expect(screen.getByText(/カイポケから取った日時: 10\/20 9:12/)).toBeInTheDocument();
    expect(
      screen.getByText('取り直しの間（最大 15 分）は、カイポケへの送信・取り込みが待たされます'),
    ).toBeInTheDocument();
    expect(screen.getByText(/第 41 週の個別業務が取れませんでした/)).toBeInTheDocument();
    expect(screen.getByText(/らく助に無い職員名 2 件/)).toBeInTheDocument();
    expect(screen.getByText('時間の無い予定 3 件（メモなど）は見ていません')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/(?<!取り)直す|修正/);
  });

  it('「全て」: 職員のチップを拠点ごとにまとめ、説明に一言足す', () => {
    const base = makePayload();
    const data = {
      ...base,
      office_id: null,
      offices: [
        { id: 'o1', name: '拠点イ' },
        { id: 'o2', name: '拠点ロ' },
      ],
      staff: base.staff.map((s) => ({ ...s, office_id: s.id === 'b' ? 'o2' : 'o1' })),
    };
    render(<StrictAcceptanceBoard data={data} />);
    const first = screen.getByRole('heading', { name: '拠点イ' }).parentElement!;
    const second = screen.getByRole('heading', { name: '拠点ロ' }).parentElement!;
    expect(within(first).getByRole('button', { name: /職員A/ })).toBeInTheDocument();
    expect(within(first).queryByRole('button', { name: /職員B/ })).toBeNull();
    expect(within(second).getByRole('button', { name: /職員B/ })).toBeInTheDocument();
    expect(screen.getByText(/全拠点の職員のうち、誰か 1 人でも入れれば/)).toBeInTheDocument();
  });

  it('拠点を指定したときは拠点の見出しを出さない', () => {
    render(<StrictAcceptanceBoard data={makePayload()} />);
    expect(screen.queryByText(/全拠点の職員のうち/)).toBeNull();
    expect(screen.queryByRole('heading', { level: 4 })).toBeNull();
  });

  it('「全て」では毎週の上書きを操作させず、拠点を選ぶよう案内する', () => {
    const edit = { busy: false, set: vi.fn(), clear: vi.fn() };
    render(<StrictAcceptanceBoard data={{ ...makePayload(), office_id: null }} edit={edit} />);
    fireEvent.click(cell(/^月曜 14時台/));
    const panel = screen.getByLabelText('マスの内訳');
    expect(within(panel).getByText(/拠点を選ぶと調整できます/)).toBeInTheDocument();
    expect(within(panel).queryByRole('button', { name: /手で/ })).toBeNull();
    expect(within(panel).queryByRole('button', { name: '手の上書きを外す' })).toBeNull();
    expect(within(panel).queryByLabelText('上書きの理由')).toBeNull();
  });

  it('拠点を選んでいるときは毎週の上書きを操作できる', () => {
    const edit = { busy: false, set: vi.fn(), clear: vi.fn() };
    render(<StrictAcceptanceBoard data={makePayload()} edit={edit} />);
    fireEvent.click(cell(/^月曜 14時台/));
    const panel = screen.getByLabelText('マスの内訳');
    expect(within(panel).queryByText(/拠点を選ぶと調整できます/)).toBeNull();
    fireEvent.click(within(panel).getByRole('button', { name: '手で × にする' }));
    expect(edit.set).toHaveBeenCalledWith(expect.objectContaining({ weekday: 0, hour: 14 }));
  });

  it('表の向き: 既定は縦 (行 = 時刻・列 = 曜日)', () => {
    render(<StrictAcceptanceBoard data={makePayload()} />);
    const grid = screen.getByTestId('grid-vertical');
    expect(screen.queryByTestId('grid-horizontal')).toBeNull();
    const cols = within(grid)
      .getAllByRole('columnheader')
      .map((th) => th.textContent);
    expect(cols.slice(1).map((t) => t?.slice(0, 1))).toEqual(['月', '火']);
    expect(within(grid).getAllByRole('rowheader')[0]).toHaveTextContent('9時台');
    expect(screen.getByRole('button', { name: '縦（時刻×曜日）' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('表の向き: 切り替えると横になり、選んだ向きを残す', () => {
    const { unmount } = render(<StrictAcceptanceBoard data={makePayload()} />);
    fireEvent.click(screen.getByRole('button', { name: '横（曜日×時刻）' }));
    const grid = screen.getByTestId('grid-horizontal');
    expect(screen.queryByTestId('grid-vertical')).toBeNull();
    expect(within(grid).getAllByRole('rowheader')[0]).toHaveTextContent('月');
    expect(within(grid).getAllByRole('columnheader')[1]).toHaveTextContent('9時台');
    expect(window.localStorage.getItem('acceptance.strict.orientation')).toBe('horizontal');
    unmount();
    render(<StrictAcceptanceBoard data={makePayload()} />);
    expect(screen.getByTestId('grid-horizontal')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '縦（時刻×曜日）' }));
    expect(screen.getByTestId('grid-vertical')).toBeInTheDocument();
    expect(window.localStorage.getItem('acceptance.strict.orientation')).toBe('vertical');
  });

  it.each(['縦（時刻×曜日）', '横（曜日×時刻）'])(
    'マスを押したときの動きはどちらの向きでも同じ (%s)',
    (label) => {
      const edit = { busy: false, set: vi.fn(), clear: vi.fn() };
      render(<StrictAcceptanceBoard data={makePayload()} edit={edit} />);
      fireEvent.click(screen.getByRole('button', { name: label }));
      expect(screen.getAllByTestId('manual')).toHaveLength(1);
      expect(screen.getAllByTestId('manual-conflict')).toHaveLength(1);
      const target = cell(/^月曜 14時台 ○ .*手で上書き/);
      fireEvent.click(target);
      expect(target).toHaveAttribute('aria-pressed', 'true');
      const panel = screen.getByLabelText('マスの内訳');
      expect(within(panel).getByRole('heading', { name: '月曜 14時台' })).toBeInTheDocument();
      expect(
        within(panel).getByText(/自動の判定は × 0\/3 週。手で ○ にしています/),
      ).toBeInTheDocument();
      fireEvent.click(within(panel).getByRole('button', { name: '手の上書きを外す' }));
      expect(edit.clear).toHaveBeenCalledWith({ weekday: 0, hour: 14 });
    },
  );
});
