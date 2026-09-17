/**
 * 予定に無い訪問を記録 `/m/record/new`（設計 §2-1 導線 C・§11-1）のテスト。
 *
 * 守りたい導線:
 *   ① 患者を決めずに録音できる（上部は赤字「患者未確定」）
 *   ② 保存すると「この記録はどなたの訪問ですか？」へ進む
 *   ③ 患者を選ぶと**保存応答が返した録音 id** で `PATCH {patient_id}` を投げ、
 *      訪問が付けば訪問詳細へ・付かなければ今日の訪問へ戻る
 *      （「未紐付け一覧のいちばん新しい行」の推定はしない = 2 本続けて録っても
 *       取り違えない・2026-09-18 是正）
 *   ④ 「あとで紐付ける」は PATCH せずに戻る（紐付け待ちのまま残す）
 *   ⑤ ページを離れるときに録音を救出する（`rescueVoiceSessions`）
 *   ⑥ 圏外でキューに残った（`queued`）ら紐付けを促さない（サーバにまだ無い）
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import type { VoiceSavedInfo } from '@/components/mobile/VoiceRecorderPanel';

const routerPush = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
  usePathname: () => '/m/record/new',
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { staffId: 'staff-1' }, accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  }),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

// 実録音（MediaRecorder）は jsdom で動かない。保存の合図（送信済み / キュー待ち）
// だけ出せれば十分。
vi.mock('@/components/mobile/VoiceRecorderPanel', () => ({
  VoiceRecorderPanel: ({
    patientName,
    onSaved,
  }: {
    patientName: string;
    onSaved?: (saved: VoiceSavedInfo) => void;
  }) => (
    <div data-testid="voice-panel">
      <span>{patientName}</span>
      <button onClick={() => onSaved?.({ clientId: 'cid-1', recordingId: 'rec-1', queued: false })}>
        __saved__
      </button>
      <button onClick={() => onSaved?.({ clientId: 'cid-1', recordingId: null, queued: true })}>
        __saved-queued__
      </button>
    </div>
  ),
}));

// 救出（ページ離脱）はここで呼ばれたことだけ見る。
vi.mock('@/lib/voice/session', () => ({
  rescueVoiceSessions: vi.fn(async () => 0),
}));

const voiceFlushStub = {
  pendingCount: 0,
  failedCount: 0,
  flushNow: vi.fn(async () => undefined),
  refreshPending: vi.fn(async () => undefined),
};
vi.mock('@/lib/voice/queue', () => ({ useVoiceFlush: () => voiceFlushStub }));

vi.mock('@/lib/queries/me', () => ({
  useMyVisits: vi.fn(() => ({ data: [] })),
  todayIso: () => '2026-09-18',
  currentWeekStartIso: () => '2026-09-14',
}));

vi.mock('@/lib/queries/visit-recordings', () => ({
  useUpdateRecording: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

// 患者マスタ（`PatientPickerSheet` は本物を使う）。
vi.mock('@/lib/queries/patients', () => ({
  usePatients: vi.fn(() => ({ data: { items: [], truncated: false }, isLoading: false })),
}));

import { useMyVisits } from '@/lib/queries/me';
import { usePatients } from '@/lib/queries/patients';
import { useUpdateRecording } from '@/lib/queries/visit-recordings';
import { rescueVoiceSessions } from '@/lib/voice/session';
import MobileRecordNewPage from '../page';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;

const PATIENTS = [
  { id: 'p-1', code: 'P-0042', name: '山田 花子', kana: 'ヤマダ ハナコ', status: 'active' },
  { id: 'p-2', code: 'P-0015', name: '青木 美咲', kana: 'アオキ ミサキ', status: 'active' },
];

/** `mutate(payload, opts)` の `onSuccess` を即座に呼ぶスタブ。 */
function mutateWith(visitId: string | null) {
  return vi.fn(
    (_payload: unknown, opts?: { onSuccess?: (rec: { visit_id: string | null }) => void }) =>
      opts?.onSuccess?.({ visit_id: visitId }),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  asMock(useMyVisits).mockImplementation(() => ({ data: [] }));
  asMock(usePatients).mockImplementation(() => ({
    data: { items: PATIENTS, truncated: false },
    isLoading: false,
    isError: false,
  }));
  asMock(useUpdateRecording).mockImplementation(() => ({ mutate: vi.fn(), isPending: false }));
});

