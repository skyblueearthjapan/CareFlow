'use client';

/**
 * /settings/scheduling — 自動最適化ルールの管理UI (Phase G-88 Step4)。
 *
 * 事業所共通の最適化設定 (8 項目) を admin/manager が編集する専用ページ。
 *   GET /api/v1/scheduling-settings → 現在の有効値 + 既定フラグ
 *   PUT /api/v1/scheduling-settings → 部分更新 (omit=不変, null=既定リセット)
 *
 * 3 グループ構成:
 *   ⏱ 時間のルール   … ① 訪問間バッファー / ③ 昼休み (標準の長さ + 取得時間帯)
 *   🚗 移動の見積もり … ② 移動速度 (1kmあたり◯分 をライブ表示)
 *   🏢 営業・コース   … ④ 営業時間 / ⑤ 1コース最大人数
 *
 * 「変更は次回の最適化から反映」を明示し、各項目に「既定値に戻す」相当として
 * 一括「すべて既定に戻す」を用意。範囲外/時刻順は BE が 422 で弾く (UI でも clamp)。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useSession } from 'next-auth/react';
import { Clock, Car, Building2, RotateCcw, Info } from 'lucide-react';

import { RakusukeTitle } from '@/components/brand/Rakusuke';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';

import {
  useSchedulingSettings,
  useUpdateSchedulingSettings,
} from '@/lib/queries/schedulingSettings';
import {
  SCHEDULING_RANGES,
  SCHEDULING_SETTINGS_RESET_ALL,
  type SchedulingSettingsUpdate,
  type SchedulingSettingsValues,
} from '@/lib/schemas/schedulingSettings';

import { SliderField } from './_components/SliderField';
import { isAdminRole } from '@/lib/rbac';

// ─────────────────────────────────────────────────────────────────────────
// 時刻ヘルパー: BE は "HH:MM:SS"、<input type="time"> は "HH:MM"
// ─────────────────────────────────────────────────────────────────────────

/** "HH:MM:SS" / "HH:MM" → "HH:MM" (input 表示用)。 */
function toHM(hms: string): string {
  return hms.slice(0, 5);
}

