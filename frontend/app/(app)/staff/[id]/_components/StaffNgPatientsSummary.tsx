'use client';

/**
 * NG 指定サマリ (閲覧専用) — `docs/plans/patient-ng-staff-design.md` §8-2 Phase 2。
 *
 * 「このスタッフを NG 指定している患者」を逆引きで一覧する。
 * 編集は患者マスタ側 (PatientNgStaffSection) が正典なので、ここは表示 +
 * 患者詳細への誘導リンクのみ (手本 = `TraineeAccompanimentSummary` の流儀)。
 *
 * 0 件 (= 大多数のスタッフ) では**カードごと非表示**にしたいので、Card の枠も
 * 本コンポーネントが持つ。呼び出し側は無条件に置くだけでよい。
 */
import Link from 'next/link';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useStaffNgPatients } from '@/lib/queries/patient_ng_staff';

export function StaffNgPatientsSummary({ staffId }: { staffId: string }) {
  const { data, isLoading, isError, error } = useStaffNgPatients(staffId);

  // 読み込み中も 0 件と区別できないため、確定するまでは何も出さない
  // (= カードがちらついて出入りするのを避ける)。
  if (isLoading) return null;

  if (isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>NG 指定されている患者</CardTitle>
        </CardHeader>
        <CardContent>
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    );
  }

  const rows = data ?? [];
  // 0 件なら丸ごと非表示 (= 大多数のスタッフで余計な枠を出さない)。
  if (rows.length === 0) return null;

  return (
    <Card data-testid="staff-ng-patients-summary">
      <CardHeader>
        <CardTitle>NG 指定されている患者</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-text-secondary">
          以下の患者様は、このスタッフを「NGスタッフ」に指定しています。自動割当では候補から
          除外され、手動で割り当てる場合は確認が入ります。設定の変更は患者マスタから行います。
        </p>

        <table className="w-full text-sm">
          <thead className="border-b border-border-default text-left text-text-secondary">
            <tr>
              <th className="px-3 py-2 font-medium">患者</th>
              <th className="px-3 py-2 font-medium">理由メモ</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.patient_id} className="border-b border-border-default last:border-0">
                <td className="px-3 py-2">
                  <Link
                    href={`/patients/${r.patient_id}`}
                    className="text-brand-primary underline-offset-2 hover:underline"
                  >
                    {r.patient_name ?? '(氏名未登録)'}
                  </Link>
                </td>
                <td className="px-3 py-2 text-text-secondary">{r.note ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
