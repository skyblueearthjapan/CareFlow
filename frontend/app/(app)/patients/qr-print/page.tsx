'use client';

/**
 * /patients/qr-print — 患者 QR 印刷ビュー。
 *
 * `docs/mockups/qr-checkin/qr-print.html` の本実装。
 * A4 縦 1 枚 = **A5 横カード 2 面**（2026-09-18 お客様要望）。カードは
 * ご利用者様宅に貼るパウチ物なので、1 面に 氏名 / QR / お問い合わせ先 / ロゴ を収める。
 *   - 個別モード (`?mode=single&patient={id}`): シート 1 枚・上半分にカード 1 面。
 *   - 一括モード (`?mode=bulk`): 選択済みを 2 名ずつ 1 シートにまとめ、未選択は
 *     コンパクト行 (QR を取りに行かない) で後ろに並べる。
 *
 * カード = 左カラム (ロゴ / 患者名 / コード / 拠点 / お問い合わせ先) +
 * 右カラム (大 QR / 読み取りの案内) + foot (個人情報なしの注記 / 発行日・コード・qr_version)。
 * 切り取り線は A4 物理中央 (top:50%) に固定。
 * QR は `qrcode.react` で `${origin}/q/${token}` を符号化。
 * admin / manager のみ (非該当は /dashboard リダイレクト)。
 */

