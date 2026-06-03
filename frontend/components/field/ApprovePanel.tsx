'use client';

/**
 * CareFlow Mobile — 承認モード パネル (Phase2-3c 実データ接続)
 *
 * `GET /api/v1/pending-requests?status=pending` で実 pending を取得し、
 * 承認 (`PATCH .../approve`) / 却下 (`PATCH .../reject`) を行う。
 *
 * 【実データ制約】実 pending-requests は「ボードのどの枠か」の座標
 * (course / slot / 時刻) を持たないため、Phase 1 モックの「枠への仮配置演出」は
 * 実データで再現できない。本パネルは **申請一覧をレビュー可能に出す
 * (承認 / 却下)** に留める (タスク指定の縮退仕様)。payload に target_date 等が
 * あれば補助表示する。
 */

import { useState } from 'react';
import { Check, ClipboardCheck, User, Calendar, Sparkles, Heart } from 'lucide-react';

import {
  usePendingRequests,
  useApproveRequest,
  useRejectRequest,
} from '@/lib/queries/pending_requests';
import type { PendingRequestV2Read, RequestType } from '@/lib/schemas/pending_request';
import { INSURANCE_LABEL, SEX_LABEL } from '@/lib/schemas/patient';
import { WEEKDAY_INT_TO_JA } from '@/lib/field/patientCreate';
import { ApiError } from '@/lib/api-client';

import { CF_THEME } from './theme';

const { MINT, TERRA, TERRA_DEEP, INK, INK2, INK3, LINE, GOLD } = CF_THEME;

/** request_type の日本語ラベル。 */
const REQUEST_TYPE_LABEL_JA: Record<RequestType, string> = {
  staff_off: 'スタッフ休み',
  staff_event: 'スタッフ予定',
  staff_mentor: '同行 (メンター)',
  staff_create: 'スタッフ登録',
  patient_create: '患者新規',
  patient_cancel: '患者キャンセル',
  patient_reschedule: '訪問日変更',
  patient_special_week_on: '特別週 ON',
  patient_special_week_off: '特別週 OFF',
  staff_status_update: 'スタッフ状態変更',
  patient_status_update: '患者状態変更',
};

function requestTypeLabel(t: string): string {
  return (REQUEST_TYPE_LABEL_JA as Record<string, string>)[t] ?? t;
}

/** payload からタイトルに使える人名/件名を緩く拾う (構造は request_type 依存)。 */
function payloadHeadline(req: PendingRequestV2Read): string | null {
  const p = req.payload as Record<string, unknown>;
  const cands = ['patient_name', 'staff_name', 'name', 'title', 'summary'];
  for (const key of cands) {
    const v = p[key];
    if (typeof v === 'string' && v.trim()) return v.trim();
  }
  return null;
}

// ─────────────────────────────────────────────────────────────────────────
// patient_create プレビュー (StageB)
// ─────────────────────────────────────────────────────────────────────────

interface ProposedVisitView {
  weekday: number;
  start_time: string;
  duration_min: number;
  course_code: string;
}

/** payload.proposed_visits を安全にパースして表示用配列にする。 */
function readProposedVisits(payload: Record<string, unknown>): ProposedVisitView[] {
  const raw = payload.proposed_visits;
  if (!Array.isArray(raw)) return [];
  const out: ProposedVisitView[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const r = item as Record<string, unknown>;
    const weekday = Number(r.weekday);
    const start = typeof r.start_time === 'string' ? r.start_time : '';
    if (!Number.isFinite(weekday) || !start) continue;
    out.push({
      weekday,
      start_time: start,
      duration_min: Number.isFinite(Number(r.duration_min)) ? Number(r.duration_min) : 0,
      course_code: typeof r.course_code === 'string' ? r.course_code : '',
    });
  }
  out.sort((a, b) => a.weekday - b.weekday || a.start_time.localeCompare(b.start_time));
  return out;
}

