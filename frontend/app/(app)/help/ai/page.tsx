'use client';

/**
 * /help/ai — AI 機能の総合ヘルプ / FAQ ページ（W5-FE13）.
 *
 * 設計書 `docs/plans/v2-allocation-redesign.md` v0.9 §3.5.7 の
 *   4. ヘルプ / FAQ ページ — AI 機能の一覧 + 例文集 + 制約事項を一箇所にまとめる
 * を実装する。
 *
 * 文言ソース:
 *   - `frontend/lib/ai-capabilities.ts`（W5-FE10 で整備済み）
 *   - 「できること」/「できないこと」が増減した時に同ファイルを更新するだけで
 *     入力モーダル (`<CapabilitiesPanel />`) と本ページの両方に反映される。
 *
 * 設計ポリシー:
 *   - モーダル内パネル (`<CapabilitiesPanel />`) は折りたたみ前提の省スペース表示
 *   - 本ページは「読み物」としてカテゴリ別カード + 例文 + FAQ をフル展開で提示
 */

import { CheckCircle2, HelpCircle, Sparkles, XCircle } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  AI_CAPABILITIES,
  AI_CAPABILITY_CATEGORY_LABELS,
  AI_OUT_OF_SCOPE,
  AI_OUT_OF_SCOPE_FALLBACK_MESSAGE,
  type AiCapabilityCategory,
  groupCapabilitiesByCategory,
} from '@/lib/ai-capabilities';
import { useUIStore } from '@/lib/stores/ui';

const CATEGORY_ORDER: AiCapabilityCategory[] = ['master', 'staff_schedule', 'patient_visit'];

interface FaqItem {
  q: string;
  a: string;
}

/**
 * よくある質問。設計書 §3.5.7 の運用ポリシーをユーザー目線に翻訳。
 * 増えた場合はこの配列に追記するだけで描画される。
 */
const FAQ_ITEMS: FaqItem[] = [
  {
    q: 'AI が「対応していません」と返してきました。どうすればよいですか？',
    a:
      AI_OUT_OF_SCOPE_FALLBACK_MESSAGE +
      ' AI が対象外と判定した操作は、サイドバーの該当画面（患者・スタッフ・拠点・連携 など）から手動で行ってください。',
  },
  {
    q: 'AI で登録した内容はすぐに反映されますか？',
    a: 'スタッフ権限で AI を実行した場合は「申請」として一時保留され、管理者の承認後に本反映されます。管理者ロールが直接実行した場合は即時反映されます。申請履歴は管理者向けの「申請履歴」画面から確認できます。',
  },
  {
    q: '訪問日時の変更で「今週だけ」と「今後固定」の違いは？',
    a: '「今週だけ」は対象週の 1 件だけを上書きし、翌週以降は元の週間パターンに戻ります。「今後固定」は患者マスタの週間訪問パターン自体を更新するため、以降の週にも継続適用されます。',
  },
  {
    q: '必須項目が足りないと言われた場合は？',
    a: '患者・スタッフの新規登録で住所や勤務曜日などの必須情報が不足している場合、補完用のモーダルが開きます。赤枠で示された項目を入力して再送信してください。',
  },
  {
    q: '「カイポケに自動入力して」とお願いできますか？',
    a: 'いいえ。カイポケへの自動入力は AI の対象外です（運用上の安全性とカイポケ側の仕様変更耐性のため）。カイポケ連携は専用の連携画面（サイドバー「連携」）から実行してください。',
  },
];

export default function AiHelpPage() {
  const grouped = groupCapabilitiesByCategory(AI_CAPABILITIES);

  // W7-FE1: ヘルプページの「AI 入力を試す」ボタンは、レイアウトの AiFab が
  // useAiSubmissionHandler 統合済みで mount している AiInputModal を開く。
  // 二重マウントを避けるため、本ページでは AiInputModal をレンダリングしない。
  const setAiInputOpen = useUIStore((s) => s.setAiInputOpen);

  return (
    <section className="space-y-6">
      {/* ---------- ヘッダー ---------- */}
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="flex items-center gap-2 font-serif text-2xl font-bold text-text-primary">
            <Sparkles className="h-6 w-6 text-brand-primary" strokeWidth={1.75} aria-hidden />
            AI 機能ヘルプ
          </h1>
          <p className="mt-1 text-sm text-text-secondary">
            AI で「できること / できないこと」、よくある質問をまとめています。 画面右下の AI
            ボタン（音声・テキスト入力）から呼び出せます。
          </p>
        </div>
        <Button
          type="button"
          onClick={() => setAiInputOpen(true)}
          className="shrink-0 self-start sm:self-auto"
        >
          <Sparkles className="mr-1.5 h-4 w-4" strokeWidth={1.75} />
          AI 入力を試す
        </Button>
      </header>

      {/* ---------- できること ---------- */}
      <Card className="p-5">
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-5 w-5 text-success" aria-hidden />
          <h2 className="font-serif text-lg font-bold text-text-primary">AI でできること</h2>
        </div>
        <p className="mt-1 text-xs text-text-muted">
          下記の指示は自然な日本語でそのまま話しかけ／入力できます。例文を参考にしてください。
        </p>

        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {CATEGORY_ORDER.map((cat) => {
            const items = grouped[cat];
            if (items.length === 0) return null;
            return (
              <section
                key={cat}
                className="rounded-md border border-border-default bg-bg-muted/30 p-4"
              >
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
                  {AI_CAPABILITY_CATEGORY_LABELS[cat]}
                </h3>
                <ul className="space-y-3">
                  {items.map((cap) => (
                    <li key={cap.id} className="text-sm">
                      <p className="font-medium text-text-primary">{cap.label}</p>
                      <p className="mt-0.5 text-xs text-text-secondary">
                        例: <span className="text-text-primary">{cap.example}</span>
                      </p>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      </Card>

      {/* ---------- できないこと ---------- */}
      <Card className="p-5">
        <div className="flex items-center gap-2">
          <XCircle className="h-5 w-5 text-error" aria-hidden />
          <h2 className="font-serif text-lg font-bold text-text-primary">AI でできないこと</h2>
        </div>
        <p className="mt-1 text-xs text-text-muted">
          下記の操作は安全性・運用方針の観点から AI では対応していません。
          サイドバーの該当画面から手動で行ってください。
        </p>

        <ul className="mt-4 grid list-disc grid-cols-1 gap-y-1.5 pl-6 text-sm text-text-secondary md:grid-cols-2">
          {AI_OUT_OF_SCOPE.map((label) => (
            <li key={label}>{label}</li>
          ))}
        </ul>
      </Card>

      {/* ---------- FAQ ---------- */}
      <Card className="p-5">
        <div className="flex items-center gap-2">
          <HelpCircle className="h-5 w-5 text-brand-primary" aria-hidden />
          <h2 className="font-serif text-lg font-bold text-text-primary">よくある質問</h2>
        </div>

        <div className="mt-4 space-y-2">
          {FAQ_ITEMS.map((item, idx) => (
            <details
              key={idx}
              className="group rounded-md border border-border-default bg-bg-muted/30 px-4 py-3 text-sm open:bg-bg-muted/50"
            >
              <summary className="cursor-pointer list-none font-medium text-text-primary marker:hidden [&::-webkit-details-marker]:hidden">
                <span className="mr-2 inline-block text-text-muted">Q.</span>
                {item.q}
              </summary>
              <p className="mt-2 whitespace-pre-line pl-6 text-text-secondary">
                <span className="-ml-6 mr-2 inline-block text-text-muted">A.</span>
                {item.a}
              </p>
            </details>
          ))}
        </div>
      </Card>
    </section>
  );
}
