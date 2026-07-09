'use client';

/**
 * usePatientSexMap — 差分/取り込みビューのカードを本体スケジュールと同じ
 * 性別ウォッシュ意匠で塗るための patientId → sex マップ (FE join)。
 *
 * CorrectionItem には patient_sex が無い (BE は additive 加算しない方針) ため、
 * 本体スケジュールと同じ `usePatients({ limit: 500 })` クエリ (= 同一クエリキーで
 * キャッシュ共有) を FE 側で join して sex を解決する。連携ページは admin 限定なので
 * patients 一覧取得の RBAC 問題は無い。patient_id が null (名寄せ未解決) の item は
 * マップに無く、呼び出し側で genderPalette(null) の中立ウォッシュになる。
 */
import { useMemo } from 'react';

import { usePatients } from '@/lib/queries/patients';

export function usePatientSexMap(): Map<string, string | null | undefined> {
  const patients = usePatients({ limit: 500 });
  return useMemo(() => {
    const m = new Map<string, string | null | undefined>();
    for (const p of patients.data?.items ?? []) {
      m.set(p.id, p.sex);
    }
    return m;
  }, [patients.data?.items]);
}
