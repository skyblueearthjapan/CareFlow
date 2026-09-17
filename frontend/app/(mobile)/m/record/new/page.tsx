'use client';

/**
 * 予定に無い訪問を記録（設計 §2-1 導線 C・§11-1・モック ④）。
 *
 * QR の無い予定外訪問のための画面。**先に録音を始められる**ことが肝で、患者を
 * 探す操作で録音開始を遅らせない。停止・保存したあとに「この記録はどなたの訪問
 * ですか？」へ進み、今日/今週の担当チップ・検索・あいうえお順から選ぶ。
 * 選ばなくてもよい（「あとで紐付ける」＝ `unlinked` のまま。`/m/today` の
 * 「要紐付け」バナーからいつでも選べる）。
 *
 * 紐付けは `PATCH /visit-recordings/{id} {patient_id}`。**訪問は作らない**
 * （2026-09-18 決定・設計 §10-3）。同じ日に既存の訪問があれば BE がそれを
 * 拾い、無ければ `visit_id` は空のまま残る。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { AlertTriangle, ArrowLeft } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { toast } from '@/components/ui/sonner';
import { MobileSection } from '@/components/mobile/MobileSection';
import { PatientPickerSheet } from '@/components/mobile/PatientPickerSheet';
import { VoiceRecorderPanel, type VoiceSavedInfo } from '@/components/mobile/VoiceRecorderPanel';
import { currentWeekStartIso, todayIso, useMyVisits, type MyVisit } from '@/lib/queries/me';
import { useUpdateRecording } from '@/lib/queries/visit-recordings';
import { useVoiceFlush } from '@/lib/voice/queue';
import { rescueVoiceSessions } from '@/lib/voice/session';
import type { PatientRead } from '@/lib/schemas/patient';

type Step = 'record' | 'pick';

function ignoreRescueError(): void {
  /* noop */
}

/** 今日/今週の自分の担当患者（重複排除・名前順）。 */
function ownPatientIds(...lists: Array<MyVisit[] | undefined>): string[] {
  const byId = new Map<string, string>();
  for (const list of lists) {
    for (const v of list ?? []) {
      if (!v.patient_id) continue;
      if (!byId.has(v.patient_id)) byId.set(v.patient_id, v.patient_name ?? '');
    }
  }
  return Array.from(byId.entries())
    .sort((a, b) => a[1].localeCompare(b[1], 'ja'))
    .map(([id]) => id);
}

