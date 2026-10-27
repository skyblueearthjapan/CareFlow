/**
 * 訪問記録カード (`VisitRecordCard`) のテスト。
 *
 * 現場が見るのは**要約**なので、状態ごとに「何が見えているか」を固定する:
 *   要約済み … 見出し付き箇条書き + バイタルのグリッド + 「確認済みにする」
 *   文字起こし中 … スケルトン + らく助の一言 (要約は出さない)
 *   失敗 … 理由
 * 「確認済みにする」は PATCH `{reviewed:true}` を送る (誤りの責任の所在)。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { staffId: 'staff-1' }, accessToken: 'token' },
    status: 'authenticated',
  }),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock('@/lib/queries/visit-recordings', () => ({
  useUpdateRecording: vi.fn(),
  recordingAudioUrl: (id: string) => `/api/v1/visit-recordings/${id}/audio`,
}));

import { useUpdateRecording, type VisitRecordingRead } from '@/lib/queries/visit-recordings';
import { VisitRecordCard } from '@/components/mobile/VisitRecordCard';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;
const mutateAsync = vi.fn(async () => ({}) as VisitRecordingRead);

function makeRecording(over: Partial<VisitRecordingRead> = {}): VisitRecordingRead {
  return {
    id: 'rec-1',
    visit_id: 'visit-1',
    patient_id: 'pat-1',
    patient_name: '山田 花子',
    staff_id: 'staff-1',
    recorded_at: '2026-09-17T14:08:00.000Z',
    duration_sec: 1680,
    status: 'summarized',
    has_audio: true,
    summary: {
      '主訴・様子': ['「最近足が重い」との訴え', '顔色良好'],
      バイタル: { 血圧: '132/84 mmHg', 体温: '36.4 ℃' },
      申し送り: ['足浮腫の継続観察'],
      free: '',
    },
    transcript: '看護師: こんにちは\n患者: よろしく',
    ...over,
  } as VisitRecordingRead;
}

beforeEach(() => {
  vi.clearAllMocks();
  asMock(useUpdateRecording).mockImplementation(() => ({ mutateAsync, isPending: false }));
});

describe('VisitRecordCard', () => {
  it('要約済み: バッジ・見出し付き箇条書き・バイタルのグリッドを出す', () => {
    render(<VisitRecordCard recording={makeRecording()} />);

    expect(screen.getByText('要約済み')).toBeInTheDocument();
    expect(screen.getByText('主訴・様子')).toBeInTheDocument();
    expect(screen.getByText('「最近足が重い」との訴え')).toBeInTheDocument();
    expect(screen.getByText('血圧')).toBeInTheDocument();
    expect(screen.getByText('132/84 mmHg')).toBeInTheDocument();
    // 空の free セクションは出さない。
    expect(screen.queryByText('その他')).not.toBeInTheDocument();
  });

  // ── 要約の描き方は PC (`RecordDetailDialog`) と同じ 1 規則 (`summaryDisplayMode`) ──
  // 片方だけ規則を変えると「PC では直した要約・モバイルでは古い要約」になる。

  it('手修正あり (summary_edited_at) は summary_text を平文で出し、JSON は出さない', () => {
    render(
      <VisitRecordCard
        recording={makeRecording({
          summary_text: '手で直した要約です。',
          summary_edited_at: '2026-09-18T01:00:00.000Z',
        })}
      />,
    );

    expect(screen.getByText('手で直した要約です。')).toBeInTheDocument();
    expect(screen.getByTestId('visit-record-summary-edited')).toHaveTextContent('手修正あり');
    expect(screen.queryByText('主訴・様子')).not.toBeInTheDocument();
    expect(screen.queryByText('132/84 mmHg')).not.toBeInTheDocument();
  });

  it('手修正なしなら summary_text があっても JSON の構造表示を出す', () => {
    render(
      <VisitRecordCard
        recording={makeRecording({ summary_text: 'AI が書いた平文。', summary_edited_at: null })}
      />,
    );

    expect(screen.getByText('主訴・様子')).toBeInTheDocument();
    expect(screen.getByText('132/84 mmHg')).toBeInTheDocument();
    expect(screen.queryByText('AI が書いた平文。')).not.toBeInTheDocument();
  });

  it('JSON が無ければ summary_text を平文で出す', () => {
    render(
      <VisitRecordCard
        recording={makeRecording({ summary: null, summary_text: '本文しかありません。' })}
      />,
    );

    expect(screen.getByText('本文しかありません。')).toBeInTheDocument();
    expect(screen.queryByText('主訴・様子')).not.toBeInTheDocument();
  });

  // ── v2（設計 v2 §3 P4）: PC と同じ部品で本文・バイタル・要確認 ──

  const V2_BODY = '# 訪問記録\n\n- **血圧** 132/78 [12:05]\n- <b>タグ</b>';
  const v2 = (over: Partial<VisitRecordingRead> = {}) =>
    makeRecording({
      summary: {
        format: 'v2',
        body: V2_BODY,
        facts: {
          vitals: { 血圧: '132/78', 体温: '', 脈拍: '', SpO2: '', 血糖: '' },
          handover: [],
          next: '',
          needs_check: [{ text: '薬の量', time: '5:10' }],
        },
      },
      summary_text: V2_BODY,
      ...over,
    });

  it('v2: バイタル（語られた項目だけ）・要確認・本文を出し、タグは文字のまま', () => {
    const { container } = render(<VisitRecordCard recording={v2()} />);

    expect(screen.getByTestId('voice-v2-vitals')).toHaveTextContent('132/78');
    expect(screen.getByTestId('voice-v2-vitals')).not.toHaveTextContent('体温');
    expect(screen.getByTestId('voice-v2-needs-check')).toHaveTextContent('薬の量');
    expect(screen.getByTestId('voice-v2-body')).toHaveTextContent('<b>タグ</b>');
    expect(container.querySelector('b')).toBeNull();
    expect(screen.queryByText('主訴・様子')).not.toBeInTheDocument();
  });

  it('v2: 時刻を押すと音声を開く', async () => {
    (globalThis as unknown as { fetch: unknown }).fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      blob: async () => new Blob(['a'], { type: 'audio/webm' }),
    }));
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:m'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    render(<VisitRecordCard recording={v2()} />);

    fireEvent.click(screen.getByRole('button', { name: '12:05 ごろから聞く' }));
    expect(await screen.findByTestId('authed-audio')).toBeInTheDocument();
  });

  it('v2: 現場の追記（summary_text だけに足される）も本文に出る', () => {
    render(<VisitRecordCard recording={v2({ summary_text: `${V2_BODY}\n\n追記: 夕方に再訪` })} />);

    expect(screen.getByTestId('voice-v2-body')).toHaveTextContent('追記: 夕方に再訪');
  });

  it('v2: 音声が消えた記録では時刻を押せない', () => {
    render(<VisitRecordCard recording={v2({ has_audio: false })} />);

    expect(screen.queryByRole('button', { name: /ごろから聞く/ })).toBeNull();
    expect(screen.getByText('12:05 ごろ')).toBeInTheDocument();
  });

  it('v2 を人手で直した後は summary_text を同じ読み取りで出す', () => {
    render(
      <VisitRecordCard
        recording={v2({
          summary_text: '- 書き換えた本文 [1:00]',
          summary_edited_at: '2026-10-27T02:00:00.000Z',
        })}
      />,
    );

    expect(screen.getByText('書き換えた本文')).toBeInTheDocument();
    expect(screen.queryByTestId('voice-v2-vitals')).toBeNull();
    expect(screen.getByTestId('visit-record-summary-edited')).toBeInTheDocument();
  });

  it('文字起こし中: スケルトンとらく助の一言を出す', () => {
    render(
      <VisitRecordCard recording={makeRecording({ status: 'transcribing', summary: null })} />,
    );

    expect(screen.getByText('文字起こし中')).toBeInTheDocument();
    expect(screen.getByTestId('visit-record-working')).toBeInTheDocument();
    expect(screen.getByText('らく助が文字起こし中です')).toBeInTheDocument();
  });

  it('失敗: 理由を出す', () => {
    render(
      <VisitRecordCard
        recording={makeRecording({
          status: 'failed',
          summary: null,
          error_message: '音声が短すぎます',
        })}
      />,
    );

    expect(screen.getByText('失敗')).toBeInTheDocument();
    expect(screen.getByText('音声が短すぎます')).toBeInTheDocument();
  });

  it('要紐付け: 患者が決まっていないことを出す', () => {
    render(
      <VisitRecordCard
        recording={makeRecording({ status: 'unlinked', patient_name: null, summary: null })}
      />,
    );

    expect(screen.getByText('要紐付け')).toBeInTheDocument();
    expect(screen.getByText('(患者未紐付け)')).toBeInTheDocument();
  });

  it('「全文を見る」で文字起こしを開ける', () => {
    render(<VisitRecordCard recording={makeRecording()} />);

    expect(screen.queryByTestId('visit-record-transcript')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('全文を見る'));
    expect(screen.getByTestId('visit-record-transcript')).toHaveTextContent('看護師: こんにちは');
  });

  it('「確認済みにする」で PATCH reviewed=true を送る', async () => {
    render(<VisitRecordCard recording={makeRecording()} />);

    fireEvent.click(screen.getByText('確認済みにする'));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({ reviewed: true }));
  });

  // L-1: 「確認済み」は要約の内容に責任を持つ署名。中身が無いうちは押させない。
  it.each([
    ['文字起こし中', 'transcribing'],
    ['受付済み', 'uploaded'],
    ['失敗', 'failed'],
    ['要紐付け', 'unlinked'],
  ])('%s の記録には「確認済みにする」を出さない (L-1)', (_label, status) => {
    render(<VisitRecordCard recording={makeRecording({ status, summary: null })} />);

    expect(screen.queryByText('確認済みにする')).not.toBeInTheDocument();
  });

  it('要約済みの記録にだけ「確認済みにする」を出す (L-1)', () => {
    render(<VisitRecordCard recording={makeRecording({ status: 'summarized' })} />);

    expect(screen.getByText('確認済みにする')).toBeInTheDocument();
  });

  it('確認済みの記録はボタンではなく日時を出す', () => {
    render(<VisitRecordCard recording={makeRecording({ reviewed_at: '2026-09-17T15:00:00Z' })} />);

    expect(screen.queryByText('確認済みにする')).not.toBeInTheDocument();
    expect(screen.getByText(/確認済み（/)).toBeInTheDocument();
  });
});
