'use client';

/**
 * /settings/checkin — QR 訪問チェックインのしきい値設定 (Phase 4)。
 *
 * 全社一律のしきい値 (6 項目) を admin/manager が編集する専用ページ。
 *   GET /api/v1/checkin-settings → 現在の有効値 + 既定フラグ
 *   PUT /api/v1/checkin-settings → 部分更新 (omit=不変, null=既定リセット)
 *
 * 2 グループ構成:
 *   📍 距離のルール … ① 一致 (match_m) / ② 要確認 (review_m) / ③ GPS精度許容 (accuracy_m)
 *                      + 距離プレビュー (クライアントで距離→判定の概算を再現)
 *   ⏱ 時間のルール … ④ 未訪問の猶予 / ⑤ 遅延 / ⑥ 退出忘れ (長時間訪問中)
 *
 * 保存で PUT → monitor を invalidate して反映。review_m >= match_m を UI でも検証
 * (BE も 422 で弾く)。時間系の変更は「翌日/新規visitから」運用の注意書きを添える。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useSession } from 'next-auth/react';
import { MapPin, Clock, Settings, Info, RotateCcw } from 'lucide-react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';

import { useCheckinSettings, useUpdateCheckinSettings } from '@/lib/queries/checkinSettings';
import {
  CHECKIN_RANGES,
  CHECKIN_SETTINGS_RESET_ALL,
  type CheckinSettingsUpdate,
  type CheckinSettingsValues,
} from '@/lib/schemas/checkinSettings';

import { SliderField } from '../scheduling/_components/SliderField';

// ─────────────────────────────────────────────────────────────────────────
// プレビュー: 距離 → 判定 (クライアント概算。正本はサーバ judge)
// ─────────────────────────────────────────────────────────────────────────

type PreviewVerdict = 'match' | 'review' | 'mismatch';

function classifyWith(distanceM: number, matchM: number, reviewM: number): PreviewVerdict {
  if (distanceM <= matchM) return 'match';
  if (distanceM <= reviewM) return 'review';
  return 'mismatch';
}

const VERDICT_META: Record<PreviewVerdict, { label: string; cls: string }> = {
  match: { label: '一致', cls: 'bg-success/15 text-success' },
  review: { label: '要確認', cls: 'bg-warning/15 text-warning' },
  mismatch: { label: '不一致', cls: 'bg-error/15 text-error' },
};

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export default function CheckinSettingsPage() {
  const { data: session, status } = useSession();
  const role = session?.user?.role;
  const canEdit = role === 'admin' || role === 'manager';

  // RB (PO決定 2026-07-08): 閲覧は全ロール (PC版の表示統一)。保存系は canEdit で
  // disabled にし、BE の RBAC (PUT=admin/manager) が最終防衛する。

  const settingsQuery = useCheckinSettings();
  const updateMutation = useUpdateCheckinSettings();

  const server = settingsQuery.data?.values ?? null;
  const isDefault = settingsQuery.data?.is_default ?? null;

  const [draft, setDraft] = useState<CheckinSettingsValues | null>(null);
  // プレビュー用の距離 (m)。
  const [previewDist, setPreviewDist] = useState(80);

  const patch = <K extends keyof CheckinSettingsValues>(key: K, value: CheckinSettingsValues[K]) =>
    setDraft((d) => (d ? { ...d, [key]: value } : d));

  // 変更フィールドのみを payload に積む (省略=不変)。
  const changedPayload = useMemo<CheckinSettingsUpdate>(() => {
    if (!draft || !server) return {};
    const out: CheckinSettingsUpdate = {};
    (Object.keys(draft) as Array<keyof CheckinSettingsValues>).forEach((k) => {
      if (draft[k] !== server[k]) (out as Record<string, unknown>)[k] = draft[k];
    });
    return out;
  }, [draft, server]);

  const isDirty = Object.keys(changedPayload).length > 0;
  // review_m >= match_m を UI でも検証 (BE も 422)。
  const reviewLtMatch = draft != null && draft.review_m < draft.match_m;

  // サーバ値が変わったら draft を再シード (未保存編集中は上書きしない)。
  const isDirtyRef = useRef(isDirty);
  isDirtyRef.current = isDirty;
  useEffect(() => {
    if (!server) return;
    setDraft((d) => (d === null || !isDirtyRef.current ? server : d));
  }, [server]);

  const handleSave = async () => {
    if (!isDirty) return;
    if (reviewLtMatch) {
      toast.error('「要確認」は「一致」以上の距離にしてください');
      return;
    }
    try {
      await updateMutation.mutateAsync(changedPayload);
      toast.success('しきい値を保存しました');
    } catch {
      toast.error('保存に失敗しました。入力値をご確認ください');
    }
  };

  const allDefault = useMemo(
    () => (isDefault ? Object.values(isDefault).every(Boolean) : false),
    [isDefault],
  );

  const handleResetAll = async () => {
    if (allDefault) {
      toast.info('すべて既定値のままです');
      return;
    }
    try {
      await updateMutation.mutateAsync(CHECKIN_SETTINGS_RESET_ALL);
      toast.success('すべて既定値に戻しました');
    } catch {
      toast.error('既定値へのリセットに失敗しました');
    }
  };

  if (status === 'loading') {
    return null;
  }

  const verdict = draft ? classifyWith(previewDist, draft.match_m, draft.review_m) : null;

  return (
    <section className="mx-auto w-full max-w-3xl space-y-6 pb-24">
      <header className="space-y-1">
        <div className="flex items-center gap-2">
          <Settings className="h-6 w-6 text-brand-primary" strokeWidth={1.75} />
          <h1 className="font-serif text-2xl font-bold text-text-primary">
            チェックインしきい値の設定
          </h1>
        </div>
        <p className="text-sm text-text-secondary">
          QR 訪問チェックインの判定 (距離・遅延・退出忘れ) に使う全社一律のしきい値です。
        </p>
      </header>

      {settingsQuery.isError && (
        <Alert variant="destructive">
          <AlertDescription>設定の取得に失敗しました。再読み込みしてください。</AlertDescription>
        </Alert>
      )}

      {settingsQuery.isLoading || !draft ? (
        <div className="space-y-4">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      ) : (
        <>
          {/* ───────────── 📍 距離のルール ───────────── */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <MapPin className="h-5 w-5 text-brand-primary" strokeWidth={1.75} />
                距離のルール
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              <SliderField
                id="match-m"
                label="「一致」とみなす距離"
                description="登録住所からこの距離以内なら「一致」と判定します。"
                value={draft.match_m}
                min={CHECKIN_RANGES.match_m.min}
                max={CHECKIN_RANGES.match_m.max}
                step={CHECKIN_RANGES.match_m.step}
                unit="m"
                isDefault={isDefault?.match_m}
                onChange={(n) => patch('match_m', n)}
                disabled={!canEdit || updateMutation.isPending}
              />
              <SliderField
                id="review-m"
                label="「要確認」とみなす距離"
                description="この距離までは「要確認」、超えると「不一致」です。「一致」以上の値にしてください。"
                value={draft.review_m}
                min={CHECKIN_RANGES.review_m.min}
                max={CHECKIN_RANGES.review_m.max}
                step={CHECKIN_RANGES.review_m.step}
                unit="m"
                isDefault={isDefault?.review_m}
                onChange={(n) => patch('review_m', n)}
                disabled={!canEdit || updateMutation.isPending}
              />
              {reviewLtMatch && (
                <p className="text-xs font-medium text-error" data-testid="review-lt-match-warning">
                  「要確認」は「一致」以上の距離にしてください。
                </p>
              )}
              <SliderField
                id="accuracy-m"
                label="GPS 精度の許容"
                description="GPS の誤差がこの値より粗いときは、近距離でも「要確認」にします。"
                value={draft.accuracy_m}
                min={CHECKIN_RANGES.accuracy_m.min}
                max={CHECKIN_RANGES.accuracy_m.max}
                step={CHECKIN_RANGES.accuracy_m.step}
                unit="m"
                isDefault={isDefault?.accuracy_m}
                onChange={(n) => patch('accuracy_m', n)}
                disabled={!canEdit || updateMutation.isPending}
              />

              {/* プレビュー再判定 */}
              <div className="space-y-2 rounded-lg border border-border-default bg-bg-muted/40 p-3">
                <SliderField
                  id="preview-distance"
                  label="プレビュー: 距離を動かして判定を確認"
                  description="設定中のしきい値で、この距離がどう判定されるかの概算です（正本はサーバ）。"
                  value={previewDist}
                  min={0}
                  max={Math.max(600, draft.review_m + 100)}
                  step={10}
                  unit="m"
                  onChange={setPreviewDist}
                />
                {verdict && (
                  <div
                    data-testid="preview-verdict"
                    className={
                      'inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-semibold ' +
                      VERDICT_META[verdict].cls
                    }
                  >
                    {previewDist}m → {VERDICT_META[verdict].label}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* ───────────── ⏱ 時間のルール ───────────── */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Clock className="h-5 w-5 text-brand-primary" strokeWidth={1.75} />
                時間のルール
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              <SliderField
                id="no-show-grace"
                label="未訪問とみなすまでの猶予"
                description="予定開始からこの分数を過ぎても到着記録が無いと「未訪問」にします。"
                value={draft.no_show_grace_min}
                min={CHECKIN_RANGES.no_show_grace_min.min}
                max={CHECKIN_RANGES.no_show_grace_min.max}
                step={CHECKIN_RANGES.no_show_grace_min.step}
                unit="分"
                isDefault={isDefault?.no_show_grace_min}
                onChange={(n) => patch('no_show_grace_min', n)}
                disabled={!canEdit || updateMutation.isPending}
              />
              <SliderField
                id="late-min"
                label="遅延とみなす到着ズレ"
                description="予定開始からこの分数以上遅れて到着した訪問を「要確認」にします。"
                value={draft.late_min}
                min={CHECKIN_RANGES.late_min.min}
                max={CHECKIN_RANGES.late_min.max}
                step={CHECKIN_RANGES.late_min.step}
                unit="分"
                isDefault={isDefault?.late_min}
                onChange={(n) => patch('late_min', n)}
                disabled={!canEdit || updateMutation.isPending}
              />
              <SliderField
                id="max-inprogress"
                label="退出忘れとみなす滞在時間"
                description="到着済みで退出記録が無いまま、この分数を超えて滞在中の訪問を「要確認」にします。"
                value={draft.max_inprogress_min}
                min={CHECKIN_RANGES.max_inprogress_min.min}
                max={CHECKIN_RANGES.max_inprogress_min.max}
                step={CHECKIN_RANGES.max_inprogress_min.step}
                unit="分"
                isDefault={isDefault?.max_inprogress_min}
                onChange={(n) => patch('max_inprogress_min', n)}
                disabled={!canEdit || updateMutation.isPending}
              />
              <p className="flex items-start gap-1.5 text-xs text-text-muted">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
                時間のルールはモニターが現在値で都度評価します。日中の変更は当日分の判定にも
                即時反映されるため、運用上は翌日/新規の訪問から変えるのが安全です。
              </p>
            </CardContent>
          </Card>
        </>
      )}

      {/* ───────────── アクションバー ───────────── */}
      {draft && (
        <div className="sticky bottom-0 flex items-center justify-between gap-3 border-t border-border-default bg-bg-base/95 px-1 py-3 backdrop-blur">
          <Button
            type="button"
            variant="outline"
            onClick={handleResetAll}
            disabled={!canEdit || updateMutation.isPending}
            data-testid="reset-all"
          >
            <RotateCcw className="mr-2 h-4 w-4" strokeWidth={1.75} />
            すべて既定に戻す
          </Button>
          <Button
            type="button"
            onClick={handleSave}
            disabled={!canEdit || !isDirty || reviewLtMatch || updateMutation.isPending}
            data-testid="save"
          >
            {updateMutation.isPending ? '保存中…' : '保存'}
          </Button>
        </div>
      )}
    </section>
  );
}