/** "HH:MM" → "HH:MM:SS" (BE 送信用。秒は常に 00)。 */
function toHMS(hm: string): string {
  return /^\d{2}:\d{2}$/.test(hm) ? `${hm}:00` : hm;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export default function SchedulingSettingsPage() {
  const { data: session, status } = useSession();
  const role = session?.user?.role;
  const canEdit = isAdminRole(role);

  // RB (PO決定 2026-07-08): 閲覧は全ロール (PC版の表示統一)。保存系は canEdit で
  // disabled にし、BE の RBAC (PUT=admin/manager) が最終防衛する。

  const settingsQuery = useSchedulingSettings();
  const updateMutation = useUpdateSchedulingSettings();

  const server = settingsQuery.data?.values ?? null;
  const isDefault = settingsQuery.data?.is_default ?? null;

  // サーバ値を draft にコピーして編集する。
  const [draft, setDraft] = useState<SchedulingSettingsValues | null>(null);

  const patch = <K extends keyof SchedulingSettingsValues>(
    key: K,
    value: SchedulingSettingsValues[K],
  ) => setDraft((d) => (d ? { ...d, [key]: value } : d));

  // 変更フィールドのみを payload に積む (省略=不変)。時刻は "HH:MM:SS" に揃える。
  const changedPayload = useMemo<SchedulingSettingsUpdate>(() => {
    if (!draft || !server) return {};
    const out: SchedulingSettingsUpdate = {};
    (Object.keys(draft) as Array<keyof SchedulingSettingsValues>).forEach((k) => {
      if (draft[k] !== server[k]) {
        // 値は draft 由来 (時刻は既に "HH:MM:SS" のまま保持している)。
        (out as Record<string, unknown>)[k] = draft[k];
      }
    });
    return out;
  }, [draft, server]);

  const isDirty = Object.keys(changedPayload).length > 0;

  // サーバ値が変わったら draft を再シードする。ただし未保存編集 (isDirty) が
  // あるときは上書きしない — 背景リフェッチ (ウィンドウ再フォーカス / 保存後
  // invalidate) でユーザーの編集が消えるのを防ぐ。
  //   - 初回 (draft===null) は server で初期化。
  //   - 保存/リセット成功後は server==draft になり isDirty=false なので再シードされ、
  //     最新値が反映される。
  const isDirtyRef = useRef(isDirty);
  isDirtyRef.current = isDirty;
  useEffect(() => {
    if (!server) return;
    setDraft((d) => (d === null || !isDirtyRef.current ? server : d));
  }, [server]);

  const handleSave = async () => {
    if (!isDirty) return;
    try {
      await updateMutation.mutateAsync(changedPayload);
      toast.success('最適化ルールを保存しました（次回の最適化から反映されます）');
    } catch {
      toast.error('保存に失敗しました。入力値をご確認ください');
    }
  };

  // is_default が全項目 true なら既に全既定。無駄な PUT (= audit_log) を避ける。
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
      await updateMutation.mutateAsync(SCHEDULING_SETTINGS_RESET_ALL);
      toast.success('すべて既定値に戻しました');
    } catch {
      toast.error('既定値へのリセットに失敗しました');
    }
  };

  if (status === 'loading') {
    return null;
  }

  return (
    <section className="mx-auto w-full max-w-3xl space-y-6 pb-24">
      <header className="space-y-1">
        <RakusukeTitle
          pose="think"
          title="最適化ルールの設定"
          subtitle={
            <>
              自動スケジュール最適化の前提となる事業所共通のルールです。
              <span className="font-medium text-text-primary">
                変更は次回の最適化から反映されます
              </span>
              （既存の確定スケジュールは変わりません）。
            </>
          }
        />
      </header>

      {settingsQuery.isError && (
        <Alert variant="destructive">
          <AlertDescription>設定の取得に失敗しました。再読み込みしてください。</AlertDescription>
        </Alert>
      )}

      {settingsQuery.isLoading || !draft ? (
        <div className="space-y-4">
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      ) : (
        <>
          {/* ───────────── ⏱ 時間のルール ───────────── */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Clock className="h-5 w-5 text-brand-primary" strokeWidth={1.75} />
                時間のルール
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* ① 訪問間バッファー */}
              <SliderField
                id="visit-buffer"
                label="訪問のあいだの余裕（バッファー）"
                description="次の訪問へ移るときに必ず確保する予備時間です。移動の遅れに備えます。"
                value={draft.visit_buffer_min}
                min={SCHEDULING_RANGES.visit_buffer_min.min}
                max={SCHEDULING_RANGES.visit_buffer_min.max}
                step={SCHEDULING_RANGES.visit_buffer_min.step}
                unit="分"
                isDefault={isDefault?.visit_buffer_min}
                onChange={(n) => patch('visit_buffer_min', n)}
                disabled={!canEdit || updateMutation.isPending}
              />

              {/* ③a 昼休み・標準の長さ */}
              <SliderField
                id="lunch-duration"
                label="昼休みの標準の長さ"
                description="1日の昼休みとして基本的に確保する時間です。"
                liveHint="混んだ日は自動で最低30分まで短縮されます"
                value={draft.lunch_duration_min}
                min={SCHEDULING_RANGES.lunch_duration_min.min}
                max={SCHEDULING_RANGES.lunch_duration_min.max}
                step={SCHEDULING_RANGES.lunch_duration_min.step}
                unit="分"
                isDefault={isDefault?.lunch_duration_min}
                onChange={(n) => patch('lunch_duration_min', n)}
                disabled={!canEdit || updateMutation.isPending}
              />

              {/* ③b 昼休み・取得時間帯 */}
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <Label className="text-sm font-medium text-text-primary">
                    昼休みを取れる時間帯
                  </Label>
                  {isDefault?.lunch_window_start && isDefault?.lunch_window_end && (
                    <span className="shrink-0 rounded-full bg-bg-muted px-2 py-0.5 text-[10px] font-medium text-text-muted">
                      既定
                    </span>
                  )}
                </div>
                <p className="text-xs text-text-secondary">
                  この時間帯のあいだで昼休みを自動的に割り当てます。
                </p>
                <div className="flex items-center gap-2">
                  <Input
                    type="time"
                    aria-label="昼休み開始"
                    data-testid="lunch-window-start"
                    value={toHM(draft.lunch_window_start)}
                    disabled={!canEdit || updateMutation.isPending}
                    onChange={(e) => patch('lunch_window_start', toHMS(e.target.value))}
                    className="h-9 w-32"
                  />
                  <span className="text-text-muted">〜</span>
                  <Input
                    type="time"
                    aria-label="昼休み終了"
                    data-testid="lunch-window-end"
                    value={toHM(draft.lunch_window_end)}
                    disabled={!canEdit || updateMutation.isPending}
                    onChange={(e) => patch('lunch_window_end', toHMS(e.target.value))}
                    className="h-9 w-32"
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          {/* ───────────── 🚗 移動の見積もり ───────────── */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Car className="h-5 w-5 text-brand-primary" strokeWidth={1.75} />
                移動の見積もり
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* ② 移動速度 */}
              <SliderField
                id="travel-speed"
                label="移動の速さ（平均）"
                description="訪問先どうしの移動時間を見積もるための平均速度です。"
                liveHint={`いまの設定だと1kmあたり約${Math.round(60 / draft.travel_speed_kmh)}分の移動時間で計算します`}
                value={draft.travel_speed_kmh}
                min={SCHEDULING_RANGES.travel_speed_kmh.min}
                max={SCHEDULING_RANGES.travel_speed_kmh.max}
                step={SCHEDULING_RANGES.travel_speed_kmh.step}
                unit="km/h"
                isDefault={isDefault?.travel_speed_kmh}
                onChange={(n) => patch('travel_speed_kmh', n)}
                disabled={!canEdit || updateMutation.isPending}
              />
              <p className="flex items-start gap-1.5 text-xs text-text-muted">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
                実際の道のりではなく、地点間の直線距離をもとにした概算です。
              </p>
            </CardContent>
          </Card>

          {/* ───────────── 🏢 営業・コース ───────────── */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Building2 className="h-5 w-5 text-brand-primary" strokeWidth={1.75} />
                営業・コース
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* ④ 営業時間 */}
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <Label className="text-sm font-medium text-text-primary">営業時間</Label>
                  {isDefault?.business_start && isDefault?.business_end && (
                    <span className="shrink-0 rounded-full bg-bg-muted px-2 py-0.5 text-[10px] font-medium text-text-muted">
                      既定
                    </span>
                  )}
                </div>
                <p className="text-xs text-text-secondary">
                  訪問を割り当てられる1日の時間枠です。空き枠の表示にも反映されます。
                </p>
                <div className="flex items-center gap-2">
                  <Input
                    type="time"
                    aria-label="営業開始"
                    data-testid="business-start"
                    value={toHM(draft.business_start)}
                    disabled={!canEdit || updateMutation.isPending}
                    onChange={(e) => patch('business_start', toHMS(e.target.value))}
                    className="h-9 w-32"
                  />
                  <span className="text-text-muted">〜</span>
                  <Input
                    type="time"
                    aria-label="営業終了"
                    data-testid="business-end"
                    value={toHM(draft.business_end)}
                    disabled={!canEdit || updateMutation.isPending}
                    onChange={(e) => patch('business_end', toHMS(e.target.value))}
                    className="h-9 w-32"
                  />
                </div>
              </div>

              {/* ⑤ 1コース最大人数 */}
              <SliderField
                id="max-patients"
                label="1コースの最大人数"
                description="1つのコース（1人のスタッフの1日）で受け持つ患者数の上限です。"
                value={draft.max_patients_per_course}
                min={SCHEDULING_RANGES.max_patients_per_course.min}
                max={SCHEDULING_RANGES.max_patients_per_course.max}
                step={SCHEDULING_RANGES.max_patients_per_course.step}
                unit="人"
                isDefault={isDefault?.max_patients_per_course}
                onChange={(n) => patch('max_patients_per_course', n)}
                disabled={!canEdit || updateMutation.isPending}
              />
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
            disabled={!canEdit || !isDirty || updateMutation.isPending}
            data-testid="save"
          >
            {updateMutation.isPending ? '保存中…' : '保存'}
          </Button>
        </div>
      )}
    </section>
  );
}
