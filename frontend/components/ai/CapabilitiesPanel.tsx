'use client';

/**
 * AI モーダル内に表示する「できること / できないこと」案内パネル — W5-FE10.
 *
 * 設計書 `docs/plans/v2-allocation-redesign.md` v0.9 §3.5.7 の
 *   1.「できること」一覧（カテゴリ別 + 例文付き）
 *   2.「できないこと」表示（折りたたみ可）
 * を実装する。
 *
 * 配置: `<AiInputModal />` の input ステージ内（送信ボタン上部）。
 *
 * 実装メモ:
 *   - 折りたたみは標準 `<details>` を使用（追加依存ゼロ・a11y 既定対応）
 *   - 文言ソースは `frontend/lib/ai-capabilities.ts` に集約
 *   - W5-FE13 (AI ヘルプ / FAQ ページ) も同じ定数を参照する
 */
import { CheckCircle2, ChevronRight, XCircle } from 'lucide-react';

import {
  AI_CAPABILITIES,
  AI_CAPABILITY_CATEGORY_LABELS,
  AI_OUT_OF_SCOPE,
  type AiCapability,
  type AiCapabilityCategory,
  groupCapabilitiesByCategory,
} from '@/lib/ai-capabilities';

interface CapabilitiesPanelProps {
  /**
   * 「できること」セクションを既定で開いた状態にするか。
   * モバイル (voiceFirst) では誤タップ防止のため既定 false 推奨。
   */
  defaultCanOpen?: boolean;
  /** 「できないこと」セクションを既定で開いた状態にするか。 */
  defaultCannotOpen?: boolean;
  /** 任意の追加クラス。 */
  className?: string;
}

const CATEGORY_ORDER: AiCapabilityCategory[] = ['master', 'staff_schedule', 'patient_visit'];

export function CapabilitiesPanel({
  defaultCanOpen = true,
  defaultCannotOpen = false,
  className,
}: CapabilitiesPanelProps) {
  const grouped = groupCapabilitiesByCategory(AI_CAPABILITIES);

  return (
    <div className={`space-y-2 ${className ?? ''}`} data-testid="ai-capabilities-panel">
      {/* ---------- できること ---------- */}
      <details
        open={defaultCanOpen}
        className="group rounded-md border border-border-default bg-bg-muted/40 px-3 py-2 text-xs"
      >
        <summary className="flex cursor-pointer items-center gap-2 text-text-primary marker:hidden [&::-webkit-details-marker]:hidden">
          <ChevronRight
            className="h-3.5 w-3.5 transition-transform group-open:rotate-90"
            aria-hidden
          />
          <CheckCircle2 className="h-3.5 w-3.5 text-success" aria-hidden />
          <span className="font-medium">AI でできること</span>
          <span className="text-text-muted">（例文付き）</span>
        </summary>

        <div className="mt-2 space-y-3 pl-1">
          {CATEGORY_ORDER.map((cat) => {
            const items = grouped[cat];
            if (items.length === 0) return null;
            return (
              <section key={cat}>
                <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                  {AI_CAPABILITY_CATEGORY_LABELS[cat]}
                </h4>
                <ul className="space-y-1.5">
                  {items.map((cap) => (
                    <CapabilityItem key={cap.id} capability={cap} />
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      </details>

      {/* ---------- できないこと ---------- */}
      <details
        open={defaultCannotOpen}
        className="group rounded-md border border-border-default bg-bg-muted/40 px-3 py-2 text-xs"
      >
        <summary className="flex cursor-pointer items-center gap-2 text-text-primary marker:hidden [&::-webkit-details-marker]:hidden">
          <ChevronRight
            className="h-3.5 w-3.5 transition-transform group-open:rotate-90"
            aria-hidden
          />
          <XCircle className="h-3.5 w-3.5 text-error" aria-hidden />
          <span className="font-medium">AI でできないこと</span>
          <span className="text-text-muted">（手動で操作してください）</span>
        </summary>

        <ul className="mt-2 list-disc space-y-0.5 pl-6 text-text-secondary">
          {AI_OUT_OF_SCOPE.map((label) => (
            <li key={label}>{label}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function CapabilityItem({ capability }: { capability: AiCapability }) {
  return (
    <li className="text-text-secondary">
      <span className="text-text-primary">{capability.label}</span>
      <span className="ml-1 whitespace-pre-line text-text-muted">— {capability.example}</span>
    </li>
  );
}
