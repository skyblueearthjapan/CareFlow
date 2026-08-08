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

/**
 * CornerPushPin — カード右上に「壁へ打ち込んだ」ピン留め表示 (PO要望 2026-07-08).
 *
 * 📍住所マーカーと行内の小さな PushPin が紛らわしい問題への回答:
 * 状態表示のピンは行内アイコンをやめ、カードの右上隅に大きめの画鋲を
 * 斜めに打ち込む (= メモを壁にピン留めした見た目)。上品/かわいい必須のため
 *   - 赤いグロス頭 (fill-error) + 白リム + つやハイライト
 *   - 柔らかい落ち影 (drop-shadow) で「刺さっている」立体感
 * を持たせる。針はカード中央へ向く斜め描画。
 *
 * 使い方: カード側に relative (または absolute) を付け、子として置くだけ。
 * **頭はカードの角から少しはみ出す** (translate で頭の中心≒角。PO要望 2026-07-08:
 * 角丸+overflow-hidden で頭が欠けるのは NG・はみ出して見せる)。そのため
 * ピンを置くカードは overflow-hidden を外すこと (truncate は各要素で担保)。
 * 位置・サイズは既定 (右上・h-5) を className で上書き可能。クリックは奪わない
 * (pointer-events-none)。操作用トグル (PinToggleButton / PinScopeMenu) は従来どおり。
 */
export function CornerPushPin({ className, ...props }: PushPinIconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      role="img"
      aria-label="完全固定"
      data-icon="corner-push-pin"
      className={cn(
        // translate は自身サイズ比 → h-4/h-3.5 に縮めても「頭の中心≒カードの角」を維持。
        'pointer-events-none absolute right-0 top-0 z-[3] h-5 w-5 translate-x-[36%] -translate-y-[29%]',
        'drop-shadow-[1px_2px_1.5px_rgba(74,52,38,0.35)]',
        className,
      )}
      {...props}
    >
      {/* 針: カード中央 (左下) へ向けて刺さる。先端に向けて視覚的に締まるよう round cap. */}
      <line
        x1="12.4"
        y1="11.0"
        x2="7.0"
        y2="19.2"
        stroke="#9b9289"
        strokeWidth="1.9"
        strokeLinecap="round"
      />
      {/* 頭: 赤いグロス玉 + 白リム (上品さ/かわいさの要)。 */}
      <circle cx="15.3" cy="6.9" r="5.9" className="fill-error" stroke="#fff" strokeWidth="1.4" />
      {/* つやハイライト。 */}
      <circle cx="13.3" cy="4.9" r="1.7" fill="#fff" opacity="0.85" />
    </svg>
  );
}

/**
 * CornerWeekPushPin — カード右上に打ち込む **青い** 画鋲 (週のピン / PO 決定 2026-08-08).
 *
 * 赤い画鋲 (CornerPushPin) との対比で色分けする:
 *   - 赤 = 型 (固定訪問スケジュール) に対するピン。毎週効く。型と一致する訪問にしか刺せない
 *   - 青 = 今週の訪問に対するピン。その週だけ効く。**型とズレていても刺せる**
 *
 * 意匠は赤と完全に同じ (グロス頭 + 白リム + つや + 落ち影)。色だけが違うことで
 * 「同じ種類の操作の色違い」だと直感的に読める。
 *
 * 赤と同時に立つことは通常無い (赤が刺さる = 型と一致 = 週ピンの出番が無い) が、
 * 併存しても重ならないよう既定位置を少し左にずらしてある。
 */
export function CornerWeekPushPin({ className, ...props }: PushPinIconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      role="img"
      aria-label="今週固定（青ピン）"
      data-icon="corner-week-push-pin"
      className={cn(
        'pointer-events-none absolute right-0 top-0 z-[3] h-5 w-5 translate-x-[36%] -translate-y-[29%]',
        'drop-shadow-[1px_2px_1.5px_rgba(38,52,74,0.35)]',
        className,
      )}
      {...props}
    >
      <line
        x1="12.4"
        y1="11.0"
        x2="7.0"
        y2="19.2"
        stroke="#9b9289"
        strokeWidth="1.9"
        strokeLinecap="round"
      />
      {/* 頭: 青いグロス玉 + 白リム。赤 (fill-error) との対比で var(--info) を使う。 */}
      <circle cx="15.3" cy="6.9" r="5.9" fill="var(--info)" stroke="#fff" strokeWidth="1.4" />
      <circle cx="13.3" cy="4.9" r="1.7" fill="#fff" opacity="0.85" />
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
