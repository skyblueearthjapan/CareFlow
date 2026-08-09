'use client';

/**
 * /patients/qr-print — 患者 QR 印刷ビュー (Phase 5-1)。
 *
 * `docs/mockups/qr-checkin/qr-print.html` の本実装。1 名 1 枚・二つ折り A4。
 *   - 個別モード (`?mode=single&patient={id}`): 患者 1 名 (A4 1 枚)。
 *   - 一括モード (`?mode=bulk`): 拠点で絞り込み + チェックで選択し各 A4 1 枚。
 *
 * 上半分 = 掲示面 (患者名 / コード / 拠点 / 大 QR / 案内)、折り線 (物理中央 top:50%)、
 * 下半分 = 説明面 (ご家族向け説明 / 注意 / 発行日 + qr_version 印字)。
 * QR は `qrcode.react` で `${origin}/q/${token}` を符号化。
 * admin / manager のみ (非該当は /dashboard リダイレクト)。
 */

import { Suspense, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { QRCodeSVG } from 'qrcode.react';

import { Skeleton } from '@/components/ui/skeleton';
import { usePatients } from '@/lib/queries/patients';
import { useOffices } from '@/lib/queries/offices';
import { usePatientQr } from '@/lib/queries/patientQr';
import type { PatientRead } from '@/lib/schemas/patient';

import './qr-print.css';
import { isAdminRole } from '@/lib/rbac';

type Mode = 'single' | 'bulk';

/**
 * 一括印刷の対象上限。これを超える選択は GET 殺到 (1 名 1 リクエスト) と
 * 全 A4 描画で画面が重くなるため、選択数をこの値で頭打ちにし、超過時は拠点
 * 絞り込みを促す警告を出す。
 */
const BULK_PRINT_LIMIT = 60;

/** 今日 (JST) の YYYY-MM-DD。 */
function todayJst(): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(new Date());
}

/** QR に符号化する URL を組む (`${origin}/q/${token}`)。 */
function qrUrl(token: string): string {
  const origin = typeof window !== 'undefined' ? window.location.origin : '';
  return `${origin}/q/${token}`;
}

export default function QrPrintPage() {
  // useSearchParams は Suspense 境界が必須 (Next 15 の CSR bailout 対策)。
  return (
    <Suspense
      fallback={
        <section className="space-y-4 p-6">
          <Skeleton className="h-10 w-1/2" />
          <Skeleton className="h-[400px] w-full" />
        </section>
      }
    >
      <QrPrintPageInner />
    </Suspense>
  );
}

