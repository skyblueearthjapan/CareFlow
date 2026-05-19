'use client';

/**
 * 不足情報補完モーダル — Wave 5 / W5-FE12.
 *
 * 設計書 `docs/plans/v2-allocation-redesign.md` v0.9 §3.5.2
 * 「不足情報補完モーダル（重要）」を実装する。
 *
 * 役割:
 *   1. AI が音声 / テキストを構造化 JSON に変換した結果のうち、
 *      `missing_fields: list[str]` で明示された必須未入力フィールドを
 *      管理者がモーダル上で手動補完できるようにする。
 *   2. 補完が完了したら `onComplete(merged)` を呼び出し、呼び出し側
 *      （主に `<AiInputModal />` の missingInfoSlot に置かれる
 *      `<SubmitToPendingHandler />` 等）が通常の作成 API へ POST する。
 *
 * 使い分け:
 *   - 新規登録系（`patient_create` / `staff_create`）が主なユースケース
 *     （項目数が多く、音声 / テキストでは情報が抜け落ちやすい）
 *   - 編集系でも `missing_fields` が空でなければ同じ UX で補完を促す
 *
 * 配置:
 *   ```tsx
 *   <AiInputModal
 *     missingInfoSlot={
 *       <MissingInfoModal
 *         open={needsCompletion}
 *         onOpenChange={setNeedsCompletion}
 *         missingFields={interpreted.missing_fields ?? []}
 *         partialData={interpreted.fields}
 *         onComplete={(data) => createPatient(data)}
 *       />
 *     }
 *   />
 *   ```
 *
 * 実装メモ:
 *   - 入力 UI は最低限のテキスト入力のみ（FE12 スコープ）。日付 / 時刻 /
 *     enum 等の専用 UI は本チケットでは扱わず、後続で必要に応じて拡張する。
 *   - 必須未入力（trim 後 empty）は赤枠 (border-error) でハイライトし、
 *     「登録」ボタンを `disabled` にする（受入基準: 必須欠損時に赤枠表示）。
 *   - フィールド名（snake_case）はラベル変換テーブルで日本語に寄せる。
 *     未登録キーは humanize（snake → Title Case）でフォールバック。
 *   - W5-FE10 の `missingInfoSlot` 仕様により、本コンポーネントは独自
 *     `<Dialog />` を mount して問題ない（AiInputModal とは別 zIndex）。
 */

import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Field label dictionary
// ---------------------------------------------------------------------------

/**
 * AI が `missing_fields` で返すフィールド名（snake_case）を、UI 表示用の
 * 日本語ラベルに変換するテーブル。
 *
 * 登録対象は `patient_create` / `staff_create` の必須項目を中心に揃える。
 * 未登録キーは `humanizeFieldKey()` で snake_case → Title Case にフォール
 * バック表示するので、抜けがあっても画面は壊れない。
 */
const FIELD_LABELS_JA: Record<string, string> = {
  // 共通
  code: 'コード',
  name: '氏名',
  kana: 'フリガナ',
  sex: '性別',
  age: '年齢',
  status: 'ステータス',
  address: '住所',
  primary_office_id: '主担当拠点',
  note: '備考',

  // 患者特有
  insurance: '保険種別',
  required_staff_count: '必要スタッフ人数',
  sex_restriction: '性別制限',
  ng_time_start: 'NG 時間（開始）',
  ng_time_end: 'NG 時間（終了）',
  area: 'エリア',
  specified_type: '指名タイプ',
  weekly_pattern: '週間訪問パターン',

  // スタッフ特有
  role: 'ロール',
  home_address: '自宅住所',
  max_per_day: '1 日の最大訪問数',
  skill_level: 'スキルレベル',
  assignment_volume: '割付ボリューム',

  // 日時系（編集 / キャンセル等で出現する可能性）
  date: '日付',
  start_time: '開始時刻',
  end_time: '終了時刻',
  patient_id: '患者',
  staff_id: 'スタッフ',
  reason: '理由',
};

/**
 * snake_case のフィールド名を Title Case に変換するフォールバック。
 * 例: `preferred_start` → `Preferred Start`
 */