import { Suspense, useEffect, useMemo, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { QRCodeSVG } from 'qrcode.react';

import { Skeleton } from '@/components/ui/skeleton';
import { usePatients } from '@/lib/queries/patients';
import { STATUS_LABEL, normalizePatientStatus } from '@/lib/schemas/patient';
import { useOffices } from '@/lib/queries/offices';
import { usePatientQr } from '@/lib/queries/patientQr';
import { STATION_DAYS, STATION_HOURS, STATION_NAME, STATION_TEL } from '@/lib/qr-print-contact';
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

/** A4 1 枚に載る A5 カードの面数。 */
const CARDS_PER_SHEET = 2;

/** 配列を size ごとに切り出す (一括印刷を「2 名 = 1 シート」にまとめるため)。 */
function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

/**
 * ステータス絞り込み (PO 要望 2026-08-10): 患者マスタのステータスタブと同じ
 * 並び・ラベルで、一括印刷の対象を「稼働中だけ」等に絞れるようにする。
 * URL ?status= を尊重する (患者マスタのタブから遷移したとき同じ絞り込みで開く)。
 */
const QR_STATUS_TABS = [
  { value: 'active', label: STATUS_LABEL.active },
  { value: 'pending', label: STATUS_LABEL.pending },
  { value: 'suspended', label: STATUS_LABEL.suspended },
  { value: 'admitted', label: STATUS_LABEL.admitted },
  { value: 'cancelled', label: STATUS_LABEL.cancelled },
  { value: 'all', label: 'すべて' },
] as const;
type QrStatusValue = (typeof QR_STATUS_TABS)[number]['value'];

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

  const urlStatus = searchParams?.get('status') ?? '';
  const initialStatus: QrStatusValue = QR_STATUS_TABS.some((t) => t.value === urlStatus)
    ? (urlStatus as QrStatusValue)
    : 'active';

  const [mode, setMode] = useState<Mode>(initialMode);
  const [office, setOffice] = useState<string>('all');
  const [statusTab, setStatusTab] = useState<QrStatusValue>(initialStatus);
  const [selectedPatientId, setSelectedPatientId] = useState<string>(urlPatientId);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  /**
   * 一括モードの「初期全選択」を 1 度だけ走らせるためのフラグ。
   * 選択数 (size > 0) で判定すると、全解除した状態で shownPatients が差し替わった
   * とたんに全選択が復活してしまう (意図せず全員ぶん刷れてしまう)。
   */
  const [bulkInitialized, setBulkInitialized] = useState(false);

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

  // 一括: 拠点 × ステータスで絞った表示対象 (PO 要望 2026-08-10)。
  const officeFiltered = useMemo<PatientRead[]>(
    () =>
      office === 'all' ? allPatients : allPatients.filter((p) => p.primary_office_id === office),
    [allPatients, office],
  );
  const statusCounts = useMemo<Record<string, number>>(() => {
    const counts: Record<string, number> = { all: officeFiltered.length };
    for (const p of officeFiltered) {
      const st = normalizePatientStatus(p.status as string | null | undefined);
      counts[st] = (counts[st] ?? 0) + 1;
    }
    return counts;
  }, [officeFiltered]);
  const shownPatients = useMemo<PatientRead[]>(
    () =>
      statusTab === 'all'
        ? officeFiltered
        : officeFiltered.filter(
            (p) => normalizePatientStatus(p.status as string | null | undefined) === statusTab,
          ),
    [officeFiltered, statusTab],
  );

  // 単一モードの初期 selectedPatientId を、未指定なら先頭患者に補完。
  useEffect(() => {
    const first = allPatients[0];
    if (mode === 'single' && !selectedPatientId && first) {
      setSelectedPatientId(first.id);
    }
  }, [mode, selectedPatientId, allPatients]);

  // 一括モードに入った時点で表示中の患者を全選択 (初期状態)。上限までに頭打ち。
  // 以後は bulkInitialized が立つので、ユーザーの選択 (全解除含む) を上書きしない。
  useEffect(() => {
    if (mode === 'bulk' && !bulkInitialized && shownPatients.length > 0) {
      setSelected(new Set(shownPatients.slice(0, BULK_PRINT_LIMIT).map((p) => p.id)));
      setBulkInitialized(true);
    }
  }, [mode, bulkInitialized, shownPatients]);

  // ステータス/拠点の切替時は選択を作り直す (別ステータスの残留選択で
  // 「稼働中だけのはずが解約済みも刷れた」を防ぐ)。
  useEffect(() => {
    if (mode === 'bulk') {
      setSelected(new Set(shownPatients.slice(0, BULK_PRINT_LIMIT).map((p) => p.id)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusTab, office]);

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
  const bulkUnselectedPatients = shownPatients.filter((p) => !selected.has(p.id));
  // 2 名 = A4 1 枚。端数は 1 枚に 1 面だけ載る (下半分は白紙)。
  const bulkSheets = chunk(bulkSelectedPatients, CARDS_PER_SHEET);
  const bulkSheetCount = bulkSheets.length;
  // 表示数が上限を超える = 全員ぶんは刷れない。絞り込みを促す。
  const overLimit = mode === 'bulk' && shownPatients.length > BULK_PRINT_LIMIT;

  const issued = todayJst();

  return (
    <div className="qrprint-root">
      {/* ツールバー (画面のみ) */}
      <div className="qrprint-toolbar">
        {/* 1 段目: 戻る + 見出し (PO 要望 2026-08-10: ツールバーは明示 2 段構造)。 */}
        <div className="qrprint-toolbar-row">
          {/* 戻る導線 (PO 要望 2026-08-10): サイドバー頼みだったのを明示ボタンに。
            一括 → 患者マスタ (現在のステータス絞り込みを引き継ぐ) /
            個別 (患者詳細から遷移) → その患者の詳細へ戻る。 */}
          <Link
            href={
              mode === 'single' && urlPatientId
                ? `/patients/${urlPatientId}`
                : `/patients${statusTab !== 'active' ? `?status=${statusTab}` : ''}`
            }
            className="qrprint-btn"
            data-testid="qrprint-back"
          >
            ← {mode === 'single' && urlPatientId ? '患者詳細へ戻る' : '患者マスタへ戻る'}
          </Link>
          <span className="qrprint-sep" />
          <div>
            <div className="qrprint-crumb">
              {mode === 'single' ? '患者マスタ › 患者詳細 › QR印刷' : '患者マスタ › QR一括印刷'}
            </div>
            <h1 className="qrprint-title">患者QR 印刷</h1>
          </div>
        </div>

        {/* 2 段目: 左=モード切替+絞り込み群 / 右=件数+全選択/全解除/印刷 (PO 要望)。 */}
        <div className="qrprint-toolbar-row">
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
                    {p.primary_office_id ? `・${officeNameMap.get(p.primary_office_id) ?? ''}` : ''}
                    ）
                  </option>
                ))}
              </select>
            </span>
          ) : (
            <span className="qrprint-chips">
              {/* ステータス絞り込み (患者マスタのタブと同じ区分・PO 要望 2026-08-10)。 */}
              {QR_STATUS_TABS.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  className={`qrprint-chip${statusTab === t.value ? ' on' : ''}`}
                  data-testid={`qrprint-status-${t.value}`}
                  aria-pressed={statusTab === t.value}
                  onClick={() => setStatusTab(t.value)}
                >
                  {t.label} {statusCounts[t.value] ?? 0}
                </button>
              ))}
              <span className="qrprint-sep" />
              <button
                type="button"
                className={`qrprint-chip${office === 'all' ? ' on' : ''}`}
                aria-pressed={office === 'all'}
                onClick={() => setOffice('all')}
              >
                全拠点
              </button>
              {officeChips.map((o) => (
                <button
                  key={o.id}
                  type="button"
                  className={`qrprint-chip${office === o.id ? ' on' : ''}`}
                  aria-pressed={office === o.id}
                  onClick={() => setOffice(o.id)}
                >
                  {o.name}
                </button>
              ))}
            </span>
          )}
          {/* 2 行目 (PO 要望 2026-08-10): 操作ボタン群は右下の行にまとめて揃える。
            戻るボタン追加でツールバーが折り返し、全選択だけ 1 行目に残り
            全解除/印刷が左下へ流れていたのを、明示的な右寄せ行に固定する。 */}
          <span className="qrprint-actions" data-testid="qrprint-actions">
            <span className="qrprint-count">
              {mode === 'single'
                ? '印刷 1枚'
                : `表示 ${shownPatients.length}名 / 印刷 ${bulkSelectedPatients.length}名（A4 ${bulkSheetCount}枚）`}
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
                <button
                  type="button"
                  className="qrprint-btn"
                  onClick={() => setSelected(new Set())}
                >
                  全解除
                </button>
              </>
            ) : null}
            {/* 全解除 (= 刷る中身が無い) のまま押すと白紙が出るだけなので塞ぐ。 */}
            <button
              type="button"
              className="qrprint-btn brand"
              data-testid="qrprint-print"
              disabled={mode === 'bulk' && bulkSelectedPatients.length === 0}
              onClick={() => window.print()}
            >
              🖨 印刷
            </button>
          </span>
        </div>
      </div>
      <div className="qrprint-hint">
        {mode === 'single'
          ? '💡 個別モード：選んだ患者 1 名分を A4 上半分の A5 カード 1 面に印刷します（切り取ってパウチ）。'
          : '💡 一括モード：拠点・ステータスで絞り込み、チェックで対象を選びます。A4 1 枚に A5 カード 2 面（2 名分）をまとめて印刷します。'}
      </div>

      {overLimit ? (
        <div className="qrprint-warn" role="alert">
          ⚠ 表示 {shownPatients.length}名 は印刷上限 {BULK_PRINT_LIMIT}件
          を超えています。拠点で絞り込んでから印刷してください（選択は最大 {BULK_PRINT_LIMIT}
          名まで）。
        </div>
      ) : null}

      {/* 印刷スコープ (2026-08-10 PO要望): 印刷時はこのブロックだけを印字し、
          AppShell (サイドバー/ヘッダ) を含む他要素は qr-print.css の
          body:has(#qr-print-area) ルールで全て隠す (受け入れ枠 #acceptance-print と同方式)。 */}
      <div id="qr-print-area" className="qrprint-pages" data-testid="qrprint-pages">
        {mode === 'single' ? (
          singlePatient ? (
            // 個別: シート 1 枚・上半分にカード 1 面 (下半分は白紙・切り取り線は出す)。
            <QrSheet>
              <QrCard
                key={singlePatient.id}
                patient={singlePatient}
                issued={issued}
                showCheckbox={false}
                onToggle={() => {}}
              />
            </QrSheet>
          ) : (
            <p className="qrprint-empty">対象の患者がいません。</p>
          )
        ) : shownPatients.length === 0 ? (
          <p className="qrprint-empty">対象の患者がいません。</p>
        ) : (
          <>
            {/* 一括: 選択済みを 2 名ずつ 1 シートに詰める (= GET も描画量も選択数に比例)。
                チェックを外すと後ろのコンパクト行へ移り、残りが再ペアされる。 */}
            {bulkSheets.map((pair) => (
              <QrSheet key={pair.map((p) => p.id).join('+')}>
                {pair.map((p) => (
                  <QrCard
                    key={p.id}
                    patient={p}
                    issued={issued}
                    showCheckbox
                    onToggle={(next) => toggleSelected(p.id, next)}
                  />
                ))}
              </QrSheet>
            ))}
            {/* 未選択は氏名＋チェックのみのコンパクト行。QR は取りに行かない。 */}
            {bulkUnselectedPatients.map((p) => (
              <QrSelectRow
                key={p.id}
                patient={p}
                officeName={
                  p.primary_office_id ? (officeNameMap.get(p.primary_office_id) ?? null) : null
                }
                onToggle={(next) => toggleSelected(p.id, next)}
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

/**
 * A4 シート 1 枚。中に A5 横カードを最大 2 面。切り取り線は物理中央 (top:50%) に固定なので
 * カードが 1 面だけ (個別モード / 一括の端数) でも同じ位置に出る。
 */
function QrSheet({ children }: { children: ReactNode }) {
  return (
    <div className="qrprint-sheet" data-testid="qrprint-sheet">
      {children}
      <div className="qrprint-fold">
        <span>✂ ここで切り取り（A5・パウチ用）</span>
      </div>
    </div>
  );
}

interface QrCardProps {
  patient: PatientRead;
  issued: string;
  showCheckbox: boolean;
  onToggle: (next: boolean) => void;
}

/**
 * 患者 1 名分の A5 横カード (210mm × 148.25mm)。
 * ご利用者様宅に貼るパウチ物なので、氏名・QR・お問い合わせ先・ロゴをこの 1 面に収める。
 * 拠点名は社内の区分でご利用者様には意味がないため、カードには載せない (PO 判断 2026-09-18)。
 * QR は遅延発行 API から取得する (カードは印刷対象のときしか描画しない)。
 */
function QrCard({ patient, issued, showCheckbox, onToggle }: QrCardProps) {
  const { data: qr, isLoading, isError } = usePatientQr(patient.id);

  return (
    <div className="qrprint-card" data-testid="qrprint-card" data-patient-id={patient.id}>
      {showCheckbox ? (
        <input
          type="checkbox"
          className="qrprint-chk"
          checked
          aria-label={`${patient.name} を印刷対象にする`}
          onChange={(e) => onToggle(e.target.checked)}
        />
      ) : null}

      <div className="qrprint-card-body">
        {/* 左: ロゴ / 氏名 / コード / 拠点 … 下端に お問い合わせ先 */}
        <div className="qrprint-col-left">
          {/* 直下の連絡先ブロックにステーション名がテキストで入るので、ロゴは装飾扱い (alt="")。
              読み上げが「訪問看護ステーション よりより」を二度繰り返すのを避ける。 */}
          {/* eslint-disable-next-line @next/next/no-img-element -- 静的ブランド画像 (印刷物なので next/image の最適化は不要) */}
          <img
            className="qrprint-logo"
            src="/brand/yoriyori-logo-h.svg"
            alt=""
            width={160}
            height={45}
          />
          <div className="qrprint-pname">{patient.name} 様</div>
          <div className="qrprint-pcode">{patient.code}</div>

          <div className="qrprint-contactbox">
            <div className="qrprint-contact-h">お問い合わせ先</div>
            <div className="qrprint-contact-tel">TEL {STATION_TEL}</div>
            <div className="qrprint-contact-row">対応時間 {STATION_HOURS}</div>
            <div className="qrprint-contact-row">対応日 {STATION_DAYS}</div>
            <div className="qrprint-contact-station">{STATION_NAME}</div>
          </div>
        </div>

        {/* 右: 大 QR + 読み取りの案内 1 行 */}
        <div className="qrprint-col-right">
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
            訪問のたびに、スタッフがこのQRコードを読み取って到着・退出を記録します
          </div>
        </div>
      </div>

      <div className="qrprint-foot">
        <span>このQRにお名前・住所などの個人情報は含まれていません</span>
        <span>
          発行日 {issued}　/　{patient.code}
          {qr ? `　/　QR v${qr.version}` : ''}
        </span>
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
