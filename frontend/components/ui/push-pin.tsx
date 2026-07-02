import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * PushPin / PushPinOff — 固定枠の保護 (is_pinned) を表す共有「ピン留め」アイコン.
 *
 * CareFlow #P4-B: 従来スケジュール表で使っていた 🔒 Lock / 🔓 Unlock (鍵) を
 * 全 UI で「ピン留め」(プッシュピン / 画鋲) に統一するための共有コンポーネント.
 *
 * デザイン:
 *   - PushPin (ピン留め中): **赤い丸頭** (fill は error トークン = var(--error)) +
 *     currentColor の針. 「赤い丸ぽっち」で一目でピン留めと判別できることを優先.
 *   - PushPinOff (未ピン): 灰色アウトラインの丸頭 (塗りなし) + currentColor の針.
 *
 * 実装は lucide に依存しない小さな custom SVG (viewBox 24). サイズは className 透過
 * (呼び出し側が h-4 w-4 等を渡す). 針は currentColor なので呼び出し側の text-* で色付けできる.
 */
export type PushPinIconProps = React.SVGProps<SVGSVGElement>;

/** ピン留め中: 赤い丸頭 (fill-error) + currentColor の針. */
export function PushPin({ className, ...props }: PushPinIconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      data-icon="push-pin"
      className={cn('h-4 w-4', className)}
      {...props}
    >
      {/* 針 (斜め): currentColor で描く. */}
      <line
        x1="12"
        y1="12.5"
        x2="7.5"
        y2="21"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
      {/* 赤い丸頭: fill は error トークン (var(--error)).
          strokeWidth は PushPinOff (1.5) より意図的に細い 1.25 — 塗り (fill-error) が
          主張するため輪郭を控えめにして小サイズでの視認性を保つ (レビューNIT対応の注記). */}
      <circle
        cx="13"
        cy="8"
        r="5.5"
        className="fill-error"
        stroke="currentColor"
        strokeWidth="1.25"
      />
    </svg>
  );
}

/** 未ピン: 灰色アウトラインの丸頭 (塗りなし) + currentColor の針. */
export function PushPinOff({ className, ...props }: PushPinIconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      data-icon="push-pin-off"
      className={cn('h-4 w-4', className)}
      {...props}
    >
      <line
        x1="12"
        y1="12.5"
        x2="7.5"
        y2="21"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <circle cx="13" cy="8" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}
