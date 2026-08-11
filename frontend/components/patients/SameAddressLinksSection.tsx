/**
 * SameAddressLinksSection (Phase G-21).
 *
 * 患者編集画面 (PatientForm) の最下部に表示する「同住所紐付け」セクション.
 *
 * 仕様:
 *   1. GET /api/v1/patients/{patient_id}/same-address-candidates で候補リスト取得.
 *   2. 各候補の pair_mode を 3 択 radio で表示:
 *        - preferred (デフォルト: ペア優先 / 紐付けレコードなし状態に相当)
 *        - required  (なるべくペア)
 *        - blocked   (絶対にペア禁止)
 *   3. preferred → DELETE /patient-same-address-links/{a}/{b}
 *      required/blocked → POST /patient-same-address-links (upsert)
 *   4. note 入力 (任意) で「なぜ blocked/required にしたか」を記録. blur で保存.
 *   5. candidates 空時は「同住所候補が見つかりません」メッセージ.
 *
 * BE 既存レコードがある候補の場合: pair_mode は blocked/required.
 * BE レコードが無い候補の場合: pair_mode=null → UI 上は preferred として表示.
 */
'use client';

import * as React from 'react';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';

import {
  useDeleteSameAddressLink,
  useSameAddressCandidates,
  useSetSameAddressLink,
} from '@/lib/queries/g21';
import { PAIR_MODES, type PairMode, type SameAddressCandidate } from '@/lib/schemas/g21';
import { isAdminRole } from '@/lib/rbac';

// ─────────────────────────────────────────────────────────────────────────
// Labels
// ─────────────────────────────────────────────────────────────────────────

const PAIR_MODE_LABEL: Record<PairMode, string> = {
  preferred: 'デフォルト (ペア優先)',
  required: 'なるべくペアにする',
  blocked: '絶対にペア禁止',
};

const PAIR_MODE_HINT: Record<PairMode, string> = {
  preferred: '通常の同住所ペア優先ロジックに任せる (BE レコードなし)',
  required: '可能な限り同訪問にまとめる',
  blocked: '同訪問にまとめない',
};

