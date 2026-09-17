'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { AlertTriangle, Clock, Link2, Mic, QrCode } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/sonner';
import { CheckInButton } from '@/components/mobile/CheckInButton';
import { MobileEventChip, MobileOverrideBadge } from '@/components/mobile/MobileEventChip';
import { MobileSection } from '@/components/mobile/MobileSection';
import { MobileVisitCard } from '@/components/mobile/MobileVisitCard';
import { PatientPickerSheet } from '@/components/mobile/PatientPickerSheet';
import { QrScanner } from '@/components/mobile/QrScanner';
import { VoiceFailedSheet } from '@/components/mobile/VoiceFailedSheet';
import { RakusukeNote } from '@/components/brand/Rakusuke';
import { extractQrToken } from '@/lib/qr-token';
import { classifyVisitDisplay } from '@/lib/schedule/visitVisibility';
import { foldStaffEvents } from '@/lib/schedule/foldStaffEvents';
import { useCheckinFlush } from '@/lib/queries/checkinFlush';
import {
  useUpdateRecording,
  useVisitRecordings,
  type VisitRecordingRead,
} from '@/lib/queries/visit-recordings';
import { formatElapsed } from '@/lib/voice/recorder';
import { useVoiceFlush } from '@/lib/voice/queue';
import type { PatientRead } from '@/lib/schemas/patient';
import {
  addDays,
  todayIso,
  useMyOverrides,
  useMyStaffEvents,
  useMyVisits,
  type MyVisit,
} from '@/lib/queries/me';
import type { EventRead } from '@/lib/schemas/staff-events';

function isUnvisited(v: MyVisit): boolean {
  return v.status === 'planned' || v.status === '';
}

/**
 * 要紐付けバナーの遡り幅 (日・レビュー L-6)。
 *
 * 本人に出すのは「まだ覚えている」範囲だけ。これより古い未紐付けは admin が
 * `/records` の「要紐付け」で片付ける (設計 §2-1: 24 時間紐付け無しは admin 画面へ)。
 */
const UNLINKED_LOOKBACK_DAYS = 14;

/** 今日の 1 行 = 訪問カード or イベントチップ (design §3 C-3)。 */
type TodayRow =
  | { kind: 'visit'; at: string; visit: MyVisit }
  | { kind: 'event'; at: string; event: EventRead };

/** "HH:MM:SS" も "HH:MM" も HH:MM に揃える (時刻欠落の行でも落ちない)。 */
function sortKey(t: string | null | undefined): string {
  if (typeof t !== 'string') return '';
  return t.length >= 5 ? t.slice(0, 5) : t;
}

