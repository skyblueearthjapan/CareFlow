'use client';

/**
 * SyncBar — カイポケ同期ストリップ (方向性A・docs/mockups/sync-strip-mock.html)。
 *
 * 普段は **1 行だけ**:
 *   「カイポケ同期」+ 状態バッジ (✓カイポケと同じ / ⚠差分あり / ?未確認) +
 *   件数チップ (カイポケから N・らく助から M・要確認 K) +
 *   [⇩ カイポケから取り込む N] [⇧ カイポケへ送る M] [🔄 同期確認]
 *
 * ボタンを押した時だけ、その作業のパネルが直下に開く (同時に 1 つ):
 *   ⇩取り込む … 🔄同期確認で見つかった「カイポケ側が違う」差分をカード行で並べる。
 *                行を選ぶと「何から何へ」表 + 盤面ゴースト。未確認なら開いた時に自動開始。
 *   ⇧送る     … ●未送信 (らく助で変えた分) をカード行で並べる。当日以前は非表示、
 *                自動送信不可 (RPA 未対応) は薄く + 理由。
 *   🔄同期確認 … 押すと実行 → らく助の作業中演出 → 完了後はサマリ 3 カード +
 *                「要確認」(サービス内容のズレ / 資格) + 👥名簿の詳細。
 *
 * ロジックは従来どおり:
 *   ●未送信 = `useUnsentSummary` (RPA なし・保存済み CSV との差分)
 *   🔄同期確認 = `useKaipokeReconcile` (イベント → 訪問 → 全曜日差分の直列)
 * 当日以前 (JST) は実績が付いている可能性があるため送信対象外 (BE も 422)。
 */
import * as React from 'react';
import { toast } from 'sonner';

import { RakusukeWorking } from '@/components/brand/Rakusuke';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useSendEventsOutbound, useStartApply } from '@/lib/queries/integrations';
import { useUnsentSummary, useVisitServiceOverride } from '@/lib/queries/cockpit';
import { useUpdateStaff } from '@/lib/queries/staff';
import type { MasterReconcileQualification } from '@/lib/schemas/integration';
import { STAFF_QUALIFICATION_VALUES, type StaffQualification } from '@/lib/schemas/staff';
import type { CockpitCorrectionItem, UnsentSummaryRead } from '@/lib/schemas/v2/cockpit';
import { DiffDetailCard } from './DiffDetailCard';
import {
  correctionItemToMarker,
  fmtMd,
  itemField,
  itemSide,
  resolveDayInWeek,
  unsentEventKey,
  unsentEventToMarker,
  unsentVisitKey,
  type CockpitMarker,
  type CockpitMarkerSide,
} from './reconcileMarkers';
import { SyncStripRow, type SyncRowTone } from './SyncStripRow';
import { useKaipokeReconcile, type CockpitDiff, type ReconcilePhase } from './useKaipokeReconcile';
import { cn } from '@/lib/utils';

/** 全件ボタンの 2 段クリック猶予 (モックと同じ 3 秒)。 */
const ARM_MS = 3_000;

/** JST の「今日」(BE と同じ Asia/Tokyo 基準)。 */
export function todayIsoJst(): string {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Tokyo' }).format(new Date());
}

/** 開いているパネル (同時に 1 つ)。 */
type PanelKey = 'in' | 'out' | 'check';

type DiffAction = 'add' | 'update' | 'delete';

const ACTION_TAG: Record<DiffAction, { label: string; tone: SyncRowTone }> = {
  add: { label: '新規', tone: 'add' },
  update: { label: '変更', tone: 'update' },
  delete: { label: '取消', tone: 'delete' },
};

/** ⇩ 取り込む側の補足 (カイポケが正)。 */
const IN_NOTE: Record<DiffAction, string> = {
  add: 'カイポケにだけある',
  update: 'カイポケ側で変わっている',
  delete: 'カイポケで消えている',
};

/** ⇧ 送る側の補足 (らく助が正)。 */
const OUT_NOTE: Record<DiffAction, string> = {
  add: 'らく助にだけある',
  update: 'らく助で変えました',
  delete: 'らく助で取消しました',
};

/**
 * RPA 未対応の行に付ける理由 (S3 完了まで・kaipoke-service-content-design.md §3)。
 * 准看/一般のサービス内容は RPA が固定値で登録してしまうため送らない。
 * 判定そのものは BE (`rpa_unsupported`) が持ち、FE は表示だけ。
 */
const RPA_UNSUPPORTED_NOTE =
  '准看護師／一般の登録はRPAが未対応です。カイポケで直接登録してください';

/**
 * 担当なしの行に付ける理由 (2026-09-03 の事故)。カイポケのスケジュール表CSVは
 * 職員未割当の行を出さないため、送っても確認できず add を繰り返す。
 * 判定そのものは BE (`unassigned`) が持ち、FE は表示だけ。
 */
const UNASSIGNED_NOTE = '担当が付いていない予定はカイポケへ送れません。先に担当を付けてください';

/** BE の差分 action → 表示上の 3 種別。 */
function diffAction(action: string): DiffAction {
  if (action === 'add') return 'add';
  if (action === 'delete') return 'delete';
  return 'update'; // edit / date_change
}

export interface SyncBarProps {
  /** 対象週の月曜 (YYYY-MM-DD)。 */
  weekStartIso: string;
  canEdit: boolean;
  /** CSV の氏名 → staff_id (ゴーストのセル解決用)。 */
  staffIdByName?: Map<string, string>;
  /** staff_id → 氏名 (差分カードの表示用)。 */
  staffNameById?: Map<string, string>;
  /** 選択中の差分を親へ (盤面ゴースト)。null = 選択解除。 */
  onSelectDiff: (marker: CockpitMarker | null) => void;
  /**
   * ●未送信の突合キー集合を親へ (盤面/タイムラインの ● ドット)。
   * 訪問 = `unsentVisitKey(日付, 開始時刻, 患者名)` / イベント = `unsentEventKey(id)`。
   * **未取得の間は呼ばない** — 親は「まだ分からない」と「送信済み」を区別する。
   */
  onUnsentChange?: (keys: Set<string>) => void;
  /** 値が変わると未送信を数え直す (盤面を操作した直後など)。 */
  reloadKey?: number;
  /** RPA が空いていれば自動で 1 回だけ同期確認を始める。 */
  autoStartReconcile?: boolean;
  className?: string;
}

interface UnsentRow {
  id: string;
  kind: 'visit' | 'event';
  action: DiffAction;
  dateIso: string | null;
  /** 「久須見 様 14:00」など。 */
  headline: string;
  /** 「担当 熊澤 → 佐藤」など (無ければ空)。 */
  change: string;
  marker: CockpitMarker | null;
  /** 盤面の予定と突き合わせるキー (● ドット用)。解決できないときは null。 */
  matchKey: string | null;
  /**
   * RPA がカイポケへ正しく登録できない行 (S3 完了まで・BE 判定)。
   * true の間は送信対象から外す。
   */
  rpaUnsupported: boolean;
  /**
   * 担当が付いていない行 (職員1が空/'-'・BE 判定)。
   * カイポケへ送っても現況CSVに出てこないため送信対象から外す。
   */
  unassigned: boolean;
}