function QrPrintPageInner() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const searchParams = useSearchParams();
  const role = session?.user?.role;
  const canView = isAdminRole(role);

  useEffect(() => {
    if (status === 'authenticated' && !canView) {
      router.replace('/dashboard');
    }
  }, [status, canView, router]);

  const initialMode: Mode = searchParams?.get('mode') === 'bulk' ? 'bulk' : 'single';
  const urlPatientId = searchParams?.get('patient') ?? '';

  const [mode, setMode] = useState<Mode>(initialMode);
  const [office, setOffice] = useState<string>('all');
  const [selectedPatientId, setSelectedPatientId] = useState<string>(urlPatientId);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data: patientsData, isLoading } = usePatients({ limit: 500 });
  const { offices } = useOffices({ limit: 500 });

  const officeNameMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const o of offices ?? []) m.set(o.id, o.name);
    return m;
  }, [offices]);

  // 削除済みを除いた有効な患者のみ対象。
  const allPatients = useMemo<PatientRead[]>(
    () => (patientsData?.items ?? []).filter((p) => !p.deleted_at),
    [patientsData],
  );

  // 患者が紐づく拠点だけをチップに出す (全拠点 + 実在する拠点)。
  const officeChips = useMemo<Array<{ id: string; name: string }>>(() => {
    const ids = new Set<string>();
    for (const p of allPatients) {
      if (p.primary_office_id) ids.add(p.primary_office_id);
    }
    return Array.from(ids).map((id) => ({ id, name: officeNameMap.get(id) ?? id }));
  }, [allPatients, officeNameMap]);

  // 一括: 拠点で絞った表示対象。
  const shownPatients = useMemo<PatientRead[]>(
    () =>
      office === 'all' ? allPatients : allPatients.filter((p) => p.primary_office_id === office),
    [allPatients, office],
  );

  // 単一モードの初期 selectedPatientId を、未指定なら先頭患者に補完。
  useEffect(() => {
    const first = allPatients[0];
    if (mode === 'single' && !selectedPatientId && first) {
      setSelectedPatientId(first.id);
    }
  }, [mode, selectedPatientId, allPatients]);

  // 一括モードに入った時点で表示中の患者を全選択 (初期状態)。上限までに頭打ち。
  useEffect(() => {
    if (mode === 'bulk') {
      setSelected((prev) => {
        if (prev.size > 0) return prev;
        return new Set(shownPatients.slice(0, BULK_PRINT_LIMIT).map((p) => p.id));
      });
    }
  }, [mode, shownPatients]);

  // 選択トグル。追加方向は上限ガード (これ以上は GET 殺到/重DOM を避ける)。
  const toggleSelected = (id: string, next: boolean) =>
    setSelected((prev) => {
      const copy = new Set(prev);
      if (next) {
        if (copy.size >= BULK_PRINT_LIMIT) return prev;
        copy.add(id);
      } else {
        copy.delete(id);
      }
      return copy;
    });

  if (status !== 'authenticated' || !canView) {
    return null;
  }

  if (isLoading) {
    return (
      <section className="space-y-4 p-6">
        <Skeleton className="h-10 w-1/2" />
        <Skeleton className="h-[400px] w-full" />
      </section>
    );
  }

  const singlePatient = allPatients.find((p) => p.id === selectedPatientId) ?? null;
  const bulkSelectedPatients = shownPatients.filter((p) => selected.has(p.id));
  // 表示数が上限を超える = 全員ぶんは刷れない。絞り込みを促す。
  const overLimit = mode === 'bulk' && shownPatients.length > BULK_PRINT_LIMIT;

  const issued = todayJst();

  return (
    <div className="qrprint-root">
      {/* ツールバー (画面のみ) */}
      <div className="qrprint-toolbar">
        <div>
          <div className="qrprint-crumb">
            {mode === 'single' ? '患者マスタ › 患者詳細 › QR印刷' : '患者マスタ › QR一括印刷'}
          </div>
          <h1 className="qrprint-title">患者QR 印刷</h1>
        </div>
        <span className="qrprint-sep" />
        <div className="qrprint-seg">
          <button
            type="button"
            className={mode === 'single' ? 'on' : ''}
            onClick={() => setMode('single')}
          >
            個別（患者詳細から1名）
          </button>
          <button
            type="button"
            className={mode === 'bulk' ? 'on' : ''}
            onClick={() => setMode('bulk')}
          >
            一括（一覧から全員）
          </button>
        </div>
        <span className="qrprint-sep" />
        {mode === 'single' ? (
          <span>
            患者：
            <select
              className="qrprint-select"
              value={selectedPatientId}
              onChange={(e) => setSelectedPatientId(e.target.value)}
            >
              {allPatients.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}（{p.code}
                  {p.primary_office_id ? `・${officeNameMap.get(p.primary_office_id) ?? ''}` : ''}）
                </option>
              ))}
            </select>
          </span>
        ) : (
          <span className="qrprint-chips">
            <span
              className={`qrprint-chip${office === 'all' ? ' on' : ''}`}
              onClick={() => setOffice('all')}
            >
              全拠点
            </span>
            {officeChips.map((o) => (
              <span
                key={o.id}
                className={`qrprint-chip${office === o.id ? ' on' : ''}`}
                onClick={() => setOffice(o.id)}
              >
                {o.name}
              </span>
            ))}
          </span>
        )}
        <span className="qrprint-spacer" />
        <span className="qrprint-count">
          {mode === 'single'
            ? '印刷 1枚'
            : `表示 ${shownPatients.length}名 / 印刷 ${bulkSelectedPatients.length}枚`}
        </span>
        {mode === 'bulk' ? (
          <>
            <button
              type="button"
              className="qrprint-btn"
              onClick={() =>
                setSelected(new Set(shownPatients.slice(0, BULK_PRINT_LIMIT).map((p) => p.id)))
              }
            >
              全選択
            </button>
            <button type="button" className="qrprint-btn" onClick={() => setSelected(new Set())}>
              全解除
            </button>
          </>
        ) : null}
        <button type="button" className="qrprint-btn brand" onClick={() => window.print()}>
          🖨 印刷
        </button>
      </div>
      <div className="qrprint-hint">
        {mode === 'single'
          ? '💡 個別モード：選んだ患者 1 名分（A4 1 枚）を印刷・再発行します。'
          : '💡 一括モード：拠点で絞り込み、チェックで対象を選び 全員分を各 A4 1 枚ずつ 印刷します。'}
      </div>

      {overLimit ? (
        <div className="qrprint-warn" role="alert">
          ⚠ 表示 {shownPatients.length}名 は印刷上限 {BULK_PRINT_LIMIT}件
          を超えています。拠点で絞り込んでから印刷してください（選択は最大 {BULK_PRINT_LIMIT}
          名まで）。
        </div>
      ) : null}

      <div className="qrprint-pages" data-testid="qrprint-pages">
        {mode === 'single' ? (
          singlePatient ? (
            <QrSheet
              key={singlePatient.id}
              patient={singlePatient}
              officeName={
                singlePatient.primary_office_id
                  ? (officeNameMap.get(singlePatient.primary_office_id) ?? null)
                  : null
              }
              issued={issued}
              showCheckbox={false}
              checked
              onToggle={() => {}}
            />
          ) : (
            <p className="qrprint-empty">対象の患者がいません。</p>
          )
        ) : shownPatients.length === 0 ? (
          <p className="qrprint-empty">対象の患者がいません。</p>
        ) : (
          // 一括: 選択済みのみ「全 A4 シート」を描画 (= GET も描画量も選択数に比例)。
          // 未選択は氏名＋チェックのみのコンパクト行に留め、重 DOM と GET 殺到を防ぐ。
          shownPatients.map((p) => {
            const officeName = p.primary_office_id
              ? (officeNameMap.get(p.primary_office_id) ?? null)
              : null;
            return selected.has(p.id) ? (
              <QrSheet
                key={p.id}
                patient={p}
                officeName={officeName}
                issued={issued}
                showCheckbox
                checked
                onToggle={(next) => toggleSelected(p.id, next)}
              />
            ) : (
              <QrSelectRow
                key={p.id}
                patient={p}
                officeName={officeName}
                onToggle={(next) => toggleSelected(p.id, next)}
              />
            );
          })
        )}
      </div>
    </div>
  );
}

