/**
 * AI 機能の「できること / できないこと」定数集 — Wave 5 / W5-FE10.
 *
 * 設計書 `docs/plans/v2-allocation-redesign.md` v0.9 §3.5.7 に対応。
 *
 * 目的:
 *   1. AI 入力モーダル (`<CapabilitiesPanel />`) に表示する一覧の唯一の出典
 *   2. ヘルプ / FAQ ページ (W5-FE13) もこの定数を参照
 *   3. 対象内 / 対象外が増減した時に **1 箇所** で更新できる構成
 *
 * 「何でもできる AI」と誤解させないため、ユーザー目線の例文付きで
 * 範囲を明示することが運用上の信頼に直結する（§3.5.7 設計要件）。
 */

/**
 * AI 機能カテゴリ。設計書 §3.5.2 の対象内表に基づく。
 *
 * - `master`         — 患者・スタッフのマスタ操作（新規登録・メンター指定）
 * - `staff_schedule` — スタッフのスケジュール（休み・イベント）
 * - `patient_visit`  — 患者の訪問予定（特別週・キャンセル・日時変更）
 */
export type AiCapabilityCategory = 'master' | 'staff_schedule' | 'patient_visit';

/** カテゴリのユーザー向け表示ラベル。 */
export const AI_CAPABILITY_CATEGORY_LABELS: Record<AiCapabilityCategory, string> = {
  master: 'マスタ操作',
  staff_schedule: 'スタッフのスケジュール',
  patient_visit: '患者の訪問予定',
};

/**
 * 個別の AI 機能（できること）1 件分の定義。
 *
 * @property id        — 内部識別子 (snake_case)。安定識別子としてログ等にも使う想定
 * @property category  — UI でのグルーピングに利用
 * @property label     — ユーザー向け短い見出し
 * @property example   — 「こう発話すると動く」例文。日本語の自然な指示文
 */
export interface AiCapability {
  id: string;
  category: AiCapabilityCategory;
  label: string;
  example: string;
}

/**
 * v2 時点の AI 対象内一覧（できること）。
 *
 * 設計書 §3.5.7「✅ できること」の 8 項目に 1:1 対応。
 * 並びはユーザー目線で頻度の高い順 → カテゴリ纏まり順。
 */
export const AI_CAPABILITIES: AiCapability[] = [
  {
    id: 'patient_create',
    category: 'master',
    label: '患者の新規登録',
    example: '「田中さんを新規登録、月水金 10時から訪問」',
  },
  {
    id: 'staff_create',
    category: 'master',
    label: 'スタッフの新規登録',
    example: '「看護師の佐藤さんを新規登録」',
  },
  {
    id: 'staff_mentor_assign',
    category: 'master',
    label: 'スタッフのメンター登録',
    example: '「鈴木さんのメンターを山田さんに設定」',
  },
  {
    id: 'staff_override_create',
    category: 'staff_schedule',
    label: 'スタッフの今週だけの休み登録',
    example: '「田中さん木曜の午前休みにして」',
  },
  {
    id: 'staff_event_create',
    category: 'staff_schedule',
    label: 'スタッフのイベント新規（会議・研修）',
    example: '「火曜午後 管理者会議」',
  },
  {
    id: 'patient_special_week_toggle',
    category: 'patient_visit',
    label: '患者の特別訪問週間 ON / OFF',
    example: '「来週は山田さんを特別訪問週間に」',
  },
  {
    id: 'patient_visit_cancel',
    category: 'patient_visit',
    label: '患者の訪問キャンセル',
    example: '「山田さん明日の訪問キャンセル」',
  },
  {
    id: 'patient_visit_reschedule',
    category: 'patient_visit',
    label: '患者の訪問日時変更（今週だけ / 今後固定 を選択）',
    example: '「鈴木さん明日10時に1時間追加」',
  },
];

/**
 * v2 時点の AI 対象外一覧（できないこと）。
 *
 * 設計書 §3.5.7「❌ できないこと」と 1:1 対応。
 * 「誤解されやすい範囲外」を明示することで運用上の信頼を担保する。
 */
export const AI_OUT_OF_SCOPE: string[] = [
  'カイポケへの自動入力',
  '給与・経費計算',
  '契約書・請求書の作成',
  '患者・スタッフの削除',
  '拠点マスタの編集',
  '監査ログの編集',
  'ユーザー（ログインアカウント）の作成・権限変更',
];

/**
 * 範囲外検知時の汎用フィードバック文言（設計書 §3.5.7）。
 *
 * Gemini が `out_of_scope` action_type を返した場合、`fields.message`
 * （または `fields.reason`）に具体的誘導が含まれていればそちらを優先し、
 * なければ本文言をフォールバックとして表示する。
 */
export const AI_OUT_OF_SCOPE_FALLBACK_MESSAGE =
  '申し訳ありません、これは現在 AI で対応していません。該当する画面から手動で入力してください。';

/**
 * AI 解釈結果の action_type が `out_of_scope` かどうかを判定するヘルパ。
 *
 * 設計書 §3.5.7 / §3.5 では Gemini プロンプトに
 * 「対象外の場合は `out_of_scope` アクションを返す」と明示する想定。
 */
export function isOutOfScopeActionType(actionType: string | undefined | null): boolean {
  return actionType === 'out_of_scope';
}

/**
 * カテゴリ別にグループ化した capabilities を返す。
 * UI 描画時のグルーピング用。
 */
export function groupCapabilitiesByCategory(
  list: AiCapability[] = AI_CAPABILITIES,
): Record<AiCapabilityCategory, AiCapability[]> {
  const empty: Record<AiCapabilityCategory, AiCapability[]> = {
    master: [],
    staff_schedule: [],
    patient_visit: [],
  };
  return list.reduce((acc, cap) => {
    acc[cap.category].push(cap);
    return acc;
  }, empty);
}