/**
 * 「サービス内容のズレ」= 同じ訪問 (日付/開始時刻/患者) について delete と add が
 * 両方立ち、**サービス内容だけ** が違うペア。
 *
 * カイポケの編集ダイアログはサービス内容を触れないため、差分エンジンは
 * この違いを必ず delete + add で表現する (設計 §3-1)。生のまま見せると
 * 「取消して新規登録する」ように読めて怖いので、1 行に束ねて
 * 「らく助はこう / カイポケはこう」と並べる。
 */
interface ServiceMismatchPair {
  key: string;
  dateIso: string;
  patientName: string;
  startTime: string;
  /** らく助が出したサービス内容 (add 側)。 */
  rakusuke: string;
  /** カイポケに入っているサービス内容 (delete 側)。 */
  kaipoke: string;
  addItemId: string;
  deleteItemId: string;
}

/** ペアかどうかの判定に使う「サービス内容以外」の項目。 */
const PAIR_SAME_FIELDS = ['end_time', 'staff1', 'staff2', 'business_type'] as const;

/** 差分の片側の担当者名 (staff_id で引けなければ CSV の氏名)。 */
function sideStaffName(
  side: CockpitMarkerSide | undefined,
  staffNameById?: Map<string, string>,
): string {
  if (!side) return '';
  if (side.staff_id && staffNameById?.has(side.staff_id)) return staffNameById.get(side.staff_id)!;
  return side.staff_name ?? '';
}

/** マーカー → 「誰が・何が・どう変わる」の 1 文 (カード行の中央)。 */
function describeMarker(
  m: CockpitMarker,
  staffNameById?: Map<string, string>,
): { headline: string; change: string } {
  const anchor = m.after ?? m.before;
  const start = anchor?.start ?? m.start ?? '';
  const headline =
    m.kind === 'visit'
      ? `${m.patient_name ?? m.title} 様 ${start}`.trim()
      : `${sideStaffName(anchor, staffNameById) || '（担当不明）'} ${start} ${m.title}`.trim();

  const chg: string[] = [];
  if (m.before && m.after) {
    if (m.before.date !== m.after.date) {
      chg.push(`日付 ${fmtMd(m.before.date)} → ${fmtMd(m.after.date)}`);
    }
    if (m.before.start !== m.after.start || m.before.end !== m.after.end) {
      chg.push(`時刻 ${m.before.start}〜${m.before.end} → ${m.after.start}〜${m.after.end}`);
    }
    const b = sideStaffName(m.before, staffNameById);
    const a = sideStaffName(m.after, staffNameById);
    if (b !== a) chg.push(`担当 ${b || '—'} → ${a || '—'}`);
    if ((m.before.course_label ?? '') !== (m.after.course_label ?? '')) {
      chg.push(`サービス ${m.before.course_label || '—'} → ${m.after.course_label || '—'}`);
    }
  }
  return { headline, change: chg.join('・') };
}

/** マーカーが載る日付 (after 優先 = 変わった後の置き場)。 */
function markerDateIso(m: CockpitMarker): string | null {
  return m.after?.date ?? m.before?.date ?? null;
}

type StepState = 'todo' | 'now' | 'done';

/** 同期確認の進捗チップ (ログイン → イベント → 訪問 → 差分計算)。 */
function progressSteps(
  phase: ReconcilePhase,
  busyKey: string | null,
): { label: string; state: StepState }[] {
  const started = phase !== 'idle';
  const diffing = busyKey === '__in_diff__';
  const ready = phase === 'ready';
  const st = (now: boolean, done: boolean): StepState => (now ? 'now' : done ? 'done' : 'todo');
  return [
    { label: 'ログイン', state: st(false, started) },
    { label: 'イベント', state: st(phase === 'events', phase === 'visits' || ready) },
    { label: '訪問', state: st(phase === 'visits', ready) },
    { label: '差分計算', state: st(diffing, ready && !diffing) },
  ];
}

const STEP_CLS: Record<StepState, string> = {
  todo: 'border-border-default text-text-muted',
  now: 'border-brand-primary font-bold text-brand-primary',
  done: 'border-success text-success',
};

