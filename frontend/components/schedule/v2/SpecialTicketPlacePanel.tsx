'use client';

/**
 * SpecialTicketPlacePanel / SpecialVisitPoolSection — 特別訪問週間のプール統合.
 *
 * 正典: `docs/plans/special-visit-week-design.md` §6-2 (Wave2)。
 *
 * 保留プールの**最上段**に「⭐特別訪問週間」専用セクションを出し、表示中の週の
 * チケット (GET /special-visit-marks/pool) を通常のプール患者と明確に区別して並べる。
 * チケットをクリックすると、そのチケットの曜日に絞った候補提案 (既存の
 * POST /v2/propose-slots を再利用) をインライン展開し、1 クリックで
 * POST /special-visit-marks/{id}/place へ流す。
 *
 * 設計上のルール:
 *   - 追加分は**固定化しない**。place は PFV を作らず、その週の visit 行だけを増やす
 *     (= トーストにも「この週のみ・固定化しません」と明記する)。
 *   - チケット 0 件のときはセクション自体を描画しない (通常のプールを邪魔しない)。
 *   - `last_placement` は**参考ヒント**。候補リストの先頭に出すだけで強制はしない。
 *   - 意匠は既存トークンのみ (brand-primary / border-border-default / text-text-* /
 *     warning 系)。ミニスケジュールは出さずコンパクト優先 (プール幅 320px)。
 */
import * as React from 'react';
import { AlertTriangle, CalendarDays, Loader2, Star } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

import { proposeWarningLabel, useProposeSlots } from '@/lib/queries/fieldBoard';
import { usePatient } from '@/lib/queries/patients';
import { usePlaceSpecialMark, useSpecialVisitPool } from '@/lib/queries/specialVisitWeek';
import { coerceWeeklyPattern } from '@/lib/schemas/patient';
import type { SpecialPoolTicket } from '@/lib/schemas/specialVisitWeek';
import type {
  ProposeSlotItem,
  ProposeTimeType,
  WeekdayCode,
} from '@/lib/schemas/v2/propose_slots';

import { SpecialVisitWeekDialog } from './SpecialVisitWeekDialog';

/** 0=月..5=土 (日曜は対象外だが propose 側の 6 も落ちないよう 7 要素持つ). */
const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;
const WEEKDAY_CODES: readonly WeekdayCode[] = [
  'Mon',
  'Tue',
  'Wed',
  'Thu',
  'Fri',
  'Sat',
  'Sun',
] as const;

/** 候補の表示上限 (プール幅に収めるためコンパクトに 5 件)。 */
const CANDIDATE_LIMIT = 5;

function weekdayLabel(weekday: number): string {
  return WEEKDAY_LABELS[weekday] ?? '?';
}

/** "HH:MM:SS" → "HH:MM" (BE は秒付きで返すことがある)。 */
function trimSeconds(t: string | null | undefined): string {
  if (!t) return '';
  return t.length >= 5 ? t.slice(0, 5) : t;
}

/** 候補 1 件のキー (拠点 + コース + 曜日 + 開始時刻で一意)。 */
function candidateKey(s: ProposeSlotItem): string {
  return `${s.office_id}-${s.course_code}-${s.weekday}-${trimSeconds(s.start_time)}`;
}

/** ApiError 由来の detail 文字列を取り出す (409「既に配置済みです」等をそのまま出す)。 */
function errorDetail(err: unknown): string | null {
  const body = (err as { body?: unknown } | null)?.body;
  if (body && typeof body === 'object') {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === 'string' && detail) return detail;
  }
  return null;
}

// ---------------------------------------------------------------------------
// SpecialTicketPlacePanel — チケット 1 件のインライン配置パネル
// ---------------------------------------------------------------------------

export interface SpecialTicketPlacePanelProps {
  ticket: SpecialPoolTicket;
  /** 一覧と同じ拠点スコープ. null = 全拠点 (患者の主担当拠点にフォールバック)。 */
  officeId: string | null;
  /** 配置ボタンを出すか (RBAC; admin/manager のみ)。 */
  canEdit: boolean;
  /** 配置成功後に親へ通知 (展開を畳む等)。 */
  onPlaced?: () => void;
}

