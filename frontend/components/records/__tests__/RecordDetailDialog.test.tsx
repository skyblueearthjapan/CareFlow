/**
 * RecordDetailDialog（訪問記録の詳細・モック ⑥）の vitest。
 *
 * 縛る挙動:
 *   1. 要約 JSON を見出し付きで整形し、`summary_edited_at` があれば手修正を出す
 *   2. 「編集」→ textarea → 保存で PATCH `summary_text` が飛ぶ
 *   3. 「確認済みにする」は PATCH `reviewed: true`。要約前は押せない
 *   4. admin 限定ボタン（再処理・削除）は staff では disabled（隠さない = PO 決定）
 *   5. 文字起こしは `transcript_json` があれば話者ラベル付き、無ければ素の全文
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver ??= ResizeObserverStub;

const { mockUseVisitRecording, mockUpdate, mockRetry, mockDelete, mockSession } = vi.hoisted(
  () => ({
    mockUseVisitRecording: vi.fn(),
    mockUpdate: vi.fn(),
    mockRetry: vi.fn(),
    mockDelete: vi.fn(),
    mockSession: {
      value: {
        data: { user: { role: 'admin', staffId: 'st-1' }, accessToken: 'tok' },
        status: 'authenticated',
      } as unknown,
    },
  }),
);

vi.mock('next-auth/react', () => ({
  useSession: () => mockSession.value,
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

// 実モジュールを土台に、フックだけ差し替える（共有ファクトリ・レビュー H-1）。
vi.mock('@/lib/queries/visit-recordings', async (importOriginal) => {
  const { visitRecordingsMock } = await import('./visitRecordingsMock');
  return visitRecordingsMock(importOriginal, {
    useVisitRecording: (...a: unknown[]) => mockUseVisitRecording(...a),
    useUpdateRecording: () => ({ mutateAsync: mockUpdate, isPending: false }),
    useRetryRecording: () => ({ mutateAsync: mockRetry, isPending: false }),
    useDeleteRecording: () => ({ mutateAsync: mockDelete, isPending: false }),
  });
});

vi.mock('@/lib/queries/patients', () => ({
  usePatients: () => ({ data: { items: [] }, isLoading: false }),
}));

// 音声プレーヤーは blob を fetch するので、ここでは差し替える
// （単体は AuthedAudioPlayer 側の責務・ここでは act 警告の雑音を作らない）。
vi.mock('@/components/records/AuthedAudioPlayer', () => ({
  AuthedAudioPlayer: () => <div data-testid="records-audio-player" />,
}));

import { toast } from '@/components/ui/sonner';

import { RecordDetailDialog } from '../RecordDetailDialog';

const BASE = {
  id: 'rec-1',
  visit_id: 'v-1',
  patient_id: 'p-1',
  patient_name: '山田 花子',
  staff_id: 'st-1',
  staff_name: '川名 幸子',
  office_id: 'of-1',
  office_name: '都賀',
  recorded_at: '2026-09-17T05:08:00Z',
  duration_sec: 1694,
  status: 'summarized',
  has_audio: true,
  transcript: '山田さん、こんにちは。',
  transcript_json: null as unknown,
  summary: {
    '主訴・様子': ['「最近足が重い」とのご本人の訴え'],
    バイタル: { 血圧: '132/84 mmHg', 体温: '36.4 ℃' },
  },
  summary_text: null as string | null,
  summary_edited_at: null as string | null,
  summary_edited_by: null as string | null,
  reviewed_at: null as string | null,
  cost_usd: 0.0123,
  provider: 'vertex',
  model: 'gemini-2.5-flash',
  prompt_version: 'v1',
};

function mockRecording(over: Record<string, unknown> = {}) {
  mockUseVisitRecording.mockReturnValue({ data: { ...BASE, ...over }, isLoading: false });
}

function renderDialog() {
  return render(<RecordDetailDialog recordingId="rec-1" open onOpenChange={vi.fn()} />);
}

beforeEach(() => {
  mockUpdate.mockReset().mockResolvedValue({});
  mockRetry.mockReset().mockResolvedValue({});
  mockDelete.mockReset().mockResolvedValue(undefined);
  mockSession.value = {
    data: { user: { role: 'admin', staffId: 'st-1' }, accessToken: 'tok' },
    status: 'authenticated',
  };
  mockRecording();
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});

describe('RecordDetailDialog', () => {
  // ── 要約の描き方はモバイル (`VisitRecordCard`) と同じ 1 規則 (`summaryDisplayMode`) ──

  it('手修正あり (summary_edited_at) は summary_text を平文で出し、JSON は出さない', () => {
    mockRecording({
      summary_text: '手で直した要約です。',
      summary_edited_at: '2026-09-18T01:00:00Z',
    });
    renderDialog();
    expect(screen.getByTestId('record-summary-text')).toHaveTextContent('手で直した要約です。');
    expect(screen.queryByText('主訴・様子')).not.toBeInTheDocument();
    expect(screen.queryByText('132/84 mmHg')).not.toBeInTheDocument();
  });

  it('手修正なしなら summary_text があっても JSON の構造表示を出す', () => {
    mockRecording({ summary_text: 'AI が書いた平文。', summary_edited_at: null });
    renderDialog();
    expect(screen.getByText('主訴・様子')).toBeInTheDocument();
    expect(screen.getByText('「最近足が重い」とのご本人の訴え')).toBeInTheDocument();
    expect(screen.getByText('バイタル')).toBeInTheDocument();
    expect(screen.getByText('132/84 mmHg')).toBeInTheDocument();
    expect(screen.queryByTestId('record-summary-text')).not.toBeInTheDocument();
  });

  it('JSON が無ければ summary_text を平文で出す', () => {
    mockRecording({ summary: null, summary_text: '本文しかありません。' });
    renderDialog();
    expect(screen.getByTestId('record-summary-text')).toHaveTextContent('本文しかありません。');
    expect(screen.queryByText('主訴・様子')).not.toBeInTheDocument();
  });

  it('「編集」→ 保存で PATCH summary_text が飛ぶ（初期値は要約 JSON から組む）', async () => {
    renderDialog();
    fireEvent.click(screen.getByTestId('record-summary-edit'));
    const box = screen.getByLabelText('要約') as HTMLTextAreaElement;
    expect(box.value).toContain('【主訴・様子】');
    fireEvent.change(box, { target: { value: '手で直した要約' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith({ summary_text: '手で直した要約' }),
    );
  });

  it('summary_edited_at があれば手修正の痕跡を出し、編集者 UUID は出さない (L-1)', () => {
    mockRecording({
      summary_edited_at: '2026-09-18T01:00:00Z',
      summary_edited_by: '44444444-4444-4444-4444-444444444444',
    });
    renderDialog();
    const line = screen.getByTestId('record-summary-edited');
    expect(line).toHaveTextContent('手修正あり');
    expect(line).toHaveTextContent('2026/09/18');
    expect(line.textContent ?? '').not.toContain('4444');
  });

  it('編集モードは「確認済みが外れる」と先に言う (M-3)', () => {
    renderDialog();
    fireEvent.click(screen.getByTestId('record-summary-edit'));
    expect(screen.getByTestId('record-summary-edit-note')).toHaveTextContent(
      '保存すると「確認済み」が外れます',
    );
  });

  it('確認済みだった記録を保存したらトーストでも知らせる (M-3)', async () => {
    mockRecording({ reviewed_at: '2026-09-18T00:00:00Z' });
    renderDialog();
    fireEvent.click(screen.getByTestId('record-summary-edit'));
    fireEvent.change(screen.getByLabelText('要約'), { target: { value: '直した' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ summary_text: '直した' }));
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining('「確認済み」は外れました'),
      expect.objectContaining({ description: expect.any(String) }),
    );
  });

  it('読み込み失敗は detail 付きで出す (M-2)', () => {
    mockUseVisitRecording.mockReturnValue({
      data: null,
      isLoading: false,
      isError: true,
      error: new Error('記録が見つかりません'),
    });
    renderDialog();
    expect(screen.getByTestId('record-detail-error')).toHaveTextContent('記録が見つかりません');
  });

  it('「確認済みにする」で PATCH reviewed: true', async () => {
    renderDialog();
    fireEvent.click(screen.getByTestId('record-reviewed'));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ reviewed: true }));
  });

  it('要約前（文字起こし中）は「確認済みにする」を押させない', () => {
    mockRecording({ status: 'transcribing', summary: null });
    renderDialog();
    expect(screen.getByTestId('record-reviewed')).toBeDisabled();
  });

  it('再処理は確認を挟んでから POST retry', async () => {
    renderDialog();
    fireEvent.click(screen.getByTestId('record-retry'));
    expect(screen.getByTestId('record-confirm')).toBeInTheDocument();
    expect(mockRetry).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('record-confirm-ok'));
    await waitFor(() => expect(mockRetry).toHaveBeenCalled());
  });

  it('削除は確認を挟んでから DELETE', async () => {
    renderDialog();
    fireEvent.click(screen.getByTestId('record-delete'));
    expect(screen.getByTestId('record-confirm')).toHaveTextContent('削除します');
    fireEvent.click(screen.getByTestId('record-confirm-ok'));
    await waitFor(() => expect(mockDelete).toHaveBeenCalled());
  });

  it('staff（本人）は要約を直せるが、再処理・削除は disabled（隠さない）', () => {
    mockSession.value = {
      data: { user: { role: 'staff', staffId: 'st-1' }, accessToken: 'tok' },
      status: 'authenticated',
    };
    renderDialog();
    expect(screen.getByTestId('record-summary-edit')).not.toBeDisabled();
    expect(screen.getByTestId('record-relink')).not.toBeDisabled();
    expect(screen.getByTestId('record-retry')).toBeDisabled();
    expect(screen.getByTestId('record-delete')).toBeDisabled();
  });

  it('staff（他人の記録）は要約編集・紐付け変更も disabled', () => {
    mockSession.value = {
      data: { user: { role: 'staff', staffId: 'st-other' }, accessToken: 'tok' },
      status: 'authenticated',
    };
    renderDialog();
    expect(screen.getByTestId('record-summary-edit')).toBeDisabled();
    expect(screen.getByTestId('record-relink')).toBeDisabled();
  });

  it('「紐付けを変更」で患者コンボボックス行が出る（未選択では確定できない）', () => {
    renderDialog();
    fireEvent.click(screen.getByTestId('record-relink'));
    expect(screen.getByTestId('record-relink-row')).toBeInTheDocument();
    // 患者リストは空モックなので、変更ボタンは選ぶまで押せない。
    expect(screen.getByRole('button', { name: '変更する' })).toBeDisabled();
  });

  it('transcript_json があれば話者ラベル付き、無ければ素の全文', () => {
    renderDialog();
    expect(screen.getByTestId('record-transcript-plain')).toHaveTextContent(
      '山田さん、こんにちは。',
    );

    mockRecording({
      transcript_json: [
        { speaker: '川名 看護師', start: 0, text: 'こんにちは。' },
        { speaker: '山田 花子', start: 6, text: '足が重くてね。' },
      ],
    });
    renderDialog();
    const segs = screen.getByTestId('record-transcript-segments');
    expect(segs).toHaveTextContent('川名 看護師');
    expect(segs).toHaveTextContent('00:06');
    expect(segs).toHaveTextContent('足が重くてね。');
  });
});
