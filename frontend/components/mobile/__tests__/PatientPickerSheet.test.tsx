/**
 * 患者を選ぶパネル（`PatientPickerSheet`・設計 §2-1 導線 C ②③／モック ④）。
 *
 * 守りたい約束:
 *   - 今日/今週の担当はチップで**渡された順**に出る（探さずに選べる）
 *   - 検索は氏名 / カナ / 患者コードに効く（300ms 待ってから）
 *   - 一覧はあいうえお順（`compareByKana`）＋行見出し
 *   - 非稼働（入院中等）は既定で隠し、トグルで出す（§10-3 = 紐付け自体は可）
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('@/lib/queries/patients', () => ({
  usePatients: vi.fn(() => ({ data: { items: [], truncated: false }, isLoading: false })),
}));

import { usePatients } from '@/lib/queries/patients';
import { PatientPickerSheet } from '@/components/mobile/PatientPickerSheet';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

const PATIENTS = [
  { id: 'p-1', code: 'P-0042', name: '山田 花子', kana: 'ヤマダ ハナコ', status: 'active' },
  { id: 'p-2', code: 'P-0015', name: '青木 美咲', kana: 'アオキ ミサキ', status: 'active' },
  { id: 'p-3', code: 'P-0031', name: '田中 次郎', kana: 'タナカ ジロウ', status: 'active' },
  { id: 'p-4', code: 'P-0009', name: '佐藤 幸子', kana: 'サトウ サチコ', status: 'admitted' },
];

beforeEach(() => {
  vi.clearAllMocks();
  asMock(usePatients).mockImplementation(() => ({
    data: { items: PATIENTS, truncated: false },
    isLoading: false,
    isError: false,
  }));
});

function rowIds(): string[] {
  return screen
    .getAllByTestId(/^patient-row-/)
    .map((el) => el.getAttribute('data-testid') ?? '')
    .map((t) => t.replace('patient-row-', ''));
}

describe('PatientPickerSheet', () => {
  it('チップは渡された順に出て、押すと onPick が呼ばれる', () => {
    const onPick = vi.fn();
    render(<PatientPickerSheet onPick={onPick} recentPatientIds={['p-3', 'p-1']} />);

    const chips = screen.getAllByTestId(/^patient-chip-/);
    expect(chips.map((c) => c.textContent)).toEqual(['田中 次郎', '山田 花子']);

    fireEvent.click(chips[1]!);
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 'p-1' }));
  });

  it('一覧はあいうえお順で、行見出しが付く', () => {
    render(<PatientPickerSheet onPick={vi.fn()} />);
    // 非稼働 (p-4 サトウ) は既定で出ない。
    expect(rowIds()).toEqual(['p-2', 'p-3', 'p-1']);
    expect(screen.getByText('あ')).toBeInTheDocument();
    expect(screen.getByText('た')).toBeInTheDocument();
    expect(screen.getByText('や')).toBeInTheDocument();
  });

  it('検索は氏名 / カナ / 患者コードに効く（300ms デバウンス）', async () => {
    render(<PatientPickerSheet onPick={vi.fn()} />);
    const box = screen.getByLabelText('氏名 / カナ / 患者コード で検索');

    fireEvent.change(box, { target: { value: 'やまだ' } });
    // かなは表記どおりに突合する（マスタはカタカナ）ので氏名では当たらない。
    await waitFor(() => expect(screen.queryAllByTestId(/^patient-row-/)).toHaveLength(0));

    fireEvent.change(box, { target: { value: 'ヤマダ' } });
    await waitFor(() => expect(rowIds()).toEqual(['p-1']));

    fireEvent.change(box, { target: { value: 'P-0031' } });
    await waitFor(() => expect(rowIds()).toEqual(['p-3']));

    fireEvent.change(box, { target: { value: '田中' } });
    await waitFor(() => expect(rowIds()).toEqual(['p-3']));
  });

  it('非稼働は「非稼働も表示」で出る（状態も見せる）', async () => {
    render(<PatientPickerSheet onPick={vi.fn()} />);
    expect(screen.queryByTestId('patient-row-p-4')).toBeNull();

    fireEvent.click(screen.getByLabelText('非稼働も表示'));

    expect(await screen.findByTestId('patient-row-p-4')).toHaveTextContent('入院中');
    expect(rowIds()).toEqual(['p-2', 'p-4', 'p-3', 'p-1']);
  });

  it('includeInactive を渡すと最初から非稼働も出る', () => {
    render(<PatientPickerSheet onPick={vi.fn()} includeInactive />);
    expect(screen.getByTestId('patient-row-p-4')).toBeInTheDocument();
  });

  it('保存中は選べない（二重 PATCH 防止）', () => {
    const onPick = vi.fn();
    render(<PatientPickerSheet onPick={onPick} recentPatientIds={['p-1']} disabled />);

    fireEvent.click(screen.getByTestId('patient-chip-p-1'));
    fireEvent.click(screen.getByTestId('patient-row-p-1'));
    expect(onPick).not.toHaveBeenCalled();
  });
});
