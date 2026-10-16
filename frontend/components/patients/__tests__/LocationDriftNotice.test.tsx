/**
 * 登録の位置のずれのお知らせ (location-drift-align-2026-10-17 §7 FE)。
 *
 * - ずれが無ければ何も出さない
 * - 押すと確認が出て、[合わせる] で API を呼ぶ・toast に [元に戻す]
 * - [元に戻す] で revert を呼ぶ
 * - [地図で見比べる] は登録の位置 → 提案の位置の経路
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const alignMutate = vi.fn();
const revertMutate = vi.fn();
vi.mock('@/lib/queries/patientLocation', () => ({
  useAlignPatientLocation: () => ({ mutateAsync: alignMutate, isPending: false }),
  useRevertPatientLocation: () => ({ mutateAsync: revertMutate, isPending: false }),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { toast } from '@/components/ui/sonner';
import { googleMapsCompareUrl } from '@/lib/maps';

import { LocationDriftNotice, formatDriftDistance } from '../LocationDriftNotice';

const DRIFT = { distance_m: 441.2, suggested_lat: 35.604, suggested_lng: 140.1, scans: 6, days: 3 };
const REGISTERED = { lat: 35.6, lng: 140.1 };

describe('LocationDriftNotice', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    alignMutate.mockResolvedValue({ lat: 35.604, lng: 140.1 });
    revertMutate.mockResolvedValue({ lat: 35.6, lng: 140.1 });
  });

  it('ずれが無ければ何も出さない', () => {
    const { container } = render(
      <LocationDriftNotice patientId="p-1" drift={null} registered={REGISTERED} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('距離・回数・日数を出す', () => {
    render(<LocationDriftNotice patientId="p-1" drift={DRIFT} registered={REGISTERED} />);
    expect(screen.getByTestId('location-drift-notice')).toHaveTextContent(
      '登録の位置が、実際に QR を読んだ場所と約 440m 離れています（打刻 6 回・3 日分）',
    );
  });

  it('登録座標が無い患者は距離なしの文にする', () => {
    render(
      <LocationDriftNotice
        patientId="p-1"
        drift={{ ...DRIFT, distance_m: null }}
        registered={null}
      />,
    );
    expect(screen.getByTestId('location-drift-notice')).toHaveTextContent(
      '登録の位置がまだありません',
    );
  });

  it('押すと確認が出て、[合わせる] で API を呼び、toast に [元に戻す] を付ける', async () => {
    const user = userEvent.setup();
    render(<LocationDriftNotice patientId="p-1" drift={DRIFT} registered={REGISTERED} />);

    await user.click(screen.getByTestId('location-drift-align'));
    const dialog = await screen.findByTestId('location-drift-confirm');
    expect(dialog).toHaveTextContent(
      '次の打刻から、この位置で判定します。過去の打刻の判定は変わりません。',
    );
    expect(alignMutate).not.toHaveBeenCalled();

    await user.click(screen.getByTestId('location-drift-confirm-align'));
    await waitFor(() => expect(alignMutate).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        '登録の位置を合わせました',
        expect.objectContaining({
          action: expect.objectContaining({ label: '元に戻す' }),
        }),
      ),
    );
    await waitFor(() =>
      expect(screen.queryByTestId('location-drift-confirm')).not.toBeInTheDocument(),
    );

    // [元に戻す] で revert を呼ぶ。
    const options = vi.mocked(toast.success).mock.calls[0]![1] as {
      action: { onClick: () => void };
    };
    options.action.onClick();
    await waitFor(() => expect(revertMutate).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('登録の位置を元に戻しました'));
  });

  it('合わせられなかったときは error の toast', async () => {
    alignMutate.mockRejectedValue(new Error('登録の位置のずれは見つかりませんでした'));
    const user = userEvent.setup();
    render(<LocationDriftNotice patientId="p-1" drift={DRIFT} registered={REGISTERED} />);

    await user.click(screen.getByTestId('location-drift-align'));
    await user.click(await screen.findByTestId('location-drift-confirm-align'));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        '合わせられませんでした: 登録の位置のずれは見つかりませんでした',
      ),
    );
  });

  it('[地図で見比べる] は登録の位置から提案の位置への経路を新しいタブで開く', () => {
    render(<LocationDriftNotice patientId="p-1" drift={DRIFT} registered={REGISTERED} />);
    const link = screen.getByTestId('location-drift-compare');
    expect(link).toHaveAttribute(
      'href',
      'https://www.google.com/maps/dir/?api=1&origin=35.6%2C140.1&destination=35.604%2C140.1&travelmode=driving&hl=ja',
    );
    expect(link).toHaveAttribute('target', '_blank');
  });
});

describe('googleMapsCompareUrl / formatDriftDistance', () => {
  it('登録座標が無いときは提案の位置の地図を開く', () => {
    expect(googleMapsCompareUrl(null, { lat: 35.604, lng: 140.1 })).toBe(
      'https://www.google.com/maps/search/?api=1&query=35.604%2C140.1&hl=ja',
    );
  });

  it('1km 未満は 10m 単位、それ以上は 0.1km 単位', () => {
    expect(formatDriftDistance(124)).toBe('120m');
    expect(formatDriftDistance(835)).toBe('840m');
    expect(formatDriftDistance(1249)).toBe('1.2km');
  });
});