export default function MobileRecordNewPage() {
  const router = useRouter();
  const pathname = usePathname();
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? '';

  const [step, setStep] = useState<Step>('record');

  // 未送信の音声はこの画面でも再送する（救出で積んだ分の件数を数え直すため）。
  const { refreshPending: refreshVoicePending } = useVoiceFlush();

  /**
   * 録音セッションの救出（レビュー H-C・訪問詳細 `[visitId]/page.tsx` と同型）。
   *
   * 録音は `VoiceRecorderPanel` ではなく `lib/voice/session.ts` が持つので、
   * パネルの unmount では止めない。止めて未送信キューへ積むのは**ページを離れる
   * とき**だけ: この effect の cleanup（unmount / パス変更）と `pagehide` /
   * `visibilitychange`(hidden)。`beforeunload` には頼らない（iOS で発火しない）。
   */
  const staffIdRef = useRef(staffId);
  staffIdRef.current = staffId;
  useEffect(() => {
    const rescue = () => {
      void rescueVoiceSessions(staffIdRef.current).then((saved) => {
        if (saved > 0) {
          toast.success(`録音 ${saved} 件を保存しました（画面を離れたため自動保存）`);
          void refreshVoicePending();
        }
      }, ignoreRescueError);
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') rescue();
    };
    window.addEventListener('pagehide', rescue);
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      window.removeEventListener('pagehide', rescue);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      rescue();
    };
  }, [pathname, refreshVoicePending]);

  // チップ用: 今日と今週の自分の担当患者（今日を先に見るので今日ぶんが上に来る）。
  const { data: todayVisits } = useMyVisits({ date: todayIso() });
  const { data: weekVisits } = useMyVisits({ weekStart: currentWeekStartIso() });
  const recentPatientIds = useMemo(
    () => ownPatientIds(todayVisits, weekVisits),
    [todayVisits, weekVisits],
  );

  /**
   * 紐付け先の録音（保存応答が返した id）。
   *
   * 「自分の未紐付け一覧のいちばん新しい行」で代用しない（2026-09-18 是正）—
   * 続けて 2 本録れば取り違える。圏外でキューに残った（`queued`）ときは
   * サーバにまだ無いので、紐付けを促さず「あとで紐付ける」と同じ案内に落とす。
   */
  const [saved, setSaved] = useState<VoiceSavedInfo | null>(null);
  const update = useUpdateRecording(saved?.recordingId ?? '');

  const handleSaved = useCallback(
    (info: VoiceSavedInfo) => {
      setSaved(info);
      setStep('pick');
      void refreshVoicePending();
    },
    [refreshVoicePending],
  );

  const handlePick = useCallback(
    (patient: PatientRead) => {
      if (!saved?.recordingId || update.isPending) return;
      update.mutate(
        { patient_id: patient.id },
        {
          onSuccess: (rec) => {
            toast.success(`${patient.name}様の記録として保存しました`);
            router.push(rec.visit_id ? `/m/today/${rec.visit_id}` : '/m/today');
          },
          onError: (err) => {
            toast.error('患者を紐付けできませんでした', {
              description: err instanceof Error ? err.message : String(err),
            });
          },
        },
      );
    },
    [saved, update, router],
  );

  const skip = useCallback(() => {
    toast.warning('あとで紐付けられます', {
      description: '「今日の訪問」の「要紐付け」からいつでも患者を選べます',
    });
    router.push('/m/today');
  }, [router]);

  if (step === 'pick') {
    return (
      <MobileSection pose="visit" title="この記録はどなたの訪問ですか？">
        {saved?.recordingId ? (
          <p className="text-sm text-text-secondary" data-testid="record-new-target">
            録音を保存しました。どなたの訪問かを選んでください。
          </p>
        ) : (
          <Card className="space-y-2 p-4" data-testid="record-new-not-sent">
            <p className="flex items-center gap-2 text-sm font-bold text-warning">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              録音はまだ送信されていません
            </p>
            <p className="text-sm text-text-secondary">
              音声は端末に残しています。電波が戻って送信されたら、「今日の訪問」の
              「要紐付け」から患者を選べます。
            </p>
          </Card>
        )}

        {saved?.recordingId && (
          <PatientPickerSheet
            onPick={handlePick}
            recentPatientIds={recentPatientIds}
            disabled={update.isPending}
          />
        )}

        <button
          type="button"
          onClick={skip}
          data-testid="record-new-skip"
          className="w-full py-2 text-center text-sm text-text-secondary underline"
        >
          あとで紐付ける（「今日の訪問」から選べます）
        </button>
      </MobileSection>
    );
  }

  return (
    <MobileSection pose="visit" title="予定に無い訪問を記録">
      <div className="flex items-center justify-between gap-2">
        <span
          className="inline-flex items-center gap-1.5 rounded-md bg-error-bg px-2.5 py-1 text-xs font-bold text-error"
          data-testid="record-new-unlinked-badge"
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          患者未確定
        </span>
        <Link
          href="/m/today"
          className="inline-flex items-center gap-1 text-sm text-text-secondary"
        >
          <ArrowLeft className="h-4 w-4" />
          今日の訪問
        </Link>
      </div>

      <p className="text-sm text-text-secondary">
        先に録音を始められます。停止して保存したあとに、どなたの訪問かを選んでください。
      </p>

      <VoiceRecorderPanel patientName="（未確定）" heading="音声記録" onSaved={handleSaved} />
    </MobileSection>
  );
}