interface QrSheetProps {
  patient: PatientRead;
  officeName: string | null;
  issued: string;
  showCheckbox: boolean;
  checked: boolean;
  onToggle: (next: boolean) => void;
}

/** 患者 1 名分の A4 シート (二つ折り)。QR は遅延発行 API から取得する。 */
function QrSheet({ patient, officeName, issued, showCheckbox, checked, onToggle }: QrSheetProps) {
  // チェックが外れている (印刷対象外) シートは QR をフェッチしない。
  const { data: qr, isLoading, isError } = usePatientQr(patient.id, { enabled: checked });

  return (
    <div
      className={`qrprint-sheet${checked ? '' : ' off'}`}
      data-testid="qrprint-sheet"
      data-patient-id={patient.id}
    >
      {showCheckbox ? (
        <input
          type="checkbox"
          className="qrprint-chk"
          checked={checked}
          aria-label={`${patient.name} を印刷対象にする`}
          onChange={(e) => onToggle(e.target.checked)}
        />
      ) : null}

      {/* 掲示面 (上半分) */}
      <div className="qrprint-panel front">
        {officeName ? <span className="qrprint-office">{officeName}拠点</span> : null}
        <div className="qrprint-pname">{patient.name} 様</div>
        <div className="qrprint-pcode">{patient.code}</div>
        <div className="qrprint-qrbox">
          {qr ? (
            <QRCodeSVG
              value={qrUrl(qr.token)}
              size={190}
              level="M"
              fgColor="#1c1917"
              bgColor="#ffffff"
              data-testid="qrprint-qr"
            />
          ) : isError ? (
            <div className="qrprint-qr-placeholder">QR取得失敗</div>
          ) : (
            <div className="qrprint-qr-placeholder">{isLoading ? 'QR生成中…' : '—'}</div>
          )}
        </div>
        <div className="qrprint-lead">
          訪問のたびに、このQRコードを
          <br />
          スタッフが読み取って記録します
        </div>
        <div className="qrprint-brand">♥ CareFlow 訪問チェックイン</div>
      </div>

      {/* 折り線 (物理中央 top:50%) */}
      <div className="qrprint-fold">
        <span>✂ ここで半分に折ってお渡し／掲示できます</span>
      </div>

      {/* 説明面 (下半分) */}
      <div className="qrprint-panel inside">
        <h2>このQRコードについて</h2>
        <p>
          訪問介護スタッフが訪問のたびにこのQRコードを読み取り、到着・退出の時刻を記録します。記録を正確に行うため、下記にご協力をお願いいたします。
        </p>
        <ul>
          <li>玄関の内側など、雨の当たらない見やすい場所に掲示してください。</li>
          <li>QRがはがれた・汚れて読めない場合は事業所へご連絡ください（無料で再発行します）。</li>
          <li>このQRコードに、お名前や住所などの個人情報は含まれていません。</li>
        </ul>
        <div className="qrprint-contact">
          お問い合わせ：<b>{officeName ? `${officeName} 訪問介護事業所` : '訪問介護事業所'}</b>
        </div>
        <div className="qrprint-foot">
          <span>CareFlow 訪問チェックイン</span>
          <span>
            発行日 {issued}　/　{patient.code}
            {qr ? `　/　QR v${qr.version}` : ''}
          </span>
        </div>
      </div>
    </div>
  );
}

interface QrSelectRowProps {
  patient: PatientRead;
  officeName: string | null;
  onToggle: (next: boolean) => void;
}

/**
 * 一括モードで未選択の患者を表すコンパクト行 (氏名＋コード＋チェックのみ)。
 * 未選択の患者は full な A4 シートを描画せず QR もフェッチしないため、画面の
 * DOM 重量と GET 数は「選択数」に比例する。チェックで印刷対象に加える。
 */
function QrSelectRow({ patient, officeName, onToggle }: QrSelectRowProps) {
  return (
    <label className="qrprint-selrow" data-testid="qrprint-selrow" data-patient-id={patient.id}>
      <input
        type="checkbox"
        className="qrprint-selrow-chk"
        checked={false}
        aria-label={`${patient.name} を印刷対象にする`}
        onChange={(e) => onToggle(e.target.checked)}
      />
      <span className="qrprint-selrow-name">{patient.name} 様</span>
      <span className="qrprint-selrow-meta">
        {patient.code}
        {officeName ? `・${officeName}` : ''}
      </span>
    </label>
  );
}
