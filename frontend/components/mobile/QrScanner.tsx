'use client';

/**
 * QR スキャナ (Phase 2) — html5-qrcode をラップしたモバイル用カメラ読取。
 *
 * 設計上の注意:
 *   - **HTTPS 必須**: `getUserMedia` (カメラ) は secure context でしか動かない。
 *     本番は Cloudflared 経由の HTTPS なので可。ローカルは https/localhost のみ。
 *   - **iOS Safari はユーザー操作起点が必要**: カメラ起動はタップ等のジェスチャ
 *     直後に呼ぶ必要がある。本コンポーネントは「QRで到着/退出」ボタン押下で
 *     マウントされる前提なので、その起点を満たす。
 *   - **テスト容易性**: 実カメラに依存する html5-qrcode は `import()` で遅延
 *     ロードし、jsdom 環境ではこのコンポーネント自体をモックして差し替える
 *     (page テストは `vi.mock('@/components/mobile/QrScanner')`)。トークン抽出は
 *     純粋関数 `extractQrToken` に分離してユニットテスト可能にしている。
 *
 * QR の中身は `https://<app>/q/{token}` 形式。`extractQrToken` で token 部分を
 * 取り出し `onScan(token)` に渡す。カメラ不可/権限拒否時は「QRなしで記録」
 * (onManual) で qr_token 無しの manual チェックインにフォールバックする。
 */

import { useEffect, useRef, useState } from 'react';
import { X, Keyboard } from 'lucide-react';

import { extractQrToken } from '@/lib/qr-token';

export interface QrScannerProps {
  /** 読取成功時 (URL から抽出した token を渡す)。 */
  onScan: (token: string) => void;
  /** 「閉じる」押下時。 */
  onCancel: () => void;
  /** 「QRなしで記録」押下時 (カメラ不可フォールバック → manual checkin)。 */
  onManual: () => void;
  /** スキャン対象の説明 (例: "山田 花子 さん宅 / 到着")。 */
  targetLabel?: string;
}

const SCANNER_ELEMENT_ID = 'qr-scanner-region';

export function QrScanner({ onScan, onCancel, onManual, targetLabel }: QrScannerProps) {
  const regionRef = useRef<HTMLDivElement | null>(null);
  // 多重発火防止: 最初の有効読取 / 手入力だけを採用する。
  const handledRef = useRef(false);
  const [cameraError, setCameraError] = useState<string | null>(null);
  // 「QRなしで記録」の二重タップ防止 (押下後すぐ disabled にする)。
  const [manualTapped, setManualTapped] = useState(false);

  function handleManualClick() {
    if (handledRef.current) return;
    handledRef.current = true;
    setManualTapped(true);
    onManual();
  }

  useEffect(() => {
    let cancelled = false;
    // html5-qrcode のインスタンス (型は実体ロード後にのみ判明するため unknown 経由)。
    let scanner: { stop: () => Promise<void>; clear: () => void } | null = null;

    async function start() {
      try {
        const mod = await import('html5-qrcode');
        if (cancelled) return;
        const instance = new mod.Html5Qrcode(SCANNER_ELEMENT_ID, /* verbose */ false);
        scanner = instance as unknown as typeof scanner;
        await instance.start(
          { facingMode: 'environment' },
          { fps: 10, qrbox: { width: 212, height: 212 } },
          (decodedText: string) => {
            if (handledRef.current) return;
            const token = extractQrToken(decodedText);
            if (!token) return; // 別サイト QR 等は無視してスキャンを続ける。
            handledRef.current = true;
            // 読取後すぐカメラを止めてからコールバック。
            void instance.stop().catch(() => undefined);
            onScan(token);
          },
          // フレーム毎のデコード失敗は正常 (無視)。
          () => undefined,
        );
      } catch (err) {
        if (cancelled) return;
        // 権限拒否 / カメラ無し / secure context 外。手入力フォールバックを促す。
        setCameraError(err instanceof Error ? err.message : 'カメラを起動できませんでした');
      }
    }

    void start();

    return () => {
      cancelled = true;
      if (scanner) {
        // stop() は走行中のみ成功する。停止済みでも例外を握りつぶす。
        void scanner.stop().catch(() => undefined);
      }
    };
    // onScan は呼び出し側で安定参照を渡す前提 (再マウントで再スキャン)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-stone-950 text-white">
      <div className="flex items-center justify-between px-4 pt-[max(env(safe-area-inset-top),1.5rem)] pb-2 text-sm">
        <button type="button" onClick={onCancel} className="inline-flex items-center gap-1">
          <X className="h-4 w-4" /> 閉じる
        </button>
        {targetLabel && <span className="text-stone-300">{targetLabel}</span>}
      </div>

      <div className="flex flex-1 flex-col items-center justify-center px-6">
        {cameraError ? (
          <div className="space-y-3 text-center">
            <p className="text-sm text-stone-200">カメラを起動できませんでした</p>
            <p className="text-xs text-stone-400">{cameraError}</p>
            <p className="text-xs text-stone-400">
              カメラの権限を許可するか、下の「手入力で選ぶ」で記録できます。
            </p>
          </div>
        ) : (
          <>
            {/* html5-qrcode が映像とスキャン枠をこの要素に描画する。 */}
            <div
              id={SCANNER_ELEMENT_ID}
              ref={regionRef}
              className="aspect-square w-[212px] overflow-hidden rounded-xl"
            />
            <p className="mt-4 px-6 text-center text-xs leading-relaxed text-stone-400">
              患者宅のQRコードを枠内に合わせてください。
            </p>
          </>
        )}
      </div>

      <div className="px-6 pb-[max(env(safe-area-inset-bottom),1.5rem)]">
        <button
          type="button"
          onClick={handleManualClick}
          disabled={manualTapped}
          className="mx-auto flex items-center justify-center gap-2 rounded-full bg-white/15 px-6 py-3 text-sm font-semibold text-white disabled:opacity-60"
        >
          <Keyboard className="h-4 w-4" /> QRなしで記録
        </button>
      </div>
    </div>
  );
}