export function SpecialTicketPlacePanel({
  ticket,
  officeId,
  canEdit,
  onPlaced,
}: SpecialTicketPlacePanelProps) {
  const { mark, patient, last_placement: lastPlacement } = ticket;

  // 所要時間・希望時刻は患者マスタが正 (プールチケットには載らない)。
  // 未取得でも既定値 (coerceWeeklyPattern) で提案は回せる。
  const patientQuery = usePatient(patient.id);
  const weeklyPattern = React.useMemo(
    () => coerceWeeklyPattern(patientQuery.data?.weekly_pattern),
    [patientQuery.data],
  );

  const proposeMut = useProposeSlots();
  const placeMut = usePlaceSpecialMark();
  const { mutate: propose } = proposeMut;

  const lat = patient.lat ?? patientQuery.data?.lat ?? null;
  const lng = patient.lng ?? patientQuery.data?.lng ?? null;
  const address = patientQuery.data?.address ?? '';
  // 住所未確定 = 座標も住所も無い → 提案できない (距離が計算できないため)。
  const hasLocation =
    (typeof lat === 'number' && typeof lng === 'number') || address.trim().length > 0;

  const runPropose = React.useCallback(() => {
    const showTimeRange =
      weeklyPattern.time_type === '固定' || weeklyPattern.time_type === '時間帯';
    const showEnd = weeklyPattern.time_type === '時間帯';
    propose(
      {
        address,
        lat: typeof lat === 'number' ? lat : null,
        lng: typeof lng === 'number' ? lng : null,
        // 枠長は BE 正典 (PFV優先・place の _resolve_service_minutes と同一計算) を
        // 最優先。旧BE (未送出=null) は従来どおり患者マスタから (レビュー補強)。
        service_minutes: ticket.service_minutes ?? weeklyPattern.service_minutes,
        time_type: weeklyPattern.time_type as ProposeTimeType,
        preferred_start: showTimeRange ? weeklyPattern.preferred_start : null,
        preferred_end: showEnd ? weeklyPattern.preferred_end : null,
        // §6-2: 曜日はチケットが正 (○を付けた曜日にしか配置しない)。
        preferred_weekdays: [WEEKDAY_CODES[mark.weekday] ?? 'Mon'],
        frequency_per_week: 1,
        requires_multiple_staff: patient.requires_multiple_staff === true,
        sex_restriction: patient.sex_restriction ?? null,
        iso_year: mark.iso_year,
        iso_week: mark.iso_week,
        office_ids: officeId
          ? [officeId]
          : patient.primary_office_id
            ? [patient.primary_office_id]
            : [],
        existing_patient_id: patient.id,
        limit: CANDIDATE_LIMIT,
      },
      { onError: () => toast.error('候補の取得に失敗しました') },
    );
  }, [propose, address, lat, lng, weeklyPattern, mark, patient, officeId, ticket.service_minutes]);

  // 展開直後に 1 回だけ提案を回す (患者マスタの取得完了を待ってから = 所要時間が正しい)。
  const firedRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (patientQuery.isLoading) return;
    if (!hasLocation) return;
    if (firedRef.current === mark.id) return;
    firedRef.current = mark.id;
    runPropose();
    // 提案は mark 単位で 1 回。runPropose の同値変化では再実行しない。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patientQuery.isLoading, hasLocation, mark.id]);

  const slots = React.useMemo(
    () => (proposeMut.data?.slots ?? []).slice(0, CANDIDATE_LIMIT),
    [proposeMut.data],
  );

  const handlePlace = (slot: ProposeSlotItem) => {
    const startTime = trimSeconds(slot.start_time);
    const label = `${weekdayLabel(mark.weekday)}曜 ${startTime} ${slot.course_label}`;
    if (!window.confirm(`${patient.name} 様を ${label} に配置します。よろしいですか？`)) return;
    placeMut.mutate(
      {
        markId: mark.id,
        // propose-slots の候補は course_id を持たないため (office_id + course_code)。
        payload: {
          office_id: slot.office_id,
          course_code: slot.course_code,
          start_time: startTime,
        },
      },
      {
        onSuccess: () => {
          toast.success(
            `${patient.name} 様を${weekdayLabel(mark.weekday)}曜 ${startTime} に配置しました` +
              `（この週のみ・固定化しません）`,
          );
          onPlaced?.();
        },
        onError: (err) => {
          toast.error(errorDetail(err) ?? '配置に失敗しました');
        },
      },
    );
  };

  if (!hasLocation) {
    return (
      <p
        className="mt-1.5 rounded border border-border-default bg-bg-muted/30 px-2 py-1.5 text-[11px] text-text-muted"
        data-testid="special-ticket-no-location"
      >
        住所未確定のため提案できません。患者情報の住所を登録してください。
      </p>
    );
  }

  return (
    <div className="mt-1.5" data-testid="special-ticket-place-panel">
      {/* 前回配置ヒント (参考・強制しない)。 */}
      {lastPlacement ? (
        <div
          className="mb-1.5 rounded border border-border-default bg-bg-muted/30 px-2 py-1 text-[11px] text-text-secondary"
          data-testid="special-ticket-last-placement"
        >
          💡 前回はここでした: {weekdayLabel(lastPlacement.weekday)}曜{' '}
          {trimSeconds(lastPlacement.start_time)} {lastPlacement.course_label}
          {lastPlacement.staff_name ? `（担当${lastPlacement.staff_name}）` : ''}
        </div>
      ) : null}

      {proposeMut.isPending ? (
        <p className="flex items-center gap-1 text-[11px] text-text-muted">
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          空き枠を探しています…
        </p>
      ) : null}

      {!proposeMut.isPending && proposeMut.data && slots.length === 0 ? (
        <p className="text-[11px] text-text-muted" data-testid="special-ticket-no-candidate">
          この曜日に入れる空き枠が見つかりませんでした。
        </p>
      ) : null}

      {slots.length > 0 ? (
        <ul className="space-y-1" data-testid="special-ticket-candidate-list">
          {slots.map((s, i) => (
            <li
              key={`${candidateKey(s)}-${i}`}
              className="rounded border border-border-default bg-bg-base p-1.5 text-[11px]"
              data-testid={`special-ticket-candidate-${candidateKey(s)}`}
            >
              <div className="flex flex-wrap items-center gap-1">
                <span className="tnum font-medium text-text-primary">
                  {weekdayLabel(s.weekday)} {trimSeconds(s.start_time)}–{trimSeconds(s.end_time)}
                </span>
                <span className="text-text-secondary">{s.course_label}</span>
                {s.staff_name ? (
                  <span className="text-text-muted">担当: {s.staff_name}</span>
                ) : null}
              </div>
              {s.warnings.length > 0 || s.event_conflicts.length > 0 ? (
                <div className="mt-0.5 flex flex-wrap items-center gap-1">
                  {s.warnings.map((w, wi) => (
                    <Badge key={`w-${wi}`} variant="warning" className="text-[10px]">
                      {proposeWarningLabel(w)}
                    </Badge>
                  ))}
                  {s.event_conflicts.length > 0 ? (
                    <span
                      className="flex items-center gap-0.5 text-[10px] text-warning-strong"
                      data-testid="special-ticket-event-conflict"
                    >
                      <AlertTriangle className="h-3 w-3" aria-hidden />⚠ 担当者の予定と重なります（
                      {s.event_conflicts.map((e) => e.title).join('・')}）
                    </span>
                  ) : null}
                </div>
              ) : null}
              {canEdit ? (
                <div className="mt-1 flex justify-end">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => handlePlace(s)}
                    disabled={placeMut.isPending}
                    className="h-6 px-2 text-[11px]"
                    data-testid={`special-ticket-place-${candidateKey(s)}`}
                  >
                    この枠に配置
                  </Button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SpecialVisitPoolSection — 保留プール最上段の専用セクション
// ---------------------------------------------------------------------------

export interface SpecialVisitPoolSectionProps {
  isoYear: number;
  isoWeek: number;
  /** プールと同じ拠点スコープ. null = 全拠点。 */
  officeId: string | null;
  /** 配置ボタンを出すか (RBAC; admin/manager のみ)。 */
  canEdit: boolean;
}

export function SpecialVisitPoolSection({
  isoYear,
  isoWeek,
  officeId,
  canEdit,
}: SpecialVisitPoolSectionProps) {
  const poolQuery = useSpecialVisitPool(isoYear, isoWeek, officeId);
  const tickets = poolQuery.data ?? [];
  // 展開中のチケット (1 件だけ開く = クエリバーストを避ける)。
  const [openMarkId, setOpenMarkId] = React.useState<string | null>(null);
  // 「カレンダーを開く」で表示する設定モーダルの対象患者。
  const [calendarPatient, setCalendarPatient] = React.useState<{
    id: string;
    name: string;
  } | null>(null);

  // チケット 0 件ならセクションごと出さない (§6-2)。
  if (tickets.length === 0) return null;

  return (
    <div
      className="mb-2 rounded-md border-2 border-brand-primary bg-brand-primary/5 p-2"
      data-testid="special-visit-pool-section"
    >
      <div className="mb-1.5 flex items-center gap-1 text-xs font-semibold text-brand-primary">
        <Star className="h-3.5 w-3.5" aria-hidden />⭐ 特別訪問週間
        <span className="tnum text-[10px] font-normal text-text-muted">
          ({tickets.length}件)
        </span>
      </div>
      <ul className="space-y-1">
        {tickets.map((t) => {
          const open = openMarkId === t.mark.id;
          return (
            <li
              key={t.mark.id}
              className="rounded border border-brand-primary/40 bg-bg-base p-1.5"
              data-testid={`special-visit-ticket-${t.mark.id}`}
            >
              <div className="flex flex-wrap items-center gap-1">
                <button
                  type="button"
                  className="flex-1 truncate rounded px-1 py-0.5 text-left text-xs font-medium text-text-primary hover:bg-bg-muted focus:bg-bg-muted focus:outline-none"
                  onClick={() => setOpenMarkId(open ? null : t.mark.id)}
                  data-testid={`special-visit-ticket-toggle-${t.mark.id}`}
                >
                  {t.patient.name} 様・{weekdayLabel(t.mark.weekday)}曜
                </button>
                <Badge
                  variant={t.mark.kind === 'displaced' ? 'warning' : 'info'}
                  className="text-[10px]"
                  data-testid={`special-visit-ticket-kind-${t.mark.id}`}
                >
                  {t.mark.kind === 'displaced' ? '固定退避' : '追加枠'}
                </Badge>
                <Badge variant="secondary" className="text-[10px]">
                  週{t.period.weekly_target}回以上
                </Badge>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setCalendarPatient({ id: t.patient.id, name: t.patient.name })}
                  className="h-6 px-1.5 text-[10px]"
                  title="特別訪問週間のカレンダーを開く"
                  data-testid={`special-visit-ticket-calendar-${t.mark.id}`}
                >
                  <CalendarDays className="mr-0.5 h-3 w-3" aria-hidden />
                  カレンダー
                </Button>
              </div>
              {open ? (
                <SpecialTicketPlacePanel
                  ticket={t}
                  officeId={officeId}
                  canEdit={canEdit}
                  onPlaced={() => setOpenMarkId(null)}
                />
              ) : null}
            </li>
          );
        })}
      </ul>

      {calendarPatient ? (
        <SpecialVisitWeekDialog
          patientId={calendarPatient.id}
          patientName={calendarPatient.name}
          open
          onOpenChange={(v) => {
            if (!v) setCalendarPatient(null);
          }}
        />
      ) : null}
    </div>
  );
}