describe('予定に無い訪問を記録 — 録音 → 患者選択', () => {
  it('患者未確定のまま録音でき、保存すると患者選択へ進む', async () => {
    render(<MobileRecordNewPage />);
    expect(screen.getByTestId('record-new-unlinked-badge')).toHaveTextContent('患者未確定');
    expect(screen.getByTestId('voice-panel')).toHaveTextContent('（未確定）');

    fireEvent.click(screen.getByText('__saved__'));

    expect(await screen.findByText('この記録はどなたの訪問ですか？')).toBeInTheDocument();
    expect(screen.getByTestId('patient-picker')).toBeInTheDocument();
  });

  it('保存応答の録音 id で PATCH し、訪問が付けば訪問詳細へ遷移する', async () => {
    const mutate = mutateWith('visit-9');
    asMock(useUpdateRecording).mockImplementation(() => ({ mutate, isPending: false }));

    render(<MobileRecordNewPage />);
    fireEvent.click(screen.getByText('__saved__'));
    fireEvent.click(await screen.findByTestId('patient-row-p-1'));

    // 一覧から推定した id ではなく、保存が返した id を使う。
    expect(useUpdateRecording).toHaveBeenCalledWith('rec-1');
    expect(mutate).toHaveBeenCalledWith({ patient_id: 'p-1' }, expect.anything());
    await waitFor(() => expect(routerPush).toHaveBeenCalledWith('/m/today/visit-9'));
  });

  it('訪問が付かなければ今日の訪問へ戻る', async () => {
    const mutate = mutateWith(null);
    asMock(useUpdateRecording).mockImplementation(() => ({ mutate, isPending: false }));

    render(<MobileRecordNewPage />);
    fireEvent.click(screen.getByText('__saved__'));
    fireEvent.click(await screen.findByTestId('patient-row-p-1'));

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith('/m/today'));
  });

  it('今日/今週の担当患者はチップで出る（重複排除・名前順）', async () => {
    asMock(useMyVisits).mockImplementation((params: { date?: string } = {}) =>
      params.date
        ? { data: [{ patient_id: 'p-1', patient_name: '山田 花子' }] }
        : {
            data: [
              { patient_id: 'p-1', patient_name: '山田 花子' },
              { patient_id: 'p-2', patient_name: '青木 美咲' },
            ],
          },
    );

    render(<MobileRecordNewPage />);
    fireEvent.click(screen.getByText('__saved__'));

    expect(await screen.findByTestId('patient-chip-p-1')).toHaveTextContent('山田 花子');
    expect(screen.getByTestId('patient-chip-p-2')).toHaveTextContent('青木 美咲');
  });

  it('「あとで紐付ける」は PATCH せずに今日の訪問へ戻る', async () => {
    const mutate = vi.fn();
    asMock(useUpdateRecording).mockImplementation(() => ({ mutate, isPending: false }));

    render(<MobileRecordNewPage />);
    fireEvent.click(screen.getByText('__saved__'));
    fireEvent.click(await screen.findByTestId('record-new-skip'));

    expect(mutate).not.toHaveBeenCalled();
    expect(routerPush).toHaveBeenCalledWith('/m/today');
  });

  it('キュー待ち（圏外）のときは患者選択を出さない（嘘をつかない）', async () => {
    render(<MobileRecordNewPage />);
    fireEvent.click(screen.getByText('__saved-queued__'));

    expect(await screen.findByTestId('record-new-not-sent')).toBeInTheDocument();
    expect(screen.queryByTestId('patient-picker')).toBeNull();
    // 出口は「あとで紐付ける」だけ残す。
    expect(screen.getByTestId('record-new-skip')).toBeInTheDocument();
  });
});

describe('予定に無い訪問を記録 — ページ離脱の救出', () => {
  it('pagehide と unmount で録音を救出する', () => {
    const { unmount } = render(<MobileRecordNewPage />);
    expect(rescueVoiceSessions).not.toHaveBeenCalled();

    window.dispatchEvent(new Event('pagehide'));
    expect(rescueVoiceSessions).toHaveBeenCalledWith('staff-1');

    asMock(rescueVoiceSessions).mockClear();
    unmount();
    expect(rescueVoiceSessions).toHaveBeenCalledWith('staff-1');
  });
});