/** 既存レコード未設定 (= preferred として表示) の判定. */
function effectivePairMode(c: SameAddressCandidate): PairMode {
  return c.pair_mode ?? 'preferred';
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export interface SameAddressLinksSectionProps {
  /** 対象患者の ID (新規作成画面では undefined → null 描画). */
  patientId: string;
  /** true で強制的に読み取り専用 (= staff role 等). */
  readOnly?: boolean;
  /**
   * true で外側の Card を描画せず、見出しをサブ見出し (h3) に落として中身だけ返す.
   * 「訪問条件」Card の中に埋め込む用途 (PatientForm). 既定 (false) は従来どおり単独 Card.
   */
  embedded?: boolean;
}

export function SameAddressLinksSection({
  patientId,
  readOnly,
  embedded = false,
}: SameAddressLinksSectionProps) {
  const { data: session } = useSession();
  const role = session?.user?.role;
  const canEdit = !readOnly && isAdminRole(role);

  const { data: candidates = [], isLoading, isError, error } = useSameAddressCandidates(patientId);

  const body = (
    <>
      <div className="flex items-center justify-between">
        {embedded ? (
          <h3 className="font-serif text-base font-bold text-text-primary">同住所紐付け</h3>
        ) : (
          <h2 className="font-serif text-lg font-bold text-text-primary">同住所紐付け</h2>
        )}
        {!canEdit ? (
          <span className="text-xs text-text-muted bg-bg-muted rounded px-2 py-0.5">閲覧のみ</span>
        ) : null}
      </div>
      <p className="text-xs text-text-muted">
        同じ住所に住む他の患者との「ペア優先 / 強制 / 禁止」を設定します.
      </p>

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : isError ? (
        <Alert variant="destructive">
          <AlertTitle>同住所候補の取得に失敗しました</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      ) : candidates.length === 0 ? (
        <p className="text-sm text-text-muted" data-testid="same-address-no-candidates">
          同住所候補が見つかりません
        </p>
      ) : (
        <ul className="space-y-3">
          {candidates.map((c) => (
            <CandidateRow
              key={c.patient_id}
              patientId={patientId}
              candidate={c}
              canEdit={canEdit}
            />
          ))}
        </ul>
      )}
    </>
  );

  if (embedded) {
    return (
      <div
        className="space-y-3 border-t border-border-default pt-4"
        data-testid="same-address-links-section"
      >
        {body}
      </div>
    );
  }

  return (
    <Card className="p-5 space-y-3" data-testid="same-address-links-section">
      {body}
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// CandidateRow
// ─────────────────────────────────────────────────────────────────────────

interface CandidateRowProps {
  patientId: string;
  candidate: SameAddressCandidate;
  canEdit: boolean;
}

function CandidateRow({ patientId, candidate, canEdit }: CandidateRowProps) {
  const currentMode = effectivePairMode(candidate);
  const setLink = useSetSameAddressLink();
  const deleteLink = useDeleteSameAddressLink();
  const [note, setNote] = React.useState<string>(candidate.note ?? '');

  // 候補側 (server) の note が更新されたら同期 (mutation 後の refetch 反映).
  React.useEffect(() => {
    setNote(candidate.note ?? '');
  }, [candidate.note]);

  const isBusy = setLink.isPending || deleteLink.isPending;

  const handleModeChange = async (next: PairMode) => {
    if (next === currentMode) return;
    try {
      if (next === 'preferred') {
        // BE レコードを削除して「デフォルト」に戻す.
        await deleteLink.mutateAsync({
          patient_a_id: patientId,
          patient_b_id: candidate.patient_id,
        });
        toast.success('同住所紐付けをデフォルトに戻しました');
      } else {
        await setLink.mutateAsync({
          patient_a_id: patientId,
          patient_b_id: candidate.patient_id,
          pair_mode: next,
          note: note || null,
        });
        toast.success(`「${PAIR_MODE_LABEL[next]}」に変更しました`);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : '保存に失敗しました';
      toast.error(`同住所紐付けの更新に失敗: ${msg}`);
    }
  };

  const handleNoteBlur = async () => {
    // preferred 状態 (BE レコードなし) では note を保存しない (= レコードを作らない).
    if (currentMode === 'preferred') return;
    // Phase G-21 T4 reviewer H3: radio click → mutation in-flight 中の blur で
    // 同じ payload を二重 POST してしまう race を防ぐ. mutation 終了前の note 編集は
    // 諦め (= 次の dirty 化で再 POST されるか、 ユーザが再 blur すれば送信される).
    if (setLink.isPending || deleteLink.isPending) return;
    if ((note || '') === (candidate.note ?? '')) return;
    try {
      await setLink.mutateAsync({
        patient_a_id: patientId,
        patient_b_id: candidate.patient_id,
        pair_mode: currentMode,
        note: note || null,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : '保存に失敗しました';
      toast.error(`備考の保存に失敗: ${msg}`);
    }
  };

  // radio name は候補ごとに一意にする (同住所 N 人を並列描画するため).
  const groupName = `pair-mode-${candidate.patient_id}`;
  // Phase G-21 T4 reviewer H2: radiogroup の aria-label を候補ごとに個別化し、
  // スクリーンリーダーで同名 group が並んだときに識別できるようにする.
  // 氏名が無い候補も患者コード or 患者 ID で必ず ID 化する.
  const candidateLabel = candidate.patient_name ?? candidate.patient_code ?? candidate.patient_id;
  const radiogroupLabel = `${candidateLabel} の同住所紐付け方針`;

  return (
    <li
      className="rounded-md border border-border-default p-3 space-y-2"
      data-testid={`same-address-candidate-${candidate.patient_id}`}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="font-medium text-text-primary">
          {candidate.patient_name ?? '(氏名未登録)'}
        </span>
        {candidate.patient_code ? (
          <span className="text-xs text-text-muted tnum">[{candidate.patient_code}]</span>
        ) : null}
        {candidate.address ? (
          <span className="text-xs text-text-muted truncate">📍 {candidate.address}</span>
        ) : null}
      </div>

      <div role="radiogroup" aria-label={radiogroupLabel} className="flex flex-col gap-1">
        {PAIR_MODES.map((m) => (
          <label key={m} className="flex items-start gap-2 text-sm text-text-primary">
            <input
              type="radio"
              name={groupName}
              value={m}
              checked={currentMode === m}
              onChange={() => void handleModeChange(m)}
              disabled={!canEdit || isBusy}
              data-testid={`pair-mode-radio-${candidate.patient_id}-${m}`}
              className="mt-0.5 h-4 w-4"
            />
            <span className="flex flex-col">
              <span>{PAIR_MODE_LABEL[m]}</span>
              <span className="text-xs text-text-muted">{PAIR_MODE_HINT[m]}</span>
            </span>
          </label>
        ))}
      </div>

      {/* 備考: preferred 以外でのみ意味があるが入力自体は常時表示. */}
      <label className="flex flex-col gap-1 text-xs text-text-secondary">
        <span className="font-medium">備考 (任意)</span>
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onBlur={() => void handleNoteBlur()}
          disabled={!canEdit || isBusy || currentMode === 'preferred'}
          placeholder={
            currentMode === 'preferred'
              ? 'デフォルト状態では備考を保存しません'
              : 'なぜ blocked / required にしたか'
          }
          className="h-9 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-primary disabled:opacity-60"
          data-testid={`pair-mode-note-${candidate.patient_id}`}
          maxLength={500}
        />
      </label>
    </li>
  );
}