function str(payload: Record<string, unknown>, key: string): string | null {
  const v = payload[key];
  return typeof v === 'string' && v.trim() ? v.trim() : null;
}

/**
 * patient_create 申請の payload からカルテ概要 + 提案枠 (曜日/時刻/コース) を
 * プレビュー表示する (StageB)。承認すると患者作成 + PFV 確定が同一 TX で走る。
 */
function PatientCreatePreview({ payload }: { payload: Record<string, unknown> }) {
  const code = str(payload, 'code');
  const kana = str(payload, 'kana');
  const sexRaw = str(payload, 'sex');
  const sex = sexRaw && sexRaw in SEX_LABEL ? SEX_LABEL[sexRaw as keyof typeof SEX_LABEL] : null;
  const insRaw = str(payload, 'insurance');
  const insurance =
    insRaw && insRaw in INSURANCE_LABEL
      ? INSURANCE_LABEL[insRaw as keyof typeof INSURANCE_LABEL]
      : null;
  const address = str(payload, 'address');
  const visits = readProposedVisits(payload);

  const chips: Array<{ k: string; v: string }> = [];
  if (code) chips.push({ k: 'コード', v: code });
  if (kana) chips.push({ k: 'カナ', v: kana });
  if (sex) chips.push({ k: '性別', v: sex });
  if (insurance) chips.push({ k: '保険', v: insurance });

  return (
    <div
      style={{
        marginTop: 8,
        background: '#fff',
        border: `1.5px solid ${LINE}`,
        borderRadius: 12,
        padding: '9px 11px',
      }}
    >
      {(chips.length > 0 || address) && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 5,
            fontSize: 10.5,
            fontWeight: 700,
            color: TERRA_DEEP,
            marginBottom: 6,
          }}
        >
          <Heart size={11} />
          カルテ概要
        </div>
      )}
      {chips.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: address ? 5 : 0 }}>
          {chips.map((c) => (
            <span
              key={c.k}
              style={{
                fontSize: 10.5,
                fontWeight: 700,
                padding: '2px 8px',
                borderRadius: 999,
                background: '#F4EFE7',
                color: INK2,
              }}
            >
              {c.k}: <span style={{ color: INK }}>{c.v}</span>
            </span>
          ))}
        </div>
      )}
      {address && <div style={{ fontSize: 11, color: INK2, marginBottom: 2 }}>住所: {address}</div>}

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 5,
          fontSize: 10.5,
          fontWeight: 700,
          color: TERRA_DEEP,
          margin: '8px 0 6px',
        }}
      >
        <Sparkles size={11} />
        提案枠 {visits.length}件
      </div>
      {visits.length === 0 ? (
        <div style={{ fontSize: 11, color: INK3 }}>提案枠は登録されていません</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {visits.map((v, i) => (
            <div
              key={`${v.weekday}-${v.start_time}-${i}`}
              style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12 }}
            >
              <span
                style={{
                  width: 22,
                  height: 22,
                  flex: '0 0 auto',
                  borderRadius: 7,
                  background: '#FCEBD6',
                  color: TERRA,
                  display: 'grid',
                  placeItems: 'center',
                  fontFamily: 'var(--font-serif)',
                  fontSize: 12,
                  fontWeight: 700,
                }}
              >
                {WEEKDAY_INT_TO_JA[v.weekday] ?? '?'}
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: INK }}>
                {v.start_time}
                {v.duration_min > 0 ? ` ・ ${v.duration_min}分` : ''}
              </span>
              {v.course_code && (
                <span style={{ fontWeight: 700, color: INK2 }}>{v.course_code}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function ApprovePanel({ onToast }: { onToast: (msg: string) => void }) {
  const query = usePendingRequests({ status: 'pending', limit: 100 });
  const approveMut = useApproveRequest();
  const rejectMut = useRejectRequest();

  // 行ごとの「却下理由入力中」state。
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);

  const items = query.data?.items ?? [];

  const handleApprove = (id: string) => {
    setBusyId(id);
    approveMut.mutate(id, {
      onSuccess: () => onToast('✓ 承認しました'),
      onError: (e) =>
        onToast(
          e instanceof ApiError && e.status === 409 ? '既に処理済みです' : '承認に失敗しました',
        ),
      onSettled: () => setBusyId(null),
    });
  };

  const handleReject = (id: string) => {
    if (!reason.trim()) {
      onToast('却下理由を入力してください');
      return;
    }
    setBusyId(id);
    rejectMut.mutate(
      { id, rejection_reason: reason.trim() },
      {
        onSuccess: () => {
          onToast('却下しました');
          setRejectingId(null);
          setReason('');
        },
        onError: () => onToast('却下に失敗しました'),
        onSettled: () => setBusyId(null),
      },
    );
  };

  if (query.isLoading) {
    return (
      <div
        style={{
          textAlign: 'center',
          color: INK3,
          padding: '54px 20px',
          fontFamily: 'var(--font-serif)',
        }}
      >
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: '50%',
            border: `3px solid ${LINE}`,
            borderTopColor: GOLD,
            margin: '0 auto',
            animation: 'cfSpin 0.8s linear infinite',
          }}
        />
        <div style={{ marginTop: 14, fontSize: 14, fontWeight: 700 }}>承認待ちを読み込み中…</div>
      </div>
    );
  }

  if (query.isError) {
    return (
      <div
        style={{
          textAlign: 'center',
          color: INK2,
          padding: '54px 20px',
          fontFamily: 'var(--font-serif)',
        }}
      >
        <div style={{ fontSize: 38 }}>⚠️</div>
        <div style={{ marginTop: 10, fontSize: 15, fontWeight: 700, color: '#C75C77' }}>
          承認待ちの読み込みに失敗しました
        </div>
        <button
          onClick={() => void query.refetch()}
          style={{
            marginTop: 16,
            padding: '10px 22px',
            borderRadius: 12,
            background: GOLD,
            color: '#fff',
            fontFamily: 'var(--font-serif)',
            fontWeight: 700,
            fontSize: 13,
          }}
        >
          再読み込み
        </button>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div
        style={{
          textAlign: 'center',
          color: INK3,
          padding: '60px 20px',
          fontFamily: 'var(--font-serif)',
        }}
      >
        <div style={{ fontSize: 44 }}>✅</div>
        <div style={{ marginTop: 10, fontSize: 16, fontWeight: 700 }}>承認待ちはありません</div>
        <div style={{ fontSize: 12.5, marginTop: 4, fontFamily: 'var(--font-sans)' }}>
          すべての申請が処理済みです
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '8px 14px 0' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontFamily: 'var(--font-serif)',
          fontSize: 13,
          fontWeight: 700,
          color: '#9A7400',
        }}
      >
        <ClipboardCheck size={15} />
        承認待ちの申請 {items.length}件
      </div>

      {items.map((req) => {
        const headline = payloadHeadline(req);
        const busy = busyId === req.id;
        const isRejecting = rejectingId === req.id;
        return (
          <div
            key={req.id}
            style={{
              borderRadius: 14,
              border: `2.5px dashed ${GOLD}`,
              background:
                'repeating-linear-gradient(45deg, #FFFCEC, #FFFCEC 9px, #FFF5C9 9px, #FFF5C9 18px)',
              padding: '11px 13px',
              position: 'relative',
            }}
          >
            <span
              style={{
                position: 'absolute',
                top: -9,
                right: 10,
                background: GOLD,
                color: '#fff',
                fontFamily: 'var(--font-serif)',
                fontSize: 10,
                fontWeight: 700,
                padding: '2px 8px',
                borderRadius: 999,
              }}
            >
              承認待ち
            </span>
            <div
              style={{
                fontFamily: 'var(--font-serif)',
                fontSize: 14.5,
                fontWeight: 700,
                color: INK,
              }}
            >
              {requestTypeLabel(req.request_type)}
            </div>
            {headline && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 5,
                  fontSize: 12,
                  color: INK2,
                  marginTop: 3,
                  fontWeight: 600,
                }}
              >
                <User size={11} />
                {headline}
              </div>
            )}
            {req.target_date && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 5,
                  fontSize: 11.5,
                  color: INK2,
                  marginTop: 3,
                }}
              >
                <Calendar size={11} />
                対象日: {req.target_date}
              </div>
            )}
            <div
              style={{ fontSize: 10, color: INK3, marginTop: 4, fontFamily: 'var(--font-mono)' }}
            >
              申請: {req.created_at.slice(0, 16).replace('T', ' ')}
            </div>

            {req.request_type === 'patient_create' && (
              <PatientCreatePreview payload={req.payload as Record<string, unknown>} />
            )}

            {isRejecting ? (
              <div style={{ marginTop: 10 }}>
                <textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="却下理由を入力 (必須)"
                  rows={2}
                  style={{
                    width: '100%',
                    border: `2px solid #F4D4DC`,
                    borderRadius: 10,
                    padding: '9px 11px',
                    fontFamily: 'var(--font-sans)',
                    fontSize: 13,
                    fontWeight: 600,
                    color: INK,
                    background: '#fff',
                    outline: 'none',
                    resize: 'vertical',
                  }}
                />
                <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                  <button
                    onClick={() => handleReject(req.id)}
                    disabled={busy}
                    style={{
                      flex: 1,
                      padding: '9px',
                      borderRadius: 10,
                      background: '#C75C77',
                      color: '#fff',
                      fontFamily: 'var(--font-serif)',
                      fontWeight: 700,
                      fontSize: 13,
                      opacity: busy ? 0.6 : 1,
                    }}
                  >
                    却下を確定
                  </button>
                  <button
                    onClick={() => {
                      setRejectingId(null);
                      setReason('');
                    }}
                    disabled={busy}
                    style={{
                      flex: '0 0 auto',
                      padding: '9px 16px',
                      borderRadius: 10,
                      background: '#fff',
                      color: INK2,
                      border: `2px solid ${LINE}`,
                      fontFamily: 'var(--font-serif)',
                      fontWeight: 700,
                      fontSize: 13,
                    }}
                  >
                    戻る
                  </button>
                </div>
              </div>
            ) : (
              <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
                <button
                  onClick={() => handleApprove(req.id)}
                  disabled={busy}
                  style={{
                    flex: 1,
                    padding: '9px',
                    borderRadius: 10,
                    background: MINT,
                    color: '#fff',
                    fontFamily: 'var(--font-serif)',
                    fontWeight: 700,
                    fontSize: 13,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: 5,
                    opacity: busy ? 0.6 : 1,
                  }}
                >
                  <Check size={14} />
                  承認
                </button>
                <button
                  onClick={() => {
                    setRejectingId(req.id);
                    setReason('');
                  }}
                  disabled={busy}
                  style={{
                    flex: 1,
                    padding: '9px',
                    borderRadius: 10,
                    background: '#fff',
                    color: '#C75C77',
                    border: '2px solid #F4D4DC',
                    fontFamily: 'var(--font-serif)',
                    fontWeight: 700,
                    fontSize: 13,
                  }}
                >
                  却下
                </button>
              </div>
            )}
          </div>
        );
      })}

      <div
        style={{
          fontSize: 10.5,
          color: INK3,
          textAlign: 'center',
          padding: '6px 12px 12px',
          lineHeight: 1.5,
          fontFamily: 'var(--font-sans)',
        }}
      >
        ※ 申請は枠位置情報を持たないため、ボード内への仮配置表示は行いません。
        <br />
        承認すると業務テーブルへ即時反映されます。
      </div>
    </div>
  );
}