function humanizeFieldKey(key: string): string {
  return key
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function fieldLabel(key: string): string {
  return FIELD_LABELS_JA[key] ?? humanizeFieldKey(key);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * 任意値が「埋まっている」と見なせるかを判定する。
 *
 * - `undefined` / `null` → 未入力
 * - 空文字（trim 後）   → 未入力
 * - 空配列              → 未入力
 * - 空オブジェクト      → 未入力（`{}` は不足扱い）
 * - それ以外            → 入力済み
 */
function isFilled(value: unknown): boolean {
  if (value === undefined || value === null) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value as object).length > 0;
  return true;
}

/**
 * `partialData` から該当キーの初期値を取り出して、
 * `<input>` にバインドできる文字列に変換する。
 *
 * - string  → そのまま
 * - number  → `String(n)`
 * - boolean → `'true' | 'false'`
 * - その他  → `JSON.stringify` または `''`
 */
function initialInputValue(raw: unknown): string {
  if (raw === undefined || raw === null) return '';
  if (typeof raw === 'string') return raw;
  if (typeof raw === 'number' || typeof raw === 'boolean') return String(raw);
  try {
    return JSON.stringify(raw);
  } catch {
    return '';
  }
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface MissingInfoModalProps {
  /** AI 解釈結果から得られた不足フィールド配列。 */
  missingFields: string[];
  /** 既存解釈結果（埋まっているフィールド）。`onComplete` でマージして渡す。 */
  partialData: Record<string, unknown>;
  /** 補完完了時の callback。`partialData` と入力値をマージしたものを受け取る。 */
  onComplete: (completed: Record<string, unknown>) => void;
  /** モーダル開閉。 */
  open: boolean;
  onOpenChange: (next: boolean) => void;
  /**
   * 任意のタイトル文言（既定: 「不足情報を補完」）。
   * 例えば呼び出し側が `patient_create` / `staff_create` 等で文言を寄せたい場合に上書きする。
   */
  title?: string;
  /**
   * 任意の説明文言（既定: 「AI が以下の必須項目を判定できませんでした。」）。
   */
  description?: string;
  /** 「登録」ボタンの文言（既定: 「登録」）。 */
  submitLabel?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MissingInfoModal({
  missingFields,
  partialData,
  onComplete,
  open,
  onOpenChange,
  title = '不足情報を補完',
  description = 'AI が以下の必須項目を判定できませんでした。手動で補完してください。',
  submitLabel = '登録',
}: MissingInfoModalProps) {
  // 重複・空文字を除いた表示用フィールド一覧。
  const fields = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const key of missingFields) {
      if (!key || typeof key !== 'string') continue;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(key);
    }
    return out;
  }, [missingFields]);

  // 各フィールドの入力値。partialData に値があればそれを初期値とする。
  // モーダルが open になるたびに再初期化（破棄→再オープン時の状態残留防止）。
  const [values, setValues] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) return;
    const next: Record<string, string> = {};
    for (const key of fields) {
      next[key] = initialInputValue(partialData[key]);
    }
    setValues(next);
    // partialData / fields は open 時点で固定する。途中変化で値を巻き戻さない。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // 受入基準: 必須欠損時に赤枠表示 → 各フィールド単位で空判定。
  const emptyKeys = useMemo(() => {
    const out = new Set<string>();
    for (const key of fields) {
      if (!isFilled(values[key])) out.add(key);
    }
    return out;
  }, [fields, values]);

  const allFilled = emptyKeys.size === 0 && fields.length > 0;

  function setField(key: string, next: string) {
    setValues((prev) => ({ ...prev, [key]: next }));
  }

  function handleSubmit() {
    if (!allFilled) return;
    // partialData と入力値をマージ。空文字 trim 後の値で上書き。
    const merged: Record<string, unknown> = { ...partialData };
    for (const key of fields) {
      const v = values[key];
      merged[key] = typeof v === 'string' ? v.trim() : v;
    }
    onComplete(merged);
    onOpenChange(false);
  }

  function handleCancel() {
    onOpenChange(false);
  }

  // missing_fields が空 → そもそも開かない運用想定だが、保険として何も描画しない。
  if (fields.length === 0) {
    return null;
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-[480px]"
        aria-describedby="missing-info-modal-desc"
        data-testid="missing-info-modal"
      >
        <DialogHeader>
          <div className="flex items-start gap-3">
            <div
              className="flex h-7 w-7 items-center justify-center rounded-md bg-warning/15 text-warning"
              aria-hidden
            >
              <AlertTriangle className="h-4 w-4" strokeWidth={1.75} />
            </div>
            <div>
              <DialogTitle>{title}</DialogTitle>
              <DialogDescription id="missing-info-modal-desc">{description}</DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className="space-y-3">
          {fields.map((key) => {
            const isEmpty = emptyKeys.has(key);
            const inputId = `missing-info-${key}`;
            return (
              <div key={key} className="space-y-1">
                <Label htmlFor={inputId} className="flex items-center gap-1">
                  <span>{fieldLabel(key)}</span>
                  <span className="text-error" aria-label="必須">
                    *
                  </span>
                </Label>
                <Input
                  id={inputId}
                  value={values[key] ?? ''}
                  onChange={(e) => setField(key, e.target.value)}
                  placeholder={`${fieldLabel(key)}を入力`}
                  aria-invalid={isEmpty}
                  aria-required
                  data-testid={`missing-info-input-${key}`}
                  className={cn(
                    isEmpty &&
                      'border-error focus-visible:border-error focus-visible:ring-error/30',
                  )}
                />
                {/* デバッグ補助: バックエンドのフィールド名を小さく併記 */}
                <p className="text-[11px] text-text-muted">
                  <code className="rounded bg-bg-muted px-1 py-0.5">{key}</code>
                </p>
              </div>
            );
          })}
        </div>

        <DialogFooter>
          <Button type="button" variant="ghost" onClick={handleCancel}>
            キャンセル
          </Button>
          <Button
            type="button"
            onClick={handleSubmit}
            disabled={!allFilled}
            data-testid="missing-info-submit"
          >
            {submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