export function SyncBar({
  weekStartIso,
  canEdit,
  staffIdByName,
  staffNameById,
  onSelectDiff,
  onUnsentChange,
  reloadKey = 0,
  autoStartReconcile = false,
  className,
}: SyncBarProps) {
  const todayIso = todayIsoJst();

  // ─── ⇧ 送る: ●未送信 ───
  const unsentMut = useUnsentSummary();
  const serviceOverrideMut = useVisitServiceOverride();
  const startApplyMut = useStartApply();
  const sendEventsMut = useSendEventsOutbound();
  const [summary, setSummary] = React.useState<UnsentSummaryRead | null>(null);
  /**
   * 送信済みの行 (この画面で ⇧ を押したもの)。
   *
   * **item.id ではなく訪問キー** (`unsentVisitKey` / `unsentEventKey`) で持つ:
   * ●未送信は毎回シートを作り直すので item.id は再計算のたびに変わる。id で
   * 覚えると再計算した瞬間に「送ったはずの行」が復活して見え、二重送信を
   * 誘う (BE の再送ガードで 422 になるだけで、操作者には理由が分からない)。
   */
  const [sentKeys, setSentKeys] = React.useState<Set<string>>(new Set());
  const [unsentId, setUnsentId] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [armedKey, setArmedKey] = React.useState<string | null>(null);
  /** 開いているパネル (押した時だけ・同時に 1 つ)。 */
  const [openPanel, setOpenPanel] = React.useState<PanelKey | null>(null);
  /** 👥名簿の詳細 (普段は畳んでおく)。 */
  const [rosterOpen, setRosterOpen] = React.useState(false);
  // 資格の「カイポケの職種を採用」(👥名簿の詳細)。突合は ~1 分かかるので
  // 採用済みは結果を取り直さず、FE 側で行を消す。
  const updateStaffMut = useUpdateStaff();
  const [adoptingStaffId, setAdoptingStaffId] = React.useState<string | null>(null);
  const [adoptedStaffIds, setAdoptedStaffIds] = React.useState<Set<string>>(new Set());
  // 資格はマスタ = その職員の **全訪問** のサービス内容が変わる。押した瞬間に
  // 書き換えず、影響範囲を見せてから確定させる。
  const [adoptTarget, setAdoptTarget] = React.useState<MasterReconcileQualification | null>(null);

  /**
   * ●未送信を数え直す。
   *
   * `keepSent` (既定 true) の間は「この画面で送った」印を保持する。カイポケへの
   * 反映は RPA が非同期に行うため、送信直後に数え直しても **まだカイポケには
   * 入っていない** = 同じ行がもう一度未送信として返ってくる。ここで印を消すと
   * 送ったばかりの行が復活し、二重送信を誘う。
   *
   * 印を落としてよいのは **🔄同期確認が終わったとき** だけ = カイポケの現況を
   * 実際に見直した後なら、まだ残っている行は「本当にまだ送れていない」行。
   */
  const loadUnsent = React.useCallback(
    async ({ keepSent = true }: { keepSent?: boolean } = {}) => {
      setBusy('__unsent_load__');
      try {
        const res = await unsentMut.mutateAsync({ week_start: weekStartIso });
        // 競合ガード: 週を切り替えた直後に前の週の応答が届いても捨てる。
        if (res.week_start !== weekStartIso) return;
        setSummary(res);
        if (!keepSent) setSentKeys(new Set());
      } catch (err) {
        toast.error(
          `未送信の確認に失敗しました: ${err instanceof Error ? err.message : String(err)}`,
        );
      } finally {
        setBusy(null);
      }
    },
    // unsentMut は毎レンダー新しい参照になるため依存に入れない
    // (入れると loadUnsent が毎回作り直され、useEffect が無限に走る)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [weekStartIso],
  );

  React.useEffect(() => {
    if (!canEdit) return;
    // 週が変われば別の話なので印も捨てる (reloadKey の再計算では保持)。
    setSentKeys(new Set());
    void loadUnsent({ keepSent: false });
    // reloadKey: 盤面を操作した直後に親が加算する (●未送信を数え直す)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weekStartIso, canEdit]);

  React.useEffect(() => {
    if (!canEdit || reloadKey === 0) return;
    void loadUnsent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  // ─── ⇩ 取り込む / 🔄 同期確認 (終わったら未送信も数え直す) ───
  const rec = useKaipokeReconcile({
    weekStartIso,
    canEdit,
    staffIdByName,
    autoStart: autoStartReconcile,
    // 同期確認＝カイポケの現況を見直した後なので、ここでだけ送信済みの印を落とす。
    onReady: () => void loadUnsent({ keepSent: false }),
  });
  const [inId, setInId] = React.useState<string | null>(null);

  /** 差分行 → 送信済み判定に使う訪問キー (解決できなければ null)。 */
  const visitSentKey = React.useCallback(
    (it: CockpitCorrectionItem): string | null => {
      const side = itemSide(it.action);
      const dateIso = it.date_iso ?? resolveDayInWeek(itemField(it, 'date', side), weekStartIso);
      const startTime = itemField(it, 'start_time', side);
      const who = itemField(it, 'user_name', side);
      return dateIso && startTime && who ? unsentVisitKey(dateIso, startTime, who) : null;
    },
    [weekStartIso],
  );

  const isSent = React.useCallback(
    (it: CockpitCorrectionItem): boolean => {
      const key = visitSentKey(it);
      return key != null && sentKeys.has(key);
    },
    [visitSentKey, sentKeys],
  );

  const unsentRows = React.useMemo<UnsentRow[]>(() => {
    if (!summary) return [];
    const rows: UnsentRow[] = [];
    for (const it of summary.items) {
      if (isSent(it)) continue;
      const side = itemSide(it.action);
      const dateIso = it.date_iso ?? resolveDayInWeek(itemField(it, 'date', side), weekStartIso);
      const who = itemField(it, 'user_name', side) || '（患者不明）';
      const staff = itemField(it, 'staff1', side);
      const startTime = itemField(it, 'start_time', side);
      const marker = correctionItemToMarker(it, { weekStartIso, staffIdByName });
      const desc = marker ? describeMarker(marker, staffNameById) : null;
      rows.push({
        id: it.id,
        kind: 'visit',
        action: diffAction(it.action),
        dateIso,
        // 日付/時刻/患者名で盤面の訪問と突き合わせる。**側は action で決まる**
        // (delete は before / add は after) — after 固定だと delete 行の
        // 日付と時刻が空文字になり突合できない。
        matchKey: dateIso && startTime ? unsentVisitKey(dateIso, startTime, who) : null,
        marker,
        rpaUnsupported: it.rpa_unsupported === true,
        unassigned: it.unassigned === true,
        headline: `${who} 様 ${startTime}`.trim(),
        change: desc?.change || (staff ? `担当 ${staff}` : ''),
      });
    }
    for (const ev of summary.events) {
      if (sentKeys.has(unsentEventKey(ev.id))) continue;
      rows.push({
        id: ev.id,
        kind: 'event',
        action: ev.kind === 'add' ? 'add' : 'delete',
        dateIso: ev.date,
        matchKey: unsentEventKey(ev.id),
        marker: unsentEventToMarker(ev),
        // イベントはサービス内容を持たない = RPA 未対応ガードの対象外。
        rpaUnsupported: false,
        // イベントは職員に紐づく = 担当なしになりようがない。
        unassigned: false,
        headline: `${ev.staff_name} ${ev.start_time} ${ev.title}`.trim(),
        change: '',
      });
    }
    return rows;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary, sentKeys, weekStartIso, staffIdByName, staffNameById]);

  // ─── サービス内容のズレ (delete + add のペアを 1 行に束ねる) ───
  const serviceMismatches = React.useMemo<ServiceMismatchPair[]>(() => {
    if (!summary) return [];
    // BE は反対側を空文字で埋める (delete の after は空 / add の before は空)。
    // side を明示しないと delete の日付・時刻が空文字になり、ペアが永久に
    // 成立しない (2026-08-23 レビュー C1)。
    const field = (it: (typeof summary.items)[number], key: string) =>
      itemField(it, key, itemSide(it.action));

    const groups = new Map<string, typeof summary.items>();
    for (const it of summary.items) {
      if (isSent(it)) continue;
      if (it.action !== 'add' && it.action !== 'delete') continue;
      const dateIso = it.date_iso ?? resolveDayInWeek(field(it, 'date'), weekStartIso);
      const startTime = field(it, 'start_time');
      const who = field(it, 'user_name');
      if (!dateIso || !startTime || !who) continue;
      const key = unsentVisitKey(dateIso, startTime, who);
      const bucket = groups.get(key);
      if (bucket) bucket.push(it);
      else groups.set(key, [it]);
    }

    const pairs: ServiceMismatchPair[] = [];
    for (const [key, items] of groups) {
      const adds = items.filter((i) => i.action === 'add');
      const deletes = items.filter((i) => i.action === 'delete');
      // 1 対 1 のときだけ束ねる。複数あるとどれとどれが同じ訪問か決められない
      // (当てずっぽうで束ねるより、生の delete/add のまま見せる方が安全)。
      const add = adds.length === 1 ? adds[0] : undefined;
      const del = deletes.length === 1 ? deletes[0] : undefined;
      if (!add || !del) continue;
      const rakusuke = field(add, 'service_type');
      const kaipoke = field(del, 'service_type');
      if (!rakusuke || !kaipoke || rakusuke === kaipoke) continue;
      // サービス内容 **だけ** が違うことを確かめる (担当や時刻も違うなら
      // それは本当の予定変更で、サービス内容の問題ではない)。
      // 比較は add.after vs delete.before = それぞれの「実体」側どうし。
      if (PAIR_SAME_FIELDS.some((f) => field(add, f) !== field(del, f))) continue;
      pairs.push({
        key,
        dateIso: add.date_iso ?? resolveDayInWeek(field(add, 'date'), weekStartIso) ?? '',
        patientName: field(add, 'user_name'),
        startTime: field(add, 'start_time'),
        rakusuke,
        kaipoke,
        addItemId: add.id,
        deleteItemId: del.id,
      });
    }
    return pairs.sort((a, b) => a.key.localeCompare(b.key));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary, sentKeys, weekStartIso]);

  /**
   * 「この訪問だけカイポケに合わせる」= 訪問単位の上書き (mig 0078)。
   * visit の特定は **BE 側**: delete 側 item の日付/開始時刻/患者名から引く
   * (氏名の正規化ルールを FE に二重化しない)。適用後は未送信を数え直す。
   */
  const applyServiceContent = async (pair: ServiceMismatchPair) => {
    setBusy(`__svc__${pair.key}`);
    try {
      await serviceOverrideMut.mutateAsync({
        item_id: pair.deleteItemId,
        service_content: pair.kaipoke,
      });
      toast.success(
        `${pair.patientName}様の ${fmtMd(pair.dateIso)} ${pair.startTime} の訪問を` +
          `カイポケのサービス内容「${pair.kaipoke}」に合わせました`,
      );
      await loadUnsent();
    } catch (err) {
      toast.error(
        `サービス内容の変更に失敗しました: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setBusy(null);
    }
  };

  // ─── 資格のズレ / 未設定 (👥名簿の突合結果から) ───
  // `match` は出さない (一致は見ても仕方がない)。`unknown_staff` は氏名側の
  // 「カイポケのみ」で既に見えているので、ここでは二重に出さない。
  const qualificationMismatches = (rec.masterResult?.staffQualifications ?? []).filter(
    (q) => q.status === 'mismatch',
  );
  // 採用済みは即座に消す (👥突合は ~1 分かかるので結果を取り直さない)。
  const qualificationMissing = (rec.masterResult?.staffQualifications ?? []).filter(
    (q) => q.status === 'missing_in_rakusuke' && !(q.staffId && adoptedStaffIds.has(q.staffId)),
  );
  // 同じ名前の在職スタッフが複数 = 誰の資格か決められない (採用ボタンは出さない)。
  const qualificationAmbiguous = (rec.masterResult?.staffQualifications ?? []).filter(
    (q) => q.status === 'ambiguous',
  );
  /** カイポケ側の職種 (氏名 → 職種)。「カイポケのみ」行に添えるのに使う。 */
  const kaipokeQualificationByName = new Map(
    (rec.masterResult?.staffQualifications ?? [])
      .filter((q) => q.status === 'unknown_staff' && q.kaipokeQualification)
      .map((q) => [q.name, q.kaipokeQualification as string]),
  );

  /** らく助が扱える資格か (カイポケに未知の職種があっても PATCH を投げない)。 */
  const isKnownQualification = (v: string | null): v is StaffQualification =>
    v != null && (STAFF_QUALIFICATION_VALUES as readonly string[]).includes(v);

  /** 「カイポケの職種を採用」= らく助の staff.qualification を埋める (既存 PATCH)。 */
  const adoptQualification = async (q: MasterReconcileQualification) => {
    const staffId = q.staffId;
    if (staffId == null || !isKnownQualification(q.kaipokeQualification)) return;
    setAdoptingStaffId(staffId);
    try {
      await updateStaffMut.mutateAsync({
        id: staffId,
        payload: { qualification: q.kaipokeQualification },
      });
      setAdoptedStaffIds((prev) => new Set(prev).add(staffId));
      toast.success(`${q.name} の資格を「${q.kaipokeQualification}」にしました`);
    } catch (err) {
      toast.error(`資格の更新に失敗しました: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setAdoptingStaffId(null);
    }
  };

  /** BE の sendable 判定と同じ式 (date 不明は送信可・past = 当日以前のみ)。
   *  RPA 未対応 (准看/一般) と担当なしの行は BE が apply でも弾くのでここでも外す
   *  = 「送れると見えたのに BE がスキップした」ズレを作らない。 */
  const isSendable = (r: UnsentRow) =>
    !r.rpaUnsupported && !r.unassigned && (r.dateIso == null || r.dateIso > todayIso);
  const sendableRows = unsentRows.filter(isSendable);
  /** 当日以前は実績保護のため一覧に出さない (件数だけ注記する)。 */
  const pastRows = unsentRows.filter((r) => r.dateIso != null && r.dateIso <= todayIso);
  const outRows = unsentRows.filter((r) => r.dateIso == null || r.dateIso > todayIso);

  const selectedUnsent = outRows.find((r) => r.id === unsentId) ?? null;
  const selectedDiff: CockpitDiff | null = rec.diffs.find((d) => d.id === inId) ?? null;

  // ─── 選択中マーカーを親へ (盤面ゴースト・カードと必ず一致させる) ───
  const activeMarker =
    openPanel === 'in'
      ? (selectedDiff?.marker ?? null)
      : openPanel === 'out'
        ? (selectedUnsent?.marker ?? null)
        : null;
  const onSelectDiffRef = React.useRef(onSelectDiff);
  onSelectDiffRef.current = onSelectDiff;
  React.useEffect(() => {
    onSelectDiffRef.current(activeMarker);
  }, [activeMarker]);

  // ─── ●未送信のキー集合を親へ (盤面/タイムラインの ● ドット) ───
  // summary を一度も取れていない間は呼ばない = 親は「まだ分からない」を保てる。
  const onUnsentChangeRef = React.useRef(onUnsentChange);
  onUnsentChangeRef.current = onUnsentChange;
  const unsentKeysSignature = unsentRows
    .map((r) => r.matchKey)
    .filter((k): k is string => k != null)
    .sort()
    .join('\n');
  React.useEffect(() => {
    if (summary == null) return;
    onUnsentChangeRef.current?.(
      new Set(unsentKeysSignature === '' ? [] : unsentKeysSignature.split('\n')),
    );
  }, [unsentKeysSignature, summary]);

  /** 送信済みの印を付ける (訪問キー基準 — item.id は再計算で変わるため)。 */
  const markSent = (rows: UnsentRow[]) =>
    setSentKeys((prev) => {
      const next = new Set(prev);
      for (const r of rows) {
        if (r.matchKey != null) next.add(r.matchKey);
      }
      return next;
    });

  const sendUnsent = async (rows: UnsentRow[], key: string) => {
    if (rows.length === 0) return;
    const visitIds = rows.filter((r) => r.kind === 'visit').map((r) => r.id);
    const eventIds = rows.filter((r) => r.kind === 'event').map((r) => r.id);
    setBusy(key);
    try {
      if (visitIds.length > 0) {
        if (!summary?.sheet_id) {
          throw new Error(
            '送信用の差分シートがありません（🔄同期確認でカイポケ現況を取得してください）',
          );
        }
        await startApplyMut.mutateAsync({
          sheetId: summary.sheet_id,
          dryRun: false,
          itemIds: visitIds,
        });
      }
      if (eventIds.length > 0) {
        await sendEventsMut.mutateAsync({ weekStart: weekStartIso, eventIds });
      }
      markSent(rows);
      setUnsentId(null);
      toast.success(
        `カイポケへ ${rows.length} 件の送信を始めました（1件あたり約30〜60秒）。` +
          'そのまま見守っていて大丈夫です',
      );
    } catch (err) {
      toast.error(`送信に失敗しました: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(null);
    }
  };

  /** 2 段クリック確認 (外部システムへの実書込・全件系)。 */
  const armTimerRef = React.useRef<number | null>(null);
  React.useEffect(
    () => () => {
      if (armTimerRef.current != null) window.clearTimeout(armTimerRef.current);
    },
    [],
  );
  const arm = (key: string, run: () => void) => {
    if (armTimerRef.current != null) window.clearTimeout(armTimerRef.current);
    if (armedKey === key) {
      armTimerRef.current = null;
      setArmedKey(null);
      run();
      return;
    }
    setArmedKey(key);
    armTimerRef.current = window.setTimeout(() => {
      armTimerRef.current = null;
      setArmedKey((cur) => (cur === key ? null : cur));
    }, ARM_MS);
  };

  const fetching = rec.phase === 'events' || rec.phase === 'visits';
  // busy = らく助が何かしている間。同期確認系も送信系もこの間は押させない。
  const running = fetching || busy != null || rec.busyKey != null;
  const workingLabel =
    rec.phase === 'events'
      ? 'らく助がカイポケのイベントを読んでいます'
      : rec.phase === 'visits'
        ? 'らく助がカイポケの訪問を読んでいます'
        : rec.busyKey === '__in_diff__'
          ? 'らく助が全曜日の差分を数えています'
          : rec.busyKey === '__master__'
            ? 'らく助が名簿を突き合わせています'
            : busy === '__unsent_load__'
              ? 'らく助が未送信を数えています'
              : 'らく助がカイポケに入力しています';

  // ─── ストリップの状態バッジ・件数 ───
  const inCount = rec.diffs.length;
  const outCount = sendableRows.length;
  const needsCheckCount =
    serviceMismatches.length +
    qualificationMismatches.length +
    qualificationMissing.length +
    qualificationAmbiguous.length;
  const noSnapshot = summary != null && summary.snapshot == null;
  const checkedAt = rec.fetchedAt
    ? `${rec.fetchedAt.getHours()}:${String(rec.fetchedAt.getMinutes()).padStart(2, '0')}`
    : null;
  const statusText = running
    ? 'らく助が確認中…'
    : noSnapshot && rec.phase !== 'ready'
      ? '? 未確認（カイポケ側の控えがありません）'
      : inCount + outCount + needsCheckCount > 0
        ? '⚠ 差分あり'
        : '✓ カイポケと同じ';
  const statusCls = running
    ? 'bg-brand-primary-50 text-brand-primary-hover'
    : noSnapshot && rec.phase !== 'ready'
      ? 'bg-bg-muted text-text-secondary'
      : inCount + outCount + needsCheckCount > 0
        ? 'bg-warning-bg text-warning-strong'
        : 'bg-success-bg text-success';

  const stripBtnCls = 'h-8 gap-1.5 px-3 text-[13px] font-bold';
  const actionsDisabled = !canEdit || running || rec.rpaRunning;

  /** ストリップの 3 ボタン: 押した時だけパネルを開く (同時に 1 つ)。 */
  const togglePanel = (key: PanelKey) => {
    const next = openPanel === key ? null : key;
    setOpenPanel(next);
    // ⇩ は同期確認の結果を使う。まだなら開いた時に自動で始める。
    if (next === 'in' && rec.phase === 'idle' && canEdit && !rec.rpaRunning && !running) {
      void rec.runFetch();
    }
  };

  /** らく助の作業中演出 (同期確認/取り込む/送る の各パネル共通)。 */
  const workingBlock = running ? (
    <div className="mt-2 space-y-2" data-testid="sync-working">
      <RakusukeWorking
        message={workingLabel}
        sub="約2〜3分。その間も盤面は見られます"
        pose="calendar"
      />
      <div className="flex flex-wrap gap-1.5" data-testid="sync-progress">
        {progressSteps(rec.phase, rec.busyKey).map((s) => (
          <span
            key={s.label}
            className={cn('rounded-full border px-2.5 py-0.5 text-[12px]', STEP_CLS[s.state])}
          >
            {s.label}
          </span>
        ))}
      </div>
    </div>
  ) : null;

  const panelTitleCls = 'flex flex-wrap items-center gap-2 text-[15px] font-bold text-text-primary';
  const closeButton = (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      className="ml-auto h-7 px-2 text-[12px] text-text-muted"
      data-testid="sync-panel-close"
      onClick={() => setOpenPanel(null)}
      title="パネルをたたみます（結果は残ります。ボタンでいつでも開けます）"
    >
      ▴ たたむ
    </Button>
  );

  return (
    <section
      className={cn('rounded-lg border border-border-default bg-bg-muted', className)}
      data-testid="sync-bar"
      aria-label="カイポケとの同期"
    >
      {/* ── ストリップ (常時 1 行) ── */}
      <div
        className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 px-3 py-2"
        data-testid="sync-strip"
      >
        <span className="text-[13px] font-bold text-text-primary">カイポケ同期</span>
        <span
          className={cn(
            'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[13px] font-bold',
            statusCls,
          )}
          data-testid="sync-status"
        >
          {statusText}
          {checkedAt ? (
            <small className="font-normal text-text-muted">
              最終確認 {checkedAt}
              {rec.stale ? '（古い可能性）' : ''}
            </small>
          ) : null}
        </span>
        <span
          className="flex flex-wrap items-center gap-2 text-[12px] text-text-secondary"
          data-testid="sync-counts"
        >
          <span>
            カイポケから{' '}
            <b className="tnum rounded-full bg-brand-primary-50 px-1.5 text-brand-primary-hover">
              {inCount}
            </b>
          </span>
          <span>
            らく助から{' '}
            <b className="tnum rounded-full bg-info-bg px-1.5 text-info-strong">{outCount}</b>
          </span>
          <span>
            要確認{' '}
            <b className="tnum rounded-full bg-warning-bg px-1.5 text-warning-strong">
              {needsCheckCount}
            </b>
          </span>
        </span>

        <span className="flex-1" />

        <Button
          type="button"
          size="sm"
          variant={openPanel === 'in' ? 'default' : 'outline'}
          className={stripBtnCls}
          disabled={running || rec.rpaRunning}
          aria-expanded={openPanel === 'in'}
          data-testid="sync-open-in"
          title="カイポケ側で変わっている予定をらく助へ取り込みます"
          onClick={() => togglePanel('in')}
        >
          ⇩ カイポケから取り込む <span className="tnum">{inCount}</span>
        </Button>
        <Button
          type="button"
          size="sm"
          variant={openPanel === 'out' ? 'default' : 'outline'}
          className={stripBtnCls}
          disabled={running || rec.rpaRunning}
          aria-expanded={openPanel === 'out'}
          data-testid="sync-open-out"
          title="らく助で変えた予定をカイポケへ送ります（明日以降のみ）"
          onClick={() => togglePanel('out')}
        >
          ⇧ カイポケへ送る <span className="tnum">{outCount}</span>
        </Button>
        <Button
          type="button"
          size="sm"
          variant={openPanel === 'check' ? 'default' : 'outline'}
          className={stripBtnCls}
          disabled={!canEdit || running || rec.rpaRunning}
          aria-expanded={openPanel === 'check'}
          data-testid="sync-open-check"
          title={
            rec.rpaRunning
              ? 'カイポケ連携が実行中です（単一スロット）。完了後に実行してください'
              : 'カイポケの当週データを読んで、らく助と突き合わせます（2〜3分）'
          }
          onClick={() => {
            // 折りたたみ: 結果があれば開閉だけ (再実行はパネル内の「再確認」)。
            // 未実行 (idle/error) のときだけ、開くと同時に実行する。
            const next = openPanel === 'check' ? null : 'check';
            setOpenPanel(next);
            if (next === 'check' && (rec.phase === 'idle' || rec.phase === 'error')) {
              void rec.runFetch();
            }
          }}
        >
          🔄 同期確認
        </Button>
      </div>

      {rec.error ? (
        <p className="px-3 pb-2 text-[12px] text-error" data-testid="sync-error">
          {rec.error}
        </p>
      ) : null}

      {/* ── ⇩ 取り込む パネル ── */}
      {openPanel === 'in' ? (
        <div
          className="border-t border-border-subtle bg-bg-base px-3.5 py-3"
          data-testid="sync-panel-in"
        >
          <h3 className={panelTitleCls}>
            ⇩ カイポケから取り込む
            <small className="text-[12px] font-normal text-text-muted">
              カイポケ側で変わっている予定（らく助に反映する）
            </small>
            {closeButton}
          </h3>

          {rec.sheetApplied ? (
            // 適用済みシートに ⇩ を押すと BE が 409 を返す。押させ続けても直らないので
            // ここで止めて再突合へ誘導する (2026-08-24 本番の実録)。
            <div
              className="mt-2 flex flex-wrap items-center gap-2 rounded-md bg-warning-bg px-2.5 py-2 text-[13px] text-warning-strong"
              data-testid="sync-in-sheet-applied"
            >
              <span>
                この差分は既に取り込み済みです。🔄 同期確認 をもう一度実行して最新の差分を取得してください
              </span>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-7 px-2.5 text-[12px]"
                disabled={!canEdit || running || rec.rpaRunning}
                data-testid="sync-in-refetch"
                onClick={() => void rec.runFetch()}
              >
                🔄 同期確認をやり直す
              </Button>
            </div>
          ) : null}

          {running && rec.diffs.length === 0 ? (
            workingBlock
          ) : rec.diffs.length === 0 ? (
            <p className="mt-2 text-[13px] text-text-muted" data-testid="sync-in-empty">
              {rec.phase === 'ready'
                ? 'カイポケ側で変わっている予定はありません。'
                : '🔄 同期確認を実行すると、カイポケ側の変更がここに並びます。'}
            </p>
          ) : (
            <ul className="mt-2 space-y-1.5">
              {rec.diffs.map((d) => {
                const act = d.marker.action;
                const desc = describeMarker(d.marker, staffNameById);
                const dateIso = markerDateIso(d.marker);
                const selected = selectedDiff?.id === d.id;
                // 訪問差分は取込差分シート経由のため、シート適用済みなら押せない。
                const inDisabled = actionsDisabled || (d.kind === 'visit' && rec.sheetApplied);
                return (
                  <SyncStripRow
                    key={d.id}
                    testId="sync-in-row"
                    dateLabel={dateIso ? fmtMd(dateIso) : '日付不明'}
                    kindLabel={d.kind === 'visit' ? '訪問' : 'イベント'}
                    tag={ACTION_TAG[act]}
                    headline={desc.headline}
                    change={desc.change}
                    note={IN_NOTE[act]}
                    selected={selected}
                    onSelect={() => setInId(selected ? null : d.id)}
                    actions={[
                      {
                        label: '取り込む',
                        primary: true,
                        disabled: inDisabled,
                        testId: 'sync-in-apply',
                        title: 'らく助をカイポケに合わせます',
                        onClick: () => void rec.applyDiff(d),
                      },
                      ...(d.kind === 'visit'
                        ? [
                            {
                              label: 'らく助を正にして上書き',
                              disabled: actionsDisabled,
                              testId: 'sync-in-over',
                              title: 'カイポケをらく助に合わせます（上書き送信）',
                              onClick: () => void rec.overwriteDiff(d),
                            },
                          ]
                        : []),
                    ]}
                  >
                    {selected ? (
                      <DiffDetailCard
                        marker={d.marker}
                        direction="inbound"
                        staffNameById={staffNameById}
                        className="ml-6 border-brand-primary-light border-l-4 border-l-brand-primary"
                      />
                    ) : null}
                  </SyncStripRow>
                );
              })}
            </ul>
          )}

          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <small className="text-[12px] text-text-muted">
              行を選ぶと盤面に「今ここ → こう変わる」のゴーストが出ます
            </small>
            <span className="flex-1" />
            <Button
              type="button"
              size="sm"
              variant={armedKey === '__in_all__' ? 'default' : 'outline'}
              className="h-8 px-3 text-[13px]"
              disabled={actionsDisabled || rec.diffs.length === 0}
              data-testid="sync-in-apply-all"
              title="差分をすべてらく助へ取り込む（カイポケが正）"
              onClick={() => arm('__in_all__', () => void rec.applyAllDiffs())}
            >
              {armedKey === '__in_all__'
                ? `${rec.diffs.length}件すべて取り込む？もう一度押す`
                : `⇩ ${rec.diffs.length}件すべて取り込む`}
            </Button>
          </div>
        </div>
      ) : null}

      {/* ── ⇧ 送る パネル ── */}
      {openPanel === 'out' ? (
        <div
          className="border-t border-border-subtle bg-bg-base px-3.5 py-3"
          data-testid="sync-panel-out"
        >
          <h3 className={panelTitleCls}>
            ⇧ カイポケへ送る
            <small className="text-[12px] font-normal text-text-muted">
              らく助で変えた予定（明日以降のみ）
            </small>
            {closeButton}
          </h3>

          {running && outRows.length === 0 ? (
            workingBlock
          ) : outRows.length === 0 ? (
            <p className="mt-2 text-[13px] text-text-muted" data-testid="sync-out-empty">
              {noSnapshot
                ? 'カイポケ側の控えがまだありません。🔄 同期確認で最新を読み込んでください。'
                : '送るものはありません（カイポケと同じ状態です）。'}
            </p>
          ) : (
            <ul className="mt-2 space-y-1.5">
              {outRows.map((r) => {
                const selected = selectedUnsent?.id === r.id;
                return (
                  <SyncStripRow
                    key={r.id}
                    testId="sync-out-row"
                    dateLabel={r.dateIso ? fmtMd(r.dateIso) : '日付不明'}
                    kindLabel={r.kind === 'visit' ? '訪問' : 'イベント'}
                    tag={
                      r.rpaUnsupported
                        ? { label: '自動送信不可', tone: 'na' }
                        : r.unassigned
                          ? { label: '担当なし', tone: 'na' }
                          : ACTION_TAG[r.action]
                    }
                    headline={r.headline}
                    change={r.change}
                    note={
                      r.rpaUnsupported
                        ? RPA_UNSUPPORTED_NOTE
                        : r.unassigned
                          ? UNASSIGNED_NOTE
                          : OUT_NOTE[r.action]
                    }
                    muted={r.rpaUnsupported || r.unassigned}
                    selected={selected}
                    onSelect={() => setUnsentId(selected ? null : r.id)}
                    actions={[
                      {
                        label: '送る',
                        primary: true,
                        disabled: actionsDisabled || r.rpaUnsupported || r.unassigned,
                        testId: 'sync-out-send',
                        onClick: () => void sendUnsent([r], r.id),
                      },
                    ]}
                  >
                    {selected && r.marker ? (
                      <DiffDetailCard
                        marker={r.marker}
                        direction="outbound"
                        staffNameById={staffNameById}
                        className="ml-6 border-brand-primary-light border-l-4 border-l-brand-primary"
                      />
                    ) : null}
                  </SyncStripRow>
                );
              })}
            </ul>
          )}

          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <small className="text-[12px] text-text-muted" data-testid="sync-out-past-note">
              {pastRows.length > 0
                ? `当日以前の予定は実績保護のため送れません（${pastRows.length}件・非表示）`
                : ''}
            </small>
            <span className="flex-1" />
            <Button
              type="button"
              size="sm"
              variant={armedKey === '__unsent_all__' ? 'default' : 'outline'}
              className="h-8 px-3 text-[13px]"
              disabled={actionsDisabled || sendableRows.length === 0}
              data-testid="sync-unsent-send-all"
              onClick={() =>
                arm('__unsent_all__', () => void sendUnsent(sendableRows, '__unsent_all__'))
              }
            >
              {armedKey === '__unsent_all__'
                ? `${sendableRows.length}件すべて送信？もう一度押す`
                : `⇧ ${sendableRows.length}件すべて送る`}
            </Button>
          </div>
        </div>
      ) : null}

      {/* ── 🔄 同期確認 パネル ── */}
      {openPanel === 'check' ? (
        <div
          className="border-t border-border-subtle bg-bg-base px-3.5 py-3"
          data-testid="sync-panel-check"
        >
          <h3 className={panelTitleCls}>
            🔄 同期確認
            {checkedAt ? (
              <small className="text-[12px] font-normal text-text-muted">{checkedAt} に確認</small>
            ) : null}
            {!running ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="ml-auto h-7 px-2 text-[12px]"
                disabled={!canEdit || rec.rpaRunning}
                data-testid="sync-check-rerun"
                title="カイポケの当週データを読み直して突き合わせます（2〜3分）"
                onClick={() => void rec.runFetch()}
              >
                🔄 再確認
              </Button>
            ) : null}
            {closeButton}
          </h3>

          {running ? (
            workingBlock
          ) : (
            <>
              {/* サマリ 3 カード: どこへ進むかを 1 目で。 */}
              <div className="mt-2 grid gap-2 sm:grid-cols-3" data-testid="sync-summary">
                {[
                  { k: 'カイポケ側で変わっている', v: inCount, s: '件 → ⇩取り込む' },
                  { k: 'らく助で変えた（未送信）', v: outCount, s: '件 → ⇧送る' },
                  { k: '要確認（サービス内容のズレ・資格）', v: needsCheckCount, s: '件' },
                ].map((c) => (
                  <div
                    key={c.k}
                    className="rounded-lg border border-border-subtle bg-bg-base px-3 py-2"
                  >
                    <p className="text-[12px] text-text-muted">{c.k}</p>
                    <p className="text-[22px] font-bold text-text-primary">
                      <span className="tnum">{c.v}</span>
                      <small className="ml-1 text-[12px] font-normal text-text-muted">{c.s}</small>
                    </p>
                  </div>
                ))}
              </div>

              {/* 要確認: サービス内容のズレ + 資格。普段は出さない (ここだけ)。 */}
              {needsCheckCount > 0 ? (
                <div
                  className="mt-3 border-t border-dashed border-border-subtle pt-2.5"
                  data-testid="sync-needs-check"
                >
                  <h4 className="mb-1.5 text-[13px] font-bold text-text-secondary">要確認</h4>
                  <ul className="space-y-1.5" data-testid="sync-service-mismatch">
                    {serviceMismatches.map((p) => (
                      <SyncStripRow
                        key={p.key}
                        testId="sync-service-mismatch-row"
                        dateLabel={p.dateIso ? fmtMd(p.dateIso) : '日付不明'}
                        kindLabel="訪問"
                        tag={{ label: 'サービス内容', tone: 'update' }}
                        headline={`${p.patientName} 様 ${p.startTime}`}
                        note={`らく助: ${p.rakusuke} ／ カイポケ: ${p.kaipoke}`}
                        actions={[
                          {
                            label: 'この訪問だけカイポケに合わせる',
                            primary: true,
                            disabled: actionsDisabled,
                            testId: 'sync-service-mismatch-apply',
                            title:
                              'この訪問だけカイポケのサービス内容に合わせます（マスタは変えません）',
                            onClick: () => void applyServiceContent(p),
                          },
                        ]}
                      />
                    ))}
                    {qualificationMismatches.map((q) => (
                      <SyncStripRow
                        key={`mm-${q.name}`}
                        testId="sync-master-qual-mismatch"
                        dateLabel="スタッフ"
                        kindLabel="資格"
                        tag={{ label: 'ズレ', tone: 'update' }}
                        headline={`${q.name} さん`}
                        note={`カイポケ「${q.kaipokeQualification}」⇔ らく助「${q.rakusukeQualification}」（どちらが正しいかご確認ください）`}
                      />
                    ))}
                    {qualificationMissing.map((q) => (
                      <SyncStripRow
                        key={`ms-${q.staffId ?? q.name}`}
                        testId="sync-master-qual-missing"
                        dateLabel="スタッフ"
                        kindLabel="資格"
                        tag={{ label: '未設定', tone: 'na' }}
                        headline={`${q.name} さん`}
                        note={`カイポケ: ${q.kaipokeQualification} ／ らく助: 未設定`}
                        actions={[
                          {
                            label: 'カイポケの職種を採用',
                            primary: true,
                            disabled:
                              !canEdit ||
                              adoptingStaffId != null ||
                              q.staffId == null ||
                              !isKnownQualification(q.kaipokeQualification),
                            testId: 'sync-master-qual-adopt',
                            onClick: () => setAdoptTarget(q),
                          },
                        ]}
                      />
                    ))}
                    {qualificationAmbiguous.map((q) => (
                      <SyncStripRow
                        key={`am-${q.name}`}
                        testId="sync-master-qual-ambiguous"
                        dateLabel="スタッフ"
                        kindLabel="資格"
                        tag={{ label: '判別不可', tone: 'na' }}
                        headline={`${q.name} さん`}
                        note={`同じ名前の在職スタッフが複数います（カイポケ「${q.kaipokeQualification}」）。スタッフマスタで表記を分けてから設定してください`}
                      />
                    ))}
                  </ul>
                </div>
              ) : null}

              {/* 実績のない日の案内 (全曜日の取込差分をまだ計算できていないとき)。 */}
              {rec.inSheetId == null && (rec.visitsPlan?.replaceDays.length ?? 0) > 0 ? (
                <div
                  className="mt-3 flex flex-wrap items-center gap-2 border-t border-dashed border-border-subtle pt-2.5"
                  data-testid="sync-replace-days"
                >
                  <p className="text-[12px] text-text-muted">
                    🗓 実績のない日（{rec.visitsPlan!.replaceDays.map((d) => fmtMd(d)).join('・')}
                    ）の差分は「⇩ 取込差分を計算」で 1 件ずつ取り込めます。日単位の丸ごと差し替えは
                    連携ページの「カイポケから取り込む」
                    {rec.visitsPlan!.replace
                      ? `（差し替え予定 ${rec.visitsPlan!.replace.inserted} 件）`
                      : ''}
                    。
                  </p>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-7 px-2.5 text-[12px]"
                    disabled={actionsDisabled}
                    data-testid="sync-in-diff-button"
                    title="実績のない日も含めた全曜日の取込差分を計算します（約1分）"
                    onClick={() => void rec.fetchInboundDiff()}
                  >
                    ⇩ 取込差分を計算（全曜日・1件ずつ）
                  </Button>
                </div>
              ) : null}

              {/* 👥 名簿の突合 (Phase M)。普段は畳んでおく。 */}
              <div
                className="mt-3 border-t border-dashed border-border-subtle pt-2.5"
                data-testid="sync-master"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <small className="text-[12px] text-text-muted">
                    {rec.masterResult
                      ? `名簿（患者・スタッフの氏名）の突合: 一致 ${
                          rec.masterResult.patients.matched + rec.masterResult.staff.matched
                        } / 表記ズレ ${
                          rec.masterResult.patients.notationDiff.length +
                          rec.masterResult.staff.notationDiff.length
                        }`
                      : '名簿（患者・スタッフの氏名）の突合はまだ行っていません'}
                  </small>
                  <span className="flex-1" />
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-7 px-2.5 text-[12px]"
                    aria-expanded={rosterOpen}
                    data-testid="sync-master-toggle"
                    onClick={() => setRosterOpen((v) => !v)}
                  >
                    👥 名簿の詳細
                  </Button>
                </div>

                {rosterOpen ? (
                  <div className="mt-2 space-y-1.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="h-7 px-2.5 text-[12px]"
                        disabled={actionsDisabled}
                        data-testid="sync-master-button"
                        title="カイポケの当月スケジュールに現れる氏名と、らく助の患者/スタッフマスタを突き合わせます（約1分）"
                        onClick={() => void rec.runMasterReconcile()}
                      >
                        名簿を突き合わせる（約1分）
                      </Button>
                    </div>
                    {rec.masterResult == null ? (
                      <p className="text-[12px] text-text-muted">
                        患者・スタッフの登録の有無と表記ズレ（スペース・異体字）を診断します。
                        取込・送信がうまくマッチしないときの原因調査に。
                      </p>
                    ) : (
                      <div className="space-y-1.5" data-testid="sync-master-result">
                        {(
                          [
                            { label: '患者', g: rec.masterResult.patients },
                            { label: 'スタッフ', g: rec.masterResult.staff },
                          ] as const
                        ).map(({ label, g }) => (
                          <div
                            key={label}
                            className="rounded border border-border-subtle px-2 py-1.5"
                          >
                            <p className="text-[12px] font-bold text-text-primary">
                              {label}: 一致 {g.matched} ・ 表記ズレ {g.notationDiff.length} ・
                              カイポケのみ {g.kaipokeOnly.length} ・ らく助のみ{' '}
                              {g.rakusukeOnly.length}
                            </p>
                            {g.notationDiff.length > 0 ? (
                              <p className="mt-0.5 text-[12px] text-amber-800">
                                🟡 表記ズレ（同期は自動吸収済み・マスタ修正推奨）:{' '}
                                {g.notationDiff
                                  .map((d) => `カイポケ「${d.kaipoke}」⇔らく助「${d.rakusuke}」`)
                                  .join(' / ')}
                              </p>
                            ) : null}
                            {g.kaipokeOnly.length > 0 ? (
                              <p className="mt-0.5 text-[12px] text-violet-800">
                                🟣 カイポケのみ（らく助未登録）:{' '}
                                {g.kaipokeOnly
                                  .map((n) =>
                                    // スタッフ側だけ、カイポケの職種を氏名に添える (資格未登録の
                                    // 人を新規登録するとき、何の資格で作ればよいかが分かる)。
                                    label === 'スタッフ' && kaipokeQualificationByName.get(n)
                                      ? `${n}（${kaipokeQualificationByName.get(n)}）`
                                      : n,
                                  )
                                  .join('、')}
                              </p>
                            ) : null}
                            {g.rakusukeOnly.length > 0 ? (
                              <p className="mt-0.5 text-[12px] text-sky-800">
                                🔵 らく助のみ（当月のカイポケスケジュールに未出現）:{' '}
                                {g.rakusukeOnly.join('、')}
                              </p>
                            ) : null}
                          </div>
                        ))}
                        <div
                          className="rounded border border-border-subtle px-2 py-1.5"
                          data-testid="sync-master-qualifications"
                        >
                          <p className="text-[12px] font-bold text-text-primary">
                            資格: ズレ {qualificationMismatches.length} ・ 未設定{' '}
                            {qualificationMissing.length}
                            {qualificationAmbiguous.length > 0
                              ? ` ・ 同名で判別不可 ${qualificationAmbiguous.length}`
                              : ''}
                          </p>
                          <p className="mt-0.5 text-[12px] text-text-muted">
                            {needsCheckCount > 0
                              ? '内容は上の「要確認」に出しています。'
                              : 'カイポケの職種と一致しています（正看/准看の判定は正しく出ます）。'}
                          </p>
                        </div>
                        <p className="text-[11px] text-text-muted">
                          ※ カイポケ側は「当月のスケジュールに現れた氏名」で判定します
                          （予定の無い方は判定できません）。
                        </p>
                      </div>
                    )}
                  </div>
                ) : null}
              </div>
            </>
          )}
        </div>
      ) : null}

      {/* 資格の採用はマスタ更新 = その職員の全訪問に効く。影響範囲を見せて確認する。 */}
      <Dialog
        open={adoptTarget != null}
        onOpenChange={(o) => {
          if (!o && adoptingStaffId == null) setAdoptTarget(null);
        }}
      >
        <DialogContent className="max-w-sm" data-testid="sync-master-qual-confirm-dialog">
          <DialogHeader>
            <DialogTitle className="text-sm">資格を設定しますか？</DialogTitle>
            <DialogDescription className="text-[11px]">
              {adoptTarget
                ? `${adoptTarget.name}さんの資格を「${adoptTarget.kaipokeQualification}」にします。この職員の全訪問のサービス内容が変わります。`
                : ''}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={adoptingStaffId != null}
              onClick={() => setAdoptTarget(null)}
            >
              やめる
            </Button>
            <Button
              type="button"
              disabled={adoptingStaffId != null}
              data-testid="sync-master-qual-confirm"
              onClick={() => {
                const q = adoptTarget;
                setAdoptTarget(null);
                if (q) void adoptQualification(q);
              }}
            >
              設定する
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
