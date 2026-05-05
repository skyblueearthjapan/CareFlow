'use client';

/**
 * /schedule — スケジュール v2 (W3-FE5).
 *
 * 設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.2 (Layer 1
 * 時間配置) に対応する週ビュー。Wave 3 で旧 (Phase 4-8 / W2-A) の
 * `WeekGrid` 画面を `ScheduleGridV2` に置き換えた。
 *
 * 旧 UI で提供していた以下の機能はこのチケットでは **意図的に外している** ：
 *   - 拠点 / スタッフ / 患者フィルタ
 *   - 訪問チップクリックでの個別編集ダイアログ
 *   - 未割当一覧
 *   - 割当エンジン実行ボタン (将来 W4-FE で再導入)
 *
 * これらは Wave 4 の Layer 2 / Layer 3 UI で再構築される予定。本画面は
 * 「患者を時刻スロットに置く → 固定する」という Layer 1 の操作だけに
 * 集中する。
 *
 * RBAC:
 *   - admin / manager: 全機能利用可
 *   - staff: 閲覧のみ (drag & 固定はクライアントでも disable, BE でも 403)
 */
import { useSession } from 'next-auth/react';

import { ScheduleGridV2 } from '@/components/schedule/v2/ScheduleGridV2';

export default function SchedulePage() {
  const { data: session } = useSession();
  const role = session?.user?.role ?? 'staff';
  const canEdit = role === 'admin' || role === 'manager';

  return (
    <section className="space-y-4">
      <header>
        <h1 className="font-serif text-2xl font-bold text-text-primary">スケジュール</h1>
        <p className="text-sm text-text-secondary">
          週単位の訪問パターンを 15 分セル単位で配置 / 固定します (Layer 1).
        </p>
      </header>

      <ScheduleGridV2 canEdit={canEdit} />
    </section>
  );
}
