'use client';

/**
 * ProcedureGuide — カイポケ反映の作業手順を番号付きで明示 (K-2 UI)。
 *
 * 管理者が迷わず・間違えず作業できるよう、4ステップの順番と各ステップの
 * 中身・実行場所を1枚に示す。実運用の型。
 */
import { Card } from '@/components/ui/card';

const STEPS = [
  {
    no: 1,
    title: 'スケジュール展開',
    desc: '対象月をカイポケで展開（月間の予定枠を作成）。「操作メニュー」から。',
  },
  {
    no: 2,
    title: 'CSV エクスポート',
    desc: 'カイポケの現況を取得。※「差分計算」が内部で自動取得するため通常は省略可。',
  },
  {
    no: 3,
    title: '差分計算（週）',
    desc: '対象週を選び「この週の差分を計算」。CareFlow の予定との差分を週ビューで確認。',
  },
  {
    no: 4,
    title: 'スケジュール反映実行',
    desc: 'まず dry-run で確認 → 問題なければ「この週で本番反映」。ライブモニターで進捗を目視。',
  },
];

export function ProcedureGuide() {
  return (
    <Card className="p-5">
      <h2 className="mb-1 font-serif text-lg font-bold text-text-primary">反映の手順</h2>
      <p className="mb-4 text-xs text-text-secondary">
        この順番で進めます。前提: CareFlow で対象週を生成し、スタッフ割当まで済ませておくこと。
      </p>
      <ol className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((s) => (
          <li
            key={s.no}
            className="flex gap-3 rounded-lg border border-border-default bg-bg-base p-3"
          >
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-primary text-sm font-bold text-white">
              {s.no}
            </span>
            <div className="space-y-1">
              <p className="text-sm font-semibold text-text-primary">{s.title}</p>
              <p className="text-xs leading-relaxed text-text-muted">{s.desc}</p>
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}