/** ISO 日時 → `M/D HH:MM` (端末ローカル)。 */
function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${hh}:${mm}`;
}

/** 要約の 1 行目 (まだ無ければ状態の言葉)。 */
function summaryLine(r: VisitRecordingRead): string {
  const text = (r.summary_text ?? '').trim();
  if (text) return text.split('\n')[0] ?? '';
  return r.status === 'failed' ? '要約に失敗しました' : 'らく助が文字起こし中です';
}

/**
 * 要紐付けの録音を並べ、その場で患者を選ぶシート (設計 §11-1)。
 *
 * 「予定に無い訪問を記録」で患者を選ばずに保存した録音の受け皿。行を選ぶと
 * `PatientPickerSheet` に切り替わり、選んだ患者で
 * `PATCH /visit-recordings/{id} {patient_id}` を投げる。**訪問は作らない**
 * (2026-09-18 決定・設計 §10-3)。
 */
function UnlinkedRecordingsSheet({
  open,
  onOpenChange,
  rows,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  rows: VisitRecordingRead[];
}) {
  const [picking, setPicking] = useState<VisitRecordingRead | null>(null);
  const update = useUpdateRecording(picking?.id ?? '');

  // 閉じたら選択中の行を忘れる (次に開いたら一覧から始める)。
  const change = (next: boolean) => {
    if (!next) setPicking(null);
    onOpenChange(next);
  };

  const handlePick = (patient: PatientRead) => {
    if (!picking || update.isPending) return;
    update.mutate(
      { patient_id: patient.id },
      {
        onSuccess: () => {
          toast.success(`${patient.name}様の記録として保存しました`);
          setPicking(null);
          onOpenChange(false);
        },
        onError: (err) => {
          toast.error('患者を紐付けできませんでした', {
            description: err instanceof Error ? err.message : String(err),
          });
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={change}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {picking ? 'この記録はどなたの訪問ですか？' : '患者の紐付けが済んでいない録音'}
          </DialogTitle>
          <DialogDescription>
            {picking
              ? `録音 ${fmtDateTime(picking.recorded_at)}${
                  picking.duration_sec ? `（${formatElapsed(picking.duration_sec)}）` : ''
                }`
              : '録音した記録に患者を紐付けてください。'}
          </DialogDescription>
        </DialogHeader>

        {picking ? (
          <PatientPickerSheet onPick={handlePick} disabled={update.isPending} />
        ) : (
          <div className="space-y-2">
            {rows.length === 0 && (
              <p className="text-sm text-text-secondary">要紐付けの録音はありません。</p>
            )}
            {rows.map((r) => (
              <div
                key={r.id}
                className="flex items-center gap-2 rounded-md border border-border-default p-3"
                data-testid={`unlinked-row-${r.id}`}
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-bold text-text-primary">
                    {fmtDateTime(r.recorded_at)}
                    {r.duration_sec ? `・${formatElapsed(r.duration_sec)}` : ''}
                  </p>
                  <p className="truncate text-xs text-text-secondary">{summaryLine(r)}</p>
                </div>
                <Button type="button" variant="outline" size="sm" onClick={() => setPicking(r)}>
                  患者を選ぶ
                </Button>
              </div>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function MobileTodayPage() {
  const today = todayIso();
  // 要紐付けバナーの遡り幅 (日)。
  const unlinkedFrom = addDays(today, -UNLINKED_LOOKBACK_DAYS);
  const router = useRouter();
  const {
    data: visits,
    isLoading,
    isError,
    error,
  } = useMyVisits({
    date: today,
  });

  // 患者ステータス連動の取消 (source='status_cancel') は一覧から消す
  // (design 2026-09-09 §7-4)。「今週だけ取消」= manual_cancel は従来どおり残す。
  const sorted = (visits ?? [])
    .filter((v) => classifyVisitDisplay(v) !== 'hidden')
    .sort((a, b) => a.start_time.localeCompare(b.start_time));

  // 職員イベント / 休み・時間変更も当日分を取る。**補助情報**なので取得に失敗
  // しても Alert は出さず、訪問だけ静かに描く (design §3 C-3)。
  const range = { from: today, to: today };
  const { data: events } = useMyStaffEvents(range);
  const { data: overrides } = useMyOverrides(range);
  const todayOverride = (overrides ?? []).find((o) => o.date === today) ?? null;

  // 訪問とイベントを開始時刻順に混ぜる (同時刻なら訪問が先 = 現場の主役)。
  const rows: TodayRow[] = [
    ...sorted.map((v) => ({ kind: 'visit' as const, at: sortKey(v.start_time), visit: v })),
    ...foldStaffEvents(events ?? []).map((e) => ({
      kind: 'event' as const,
      at: sortKey(e.start_time),
      event: e,
    })),
  ].sort(
    (a, b) => a.at.localeCompare(b.at) || (a.kind === b.kind ? 0 : a.kind === 'visit' ? -1 : 1),
  );

  // 圏外で退避した打刻 (訪問詳細の到着/退出・/q の予定外) をここで再送する。
  // 一覧は退避後に必ず戻ってくる場所なので、「電波が戻り次第、自動で送信します」の
  // 主トリガーになる (マウント時 + online イベント)。
  const { pendingCount: checkinPending } = useCheckinFlush();
  // 音声も同じ場所で再送し、バナーの件数に合算する (設計 §10-5)。
  // 送れなかった録音 (4xx) は別バナー — 自動では直らないので本人の判断が要る。
  const {
    pendingCount: voicePending,
    failedCount: voiceFailed,
    refreshPending: refreshVoicePending,
  } = useVoiceFlush();
  const pendingCount = checkinPending + voicePending;
  const [failedOpen, setFailedOpen] = useState(false);

  // 🎙 マーク用。`recordings_count` は API に無いので、今日ぶんの記録を 1 回引いて
  // visit_id の集合で判定する。1 日の訪問数を十分に超える 200 件で引き
  // (レビュー M-1)、取り切れないときは警告だけ出す (`fields=` は BE 未対応)。
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? null;
  const { data: recordingList } = useVisitRecordings({
    staffId,
    from: today,
    to: today,
    limit: 200,
  });
  const recordedVisitIds = new Set(
    (recordingList?.items ?? []).map((r) => r.visit_id).filter((id): id is string => !!id),
  );
  // 患者を選ばずに保存した録音 (設計 §11-1)。バナー → シートで患者を選ぶ。
  // **直近 14 日ぶんだけ**を本人に出す (レビュー L-6): 現場の記憶が残っている
  // うちが紐付けの勝負どころで、それより古い分は admin が /records で対処する
  // (24 時間紐付け無しは admin 画面に出る)。古い残骸をいつまでも本人に見せると、
  // バナーが常設化して「今日ぶん」の気づきが埋もれる。
  const { data: unlinkedList } = useVisitRecordings({
    staffId,
    status: 'unlinked',
    from: unlinkedFrom,
    limit: 50,
  });
  const unlinkedRows = unlinkedList?.items ?? [];
  const [unlinkedOpen, setUnlinkedOpen] = useState(false);

  const recordingTotal = recordingList?.total ?? 0;
  const recordingLoaded = recordingList?.items.length ?? 0;
  useEffect(() => {
    if (recordingTotal > recordingLoaded) {
      console.warn('[visit-recordings] 🎙 マークが一部欠けます (取得上限)', {
        total: recordingTotal,
        loaded: recordingLoaded,
      });
    }
  }, [recordingTotal, recordingLoaded]);

  // 本日の担当訪問が無い患者 (担当外・予定外) の QR を読むための独立入口。
  // 読み取ったら振り分けは既存の `/q/{token}` に全部任せる (担当 visit 直行 /
  // 代行 / 予定外の選択 / エラー案内)。この画面は「読んで飛ばす」だけに徹する。
  const [scanning, setScanning] = useState(false);

  const handleScanned = useCallback(
    (raw: string) => {
      // QrScanner 側でも抽出済みだが、生トークン/URL のどちらで来ても壊れないよう
      // ここでも通す (extractQrToken は生トークンを素通しする冪等な関数)。
      const token = extractQrToken(raw);
      setScanning(false);
      if (!token) {
        toast.error('らく助のQRではありません', {
          description: '患者宅の玄関にあるQRコードを読み取ってください',
        });
        return;
      }
      router.push(`/q/${encodeURIComponent(token)}`);
    },
    [router],
  );

  // 全画面スキャナ (訪問詳細と同じ流儀で、開いている間は一覧を差し替える)。
  // 手動フォールバック (onManual) は渡さない = 担当外は QR 必須 (設計 決定#6)。
  if (scanning) {
    return (
      <QrScanner
        targetLabel="担当外・予定外の訪問"
        onScan={handleScanned}
        onCancel={() => setScanning(false)}
      />
    );
  }

  return (
    <MobileSection
      pose="visit"
      title="今日の訪問"
      subtitle={`${today} ・ ${sorted.length}件`}
      /* 休み / 時間変更は見出しの右へ (design §3 C-3)。 */
      action={
        todayOverride ? (
          <MobileOverrideBadge override={todayOverride} testId="today-override-badge" />
        ) : undefined
      }
    >
      {pendingCount > 0 && (
        <div
          className="flex items-center gap-2 rounded-md bg-warning/10 px-3 py-2 text-xs text-warning"
          data-testid="today-pending-banner"
        >
          <Clock className="h-3.5 w-3.5 shrink-0" />
          未送信 {pendingCount} 件・電波が戻ると自動で送信します
        </div>
      )}

      {/* 送れなかった録音 (4xx) — 自動では送られない。音声は端末に残っている。 */}
      {voiceFailed > 0 && (
        <button
          type="button"
          className="flex w-full items-center gap-2 rounded-md bg-error-bg px-3 py-2 text-left text-xs text-error"
          data-testid="today-voice-failed-banner"
          onClick={() => setFailedOpen(true)}
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          送れなかった録音 {voiceFailed} 件・タップして確認
        </button>
      )}

      {/* 患者の紐付け待ち — 録音した本人にしか分からないので、その日のうちに。 */}
      {unlinkedRows.length > 0 && (
        <button
          type="button"
          className="flex w-full items-center gap-2 rounded-md bg-warning-bg px-3 py-2 text-left text-xs text-warning"
          data-testid="today-unlinked-banner"
          onClick={() => setUnlinkedOpen(true)}
        >
          <Link2 className="h-3.5 w-3.5 shrink-0" />
          <span>
            要紐付け {unlinkedRows.length} 件・タップして患者を選ぶ
            <span className="block opacity-80">
              直近 {UNLINKED_LOOKBACK_DAYS} 日ぶんです。古い分は管理者が対応します。
            </span>
          </span>
        </button>
      )}

      <VoiceFailedSheet
        open={failedOpen}
        onOpenChange={setFailedOpen}
        onChanged={() => void refreshVoicePending()}
      />

      <UnlinkedRecordingsSheet
        open={unlinkedOpen}
        onOpenChange={setUnlinkedOpen}
        rows={unlinkedRows}
      />

      {/* 一覧の状態 (読込中/エラー/0件) に関わらず常に出す — 予定に無い訪問こそ
          「本日の患者訪問はありません」の画面から入ることが多い。 */}
      <div className="space-y-1.5">
        <CheckInButton tone="outline" onClick={() => setScanning(true)}>
          <QrCode className="h-5 w-5" />
          QRを読み取る
        </CheckInButton>
        <p className="text-center text-xs text-text-muted">
          予定に無い訪問・担当外の訪問はこちらから
        </p>
        {/* QR が無い予定外訪問 (設計 §2-1 導線 C)。先に録音し、患者は後で選ぶ。 */}
        <CheckInButton
          tone="outline"
          data-testid="today-record-new"
          onClick={() => router.push('/m/record/new')}
        >
          <Mic className="h-5 w-5" />
          予定に無い訪問を記録
        </CheckInButton>
      </div>

      {isLoading && (
        <div className="space-y-2">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      )}

      {isError && (
        <Alert variant="destructive">
          <AlertTitle>取得に失敗しました</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      )}

      {/* 空表示は「訪問もイベントも無い」ときだけ (2026-09-16 MEDIUM-9)。
          訪問 0 件でも研修・会議があれば、その日は働く日 —
          「本日の訪問はありません」の下にイベントが並ぶのは矛盾した画面。 */}
      {!isLoading && !isError && rows.length === 0 && (
        <Card className="p-6">
          <RakusukeNote
            pose="joy"
            title="本日の患者訪問はありません"
            comment="おつかれさまでした！ゆっくり休んでくださいね"
          />
        </Card>
      )}

      <div className="space-y-2">
        {rows.map((row) =>
          row.kind === 'event' ? (
            <MobileEventChip key={`ev-${row.event.id}`} event={row.event} />
          ) : (
            <MobileVisitCard
              key={row.visit.id}
              visit={row.visit}
              highlight={isUnvisited(row.visit)}
              hasRecording={recordedVisitIds.has(row.visit.id)}
            />
          ),
        )}
      </div>
    </MobileSection>
  );
}
