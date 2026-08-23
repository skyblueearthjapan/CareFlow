'use client';

/**
 * SyncBar — 同期バー (週空間 Phase E・運転席)。
 *
 * 盤面の上に常駐し、カイポケとのズレを 3 態で見せる:
 *   ✓同期済 (表示なし) / ●未送信 (らく助が進んでいる) / ⚠カイポケ側が違う (要突合)
 *
 * 左 ●未送信 … `useUnsentSummary` (RPA なし・保存済み CSV との差分)
 *               ⇧1件送信 = 既存 apply の部分適用 (itemIds) / ⇧全件 = 2段クリック
 * 右 🔄突合   … `useKaipokeReconcile` (既存パネルのロジックを hook 化)
 *               ⇩1件 / ⇩全件 / ⇧上書き / ⇧上書き全件 / 👥マスタ突合
 *
 * 当日以前 (JST) は実績が付いている可能性があるため送信対象外 (BE も 422)。
 * 選択中の差分は `onSelectDiff` で親へ渡し、盤面がゴーストを描く。
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
} from './reconcileMarkers';
import { useKaipokeReconcile, type CockpitDiff } from './useKaipokeReconcile';
import { cn } from '@/lib/utils';

/** 全件ボタンの 2 段クリック猶予 (モックと同じ 3 秒)。 */
const ARM_MS = 3_000;

/** JST の「今日」(BE と同じ Asia/Tokyo 基準)。 */
export function todayIsoJst(): string {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Tokyo' }).format(new Date());
}

const KIND_DOT: Record<string, string> = { add: '🟣 新規', delete: '🔵 取消', update: '🟡 変更' };

/**
 * RPA 未対応の行に付ける注記 (S3 完了まで・kaipoke-service-content-design.md §3)。
 * 准看/一般のサービス内容は RPA が固定値で登録してしまうため送らない。
 * 判定そのものは BE (`rpa_unsupported`) が持ち、FE は表示だけ。
 */
const RPA_UNSUPPORTED_NOTE = '（RPAが准看/一般の登録に未対応=カイポケで直接登録）';

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
  /** RPA が空いていれば自動で 1 回だけ突合を始める。 */
  autoStartReconcile?: boolean;
  className?: string;
}

interface UnsentRow {
  id: string;
  kind: 'visit' | 'event';
  label: string;
  dateIso: string | null;
  marker: CockpitMarker | null;
  /** 盤面の予定と突き合わせるキー (● ドット用)。解決できないときは null。 */
  matchKey: string | null;
  /**
   * RPA がカイポケへ正しく登録できない行 (S3 完了まで・BE 判定)。
   * true の間は選べない = 送信対象から外す。
   */
  rpaUnsupported: boolean;
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

  // ─── 左: ●未送信 ───
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
  const [activeSide, setActiveSide] = React.useState<'unsent' | 'in'>('unsent');
  const [busy, setBusy] = React.useState<string | null>(null);
  const [armedKey, setArmedKey] = React.useState<string | null>(null);
  // 資格の「カイポケの職種を採用」(👥マスタ突合)。突合は ~1 分かかるので
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
   * 印を落としてよいのは **🔄突合が終わったとき** だけ = カイポケの現況を
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

  // ─── 右: 🔄突合 (突合が終わったら未送信も数え直す) ───
  const rec = useKaipokeReconcile({
    weekStartIso,
    canEdit,
    staffIdByName,
    autoStart: autoStartReconcile,
    // 突合＝カイポケの現況を見直した後なので、ここでだけ送信済みの印を落とす。
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
      rows.push({
        id: it.id,
        kind: 'visit',
        dateIso,
        // 日付/時刻/患者名で盤面の訪問と突き合わせる。**側は action で決まる**
        // (delete は before / add は after) — after 固定だと delete 行の
        // 日付と時刻が空文字になり突合できない。
        matchKey: dateIso && startTime ? unsentVisitKey(dateIso, startTime, who) : null,
        marker: correctionItemToMarker(it, { weekStartIso, staffIdByName }),
        rpaUnsupported: it.rpa_unsupported === true,
        label:
          `${KIND_DOT[it.action] ?? '🟡 変更'}｜${dateIso ? fmtMd(dateIso) : '日付不明'} 訪問｜` +
          `${who}｜${staff} ${startTime}` +
          `${dateIso != null && dateIso <= todayIso ? '（当日以前=送信対象外）' : ''}` +
          `${it.rpa_unsupported === true ? RPA_UNSUPPORTED_NOTE : ''}`,
      });
    }
    for (const ev of summary.events) {
      if (sentKeys.has(unsentEventKey(ev.id))) continue;
      rows.push({
        id: ev.id,
        kind: 'event',
        dateIso: ev.date,
        matchKey: unsentEventKey(ev.id),
        marker: unsentEventToMarker(ev),
        // イベントはサービス内容を持たない = RPA 未対応ガードの対象外。
        rpaUnsupported: false,
        label:
          `${KIND_DOT[ev.kind] ?? '🟡 変更'}｜${fmtMd(ev.date)} イベント｜${ev.title}｜` +
          `${ev.staff_name} ${ev.start_time}` +
          `${ev.date <= todayIso ? '（当日以前=送信対象外）' : ''}`,
      });
    }
    return rows;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary, sentKeys, weekStartIso, staffIdByName, todayIso]);

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

  // ─── 資格のズレ / 未設定 (👥マスタ突合の結果から) ───
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
   *  RPA 未対応 (准看/一般) の行は BE が apply でも弾くのでここでも外す
   *  = 「送れると見えたのに BE がスキップした」ズレを作らない。 */
  const isSendable = (r: UnsentRow) =>
    !r.rpaUnsupported && (r.dateIso == null || r.dateIso > todayIso);
  const sendableRows = unsentRows.filter(isSendable);
  // 件数の表示は BE の集計 (past_count / rpa_unsupported_count) を正とし、
  // 送信済みの分だけ減らす。BE 側は両者を二重に数えないので単純な引き算でよい。
  const pastCount = summary?.past_count ?? 0;
  const rpaUnsupportedCount = summary?.rpa_unsupported_count ?? 0;
  const sendableCount = Math.max(0, unsentRows.length - pastCount - rpaUnsupportedCount);
  const selectedUnsent =
    unsentRows.find((r) => r.id === unsentId) ?? sendableRows[0] ?? unsentRows[0] ?? null;
  const selectedDiff: CockpitDiff | null =
    rec.diffs.find((d) => d.id === inId) ?? rec.diffs[0] ?? null;

  // ─── 選択中マーカーを親へ (盤面ゴースト・カードと必ず一致させる) ───
  const activeMarker =
    activeSide === 'in' ? (selectedDiff?.marker ?? null) : (selectedUnsent?.marker ?? null);
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
            '送信用の差分シートがありません（🔄突合でカイポケ現況を取得してください）',
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
  // busy = らく助が何かしている間。突合系も送信系もこの間は押させない。
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

  const selectCls =
    'min-w-0 max-w-[22rem] flex-1 rounded border border-border-default bg-bg-base px-1.5 py-1 text-[11px] disabled:opacity-50';

  return (
    <section
      className={cn('rounded-lg border border-border-default bg-bg-muted', className)}
      data-testid="sync-bar"
      aria-label="カイポケとの同期"
    >
      {running ? (
        <div className="px-3 py-2" data-testid="sync-working">
          <RakusukeWorking
            message={workingLabel}
            sub="約2〜3分。その間も盤面は見られます"
            pose="calendar"
          />
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 px-3 py-2">
        <span className="text-[12px] font-bold text-text-primary">同期</span>
        <span className="text-[11px] text-text-muted" data-testid="sync-fetched-at">
          {rec.fetchedAt
            ? `最終突合 ${rec.fetchedAt.getHours()}:${String(rec.fetchedAt.getMinutes()).padStart(2, '0')}${rec.stale ? '（古い可能性）' : ''}`
            : 'まだ突合していません'}
        </span>

        {/* ── 左: ●未送信 ── */}
        <div className="flex flex-wrap items-center gap-1" data-testid="sync-unsent">
          <span className="text-[11px] font-bold text-info-strong">● 未送信（らく助で変えた）</span>
          <span className="tnum text-[11px] text-text-muted" data-testid="sync-unsent-count">
            {unsentRows.length > 0
              ? `${unsentRows.length}件（送れる ${sendableCount}${pastCount > 0 ? `・当日以前 ${pastCount}` : ''}${rpaUnsupportedCount > 0 ? `・RPA未対応 ${rpaUnsupportedCount}` : ''}）`
              : ''}
          </span>
          <select
            className={selectCls}
            disabled={unsentRows.length === 0}
            value={selectedUnsent?.id ?? ''}
            aria-label="未送信"
            data-testid="sync-unsent-select"
            onChange={(e) => {
              setUnsentId(e.target.value || null);
              setActiveSide('unsent');
            }}
          >
            {unsentRows.length === 0 ? (
              <option value="">なし（カイポケと同じ）</option>
            ) : (
              unsentRows.map((r) => (
                <option key={r.id} value={r.id} disabled={!isSendable(r)}>
                  {r.label}
                </option>
              ))
            )}
          </select>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[11px]"
            disabled={!canEdit || running || rec.rpaRunning || sendableRows.length === 0}
            data-testid="sync-unsent-send"
            onClick={() => {
              const row =
                selectedUnsent && isSendable(selectedUnsent) ? selectedUnsent : sendableRows[0];
              if (row) void sendUnsent([row], row.id);
            }}
          >
            ⇧ 1件送信
          </Button>
          <Button
            type="button"
            size="sm"
            variant={armedKey === '__unsent_all__' ? 'default' : 'outline'}
            className="h-6 px-2 text-[11px]"
            disabled={!canEdit || running || rec.rpaRunning || sendableRows.length === 0}
            data-testid="sync-unsent-send-all"
            onClick={() =>
              arm('__unsent_all__', () => void sendUnsent(sendableRows, '__unsent_all__'))
            }
          >
            {armedKey === '__unsent_all__'
              ? `${sendableRows.length}件すべて送信？もう一度押す`
              : '⇧ 全件'}
          </Button>
        </div>

        {/* ── 右: 🔄突合 ── */}
        <div className="flex flex-wrap items-center gap-1" data-testid="sync-inbound">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[11px]"
            disabled={!canEdit || running || rec.rpaRunning}
            data-testid="sync-reconcile-run"
            title={
              rec.rpaRunning
                ? 'カイポケ連携が実行中です（単一スロット）。完了後に実行してください'
                : 'カイポケの当週データを取得して突き合わせます（2〜3分）'
            }
            onClick={() => void rec.runFetch()}
          >
            🔄 {rec.phase === 'idle' || rec.phase === 'error' ? '突合' : '再突合'}
          </Button>
          <span className="text-[11px] font-bold text-text-secondary">⇩ カイポケ側が違う</span>
          <span className="tnum text-[11px] text-text-muted" data-testid="sync-in-count">
            {rec.diffs.length > 0 ? `${rec.diffs.length}件` : ''}
          </span>
          <select
            className={selectCls}
            disabled={rec.diffs.length === 0}
            value={selectedDiff?.id ?? ''}
            aria-label="取込候補"
            data-testid="sync-in-select"
            onChange={(e) => {
              setInId(e.target.value || null);
              setActiveSide('in');
            }}
          >
            {rec.diffs.length === 0 ? (
              <option value="">差分なし（🔄突合で再確認）</option>
            ) : (
              rec.diffs.map((d) => (
                <option key={d.id} value={d.id}>
                  {`${KIND_DOT[d.marker.action] ?? ''}｜${d.kind === 'visit' ? '訪問' : 'イベント'}｜${
                    d.marker.patient_name ?? d.marker.title
                  }`}
                </option>
              ))
            )}
          </select>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[11px]"
            disabled={!canEdit || running || rec.rpaRunning || !selectedDiff}
            data-testid="sync-in-apply"
            onClick={() => {
              if (selectedDiff) void rec.applyDiff(selectedDiff);
            }}
          >
            ⇩ 取り込む
          </Button>
          <Button
            type="button"
            size="sm"
            variant={armedKey === '__in_all__' ? 'default' : 'outline'}
            className="h-6 px-2 text-[11px]"
            disabled={!canEdit || running || rec.rpaRunning || rec.diffs.length === 0}
            data-testid="sync-in-apply-all"
            title="差分をすべてらく助へ取り込む（カイポケが正）"
            onClick={() => arm('__in_all__', () => void rec.applyAllDiffs())}
          >
            {armedKey === '__in_all__'
              ? `${rec.diffs.length}件すべて取り込む？もう一度押す`
              : '⇩ 全件'}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[11px]"
            disabled={!canEdit || running || rec.rpaRunning || selectedDiff?.kind !== 'visit'}
            data-testid="sync-in-over"
            title="らく助を正としてカイポケを上書き送信（訪問のみ）"
            onClick={() => {
              if (selectedDiff) void rec.overwriteDiff(selectedDiff);
            }}
          >
            ⇧ 上書き
          </Button>
          <Button
            type="button"
            size="sm"
            variant={armedKey === '__over_all__' ? 'default' : 'outline'}
            className="h-6 px-2 text-[11px]"
            disabled={!canEdit || running || rec.rpaRunning || rec.visitItems.length === 0}
            data-testid="sync-in-over-all"
            title="差分をすべてカイポケへ上書き送信（らく助が正・訪問のみ）"
            onClick={() => arm('__over_all__', () => void rec.overwriteAllDiffs())}
          >
            {armedKey === '__over_all__'
              ? `${rec.visitItems.length}件すべて上書き？もう一度押す`
              : '⇧ 全件'}
          </Button>
        </div>

        <span className="ml-auto text-[11px] text-text-muted" data-testid="sync-meta">
          {unsentRows.length > 0
            ? `●未送信 ${unsentRows.length}件 — カイポケと差があります`
            : summary == null
              ? '型どおりの予定は ✓同期済（表示なし）'
              : '✓ カイポケと同じ状態です'}
        </span>
      </div>

      {summary != null && summary.snapshot == null ? (
        <p className="px-3 pb-2 text-[11px] text-warning-strong" data-testid="sync-no-snapshot">
          カイポケ側の控えがまだありません。🔄突合でカイポケ現況を取得してください
        </p>
      ) : null}

      {/* 実績のない日の案内 (全曜日の取込差分をまだ計算できていないとき)。 */}
      {rec.inSheetId == null && (rec.visitsPlan?.replaceDays.length ?? 0) > 0 ? (
        <div
          className="flex flex-wrap items-center gap-2 px-3 pb-2"
          data-testid="sync-replace-days"
        >
          <p className="text-[11px] text-text-muted">
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
            className="h-6 px-2 text-[11px]"
            disabled={!canEdit || running || rec.rpaRunning}
            data-testid="sync-in-diff-button"
            title="実績のない日も含めた全曜日の取込差分を計算します（約1分）"
            onClick={() => void rec.fetchInboundDiff()}
          >
            ⇩ 取込差分を計算（全曜日・1件ずつ）
          </Button>
        </div>
      ) : null}

      {rec.error ? (
        <p className="px-3 pb-2 text-[11px] text-error" data-testid="sync-error">
          {rec.error}
        </p>
      ) : null}

      {activeSide === 'in' && selectedDiff ? (
        <div className="px-3 pb-2">
          <DiffDetailCard
            marker={selectedDiff.marker}
            direction="inbound"
            staffNameById={staffNameById}
          />
        </div>
      ) : activeSide === 'unsent' && selectedUnsent?.marker ? (
        <div className="px-3 pb-2">
          <DiffDetailCard
            marker={selectedUnsent.marker}
            direction="outbound"
            staffNameById={staffNameById}
          />
        </div>
      ) : null}

      {/* ── 🧾 サービス内容のズレ (delete + add のペアを束ねた 1 行) ── */}
      {serviceMismatches.length > 0 ? (
        <div
          className="border-t border-border-subtle px-3 py-2"
          data-testid="sync-service-mismatch"
        >
          <h4 className="mb-1 text-[12px] font-bold text-text-primary">
            🧾 サービス内容のズレ（{serviceMismatches.length}件）
          </h4>
          <p className="mb-1 text-[11px] text-text-muted">
            日時も担当も同じで、サービス内容だけが違う訪問です。
            カイポケ側は編集で直せないため、上の一覧では「取消＋新規」の 2 行に見えます。
          </p>
          <ul className="space-y-1">
            {serviceMismatches.map((p) => (
              <li
                key={p.key}
                className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded border border-border-subtle px-2 py-1.5"
                data-testid="sync-service-mismatch-row"
              >
                <span className="text-[11px] text-text-primary">
                  {fmtMd(p.dateIso)} {p.startTime}｜{p.patientName}
                </span>
                <span className="text-[11px] text-text-secondary">
                  らく助: <b>{p.rakusuke}</b> / カイポケ: <b>{p.kaipoke}</b>
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-[11px]"
                  disabled={!canEdit || running || rec.rpaRunning}
                  data-testid="sync-service-mismatch-apply"
                  title="この訪問だけカイポケのサービス内容に合わせます（マスタは変えません）"
                  onClick={() => void applyServiceContent(p)}
                >
                  この訪問だけカイポケに合わせる
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* ── 👥 マスタ相互突合 (Phase M): 名簿と表記ズレの診断 ── */}
      <div className="border-t border-border-subtle px-3 py-2" data-testid="sync-master">
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <h4 className="text-[12px] font-bold text-text-primary">👥 マスタ突合（名簿チェック）</h4>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[11px]"
            disabled={!canEdit || running || rec.rpaRunning}
            data-testid="sync-master-button"
            title="カイポケの当月スケジュールに現れる氏名と、らく助の患者/スタッフマスタを突き合わせます（約1分）"
            onClick={() => void rec.runMasterReconcile()}
          >
            実行（約1分）
          </Button>
        </div>
        {rec.masterResult == null ? (
          <p className="text-[11px] text-text-muted">
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
              <div key={label} className="rounded border border-border-subtle px-2 py-1.5">
                <p className="text-[11px] font-bold text-text-primary">
                  {label}: 一致 {g.matched} ・ 表記ズレ {g.notationDiff.length} ・ カイポケのみ{' '}
                  {g.kaipokeOnly.length} ・ らく助のみ {g.rakusukeOnly.length}
                </p>
                {g.notationDiff.length > 0 ? (
                  <p className="mt-0.5 text-[11px] text-amber-800">
                    🟡 表記ズレ（同期は自動吸収済み・マスタ修正推奨）:{' '}
                    {g.notationDiff
                      .map((d) => `カイポケ「${d.kaipoke}」⇔らく助「${d.rakusuke}」`)
                      .join(' / ')}
                  </p>
                ) : null}
                {g.kaipokeOnly.length > 0 ? (
                  <p className="mt-0.5 text-[11px] text-violet-800">
                    🟣 カイポケのみ（らく助未登録）:{' '}
                    {g.kaipokeOnly
                      .map((n) =>
                        // スタッフ側だけ、カイポケの職種を氏名に添える (資格未登録の
                        // 人を新規登録するとき、何の資格で作ればよいかが分かる)。
                        // 資格セクションに別行で出すと「登録すらされていない人の
                        // 資格ズレ」という読みにくい並びになるのでここへ寄せた。
                        label === 'スタッフ' && kaipokeQualificationByName.get(n)
                          ? `${n}（${kaipokeQualificationByName.get(n)}）`
                          : n,
                      )
                      .join('、')}
                  </p>
                ) : null}
                {g.rakusukeOnly.length > 0 ? (
                  <p className="mt-0.5 text-[11px] text-sky-800">
                    🔵 らく助のみ（当月のカイポケスケジュールに未出現）: {g.rakusukeOnly.join('、')}
                  </p>
                ) : null}
              </div>
            ))}
            {/* 資格 (カイポケ職種1 × らく助 staff.qualification)。
                サービス内容の 正看/准看 はここで決まる = ズレると偽差分の元。 */}
            <div
              className="rounded border border-border-subtle px-2 py-1.5"
              data-testid="sync-master-qualifications"
            >
              <p className="text-[11px] font-bold text-text-primary">
                資格: ズレ {qualificationMismatches.length} ・ 未設定 {qualificationMissing.length}
                {qualificationAmbiguous.length > 0
                  ? ` ・ 同名で判別不可 ${qualificationAmbiguous.length}`
                  : ''}
              </p>
              {qualificationMismatches.length === 0 &&
              qualificationMissing.length === 0 &&
              qualificationAmbiguous.length === 0 ? (
                <p className="mt-0.5 text-[11px] text-text-muted">
                  カイポケの職種と一致しています（正看/准看の判定は正しく出ます）。
                </p>
              ) : null}
              {qualificationMismatches.map((q) => (
                <p
                  key={`mm-${q.name}`}
                  className="mt-0.5 text-[11px] text-amber-800"
                  data-testid="sync-master-qual-mismatch"
                >
                  🟡 {q.name}: カイポケ「{q.kaipokeQualification}」⇔ らく助「
                  {q.rakusukeQualification}」（どちらが正しいかご確認ください）
                </p>
              ))}
              {qualificationMissing.map((q) => (
                <p
                  key={`ms-${q.staffId ?? q.name}`}
                  className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-violet-800"
                  data-testid="sync-master-qual-missing"
                >
                  <span>
                    🟣 {q.name}: らく助が資格未設定（カイポケ「{q.kaipokeQualification}」）
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-5 px-1.5 text-[10px]"
                    disabled={
                      !canEdit ||
                      adoptingStaffId != null ||
                      q.staffId == null ||
                      !isKnownQualification(q.kaipokeQualification)
                    }
                    data-testid="sync-master-qual-adopt"
                    onClick={() => setAdoptTarget(q)}
                  >
                    カイポケの職種を採用
                  </Button>
                </p>
              ))}
              {qualificationAmbiguous.map((q) => (
                <p
                  key={`am-${q.name}`}
                  className="mt-0.5 text-[11px] text-text-secondary"
                  data-testid="sync-master-qual-ambiguous"
                >
                  ⚪ {q.name}: 同じ名前の在職スタッフが複数います（カイポケ「
                  {q.kaipokeQualification}」）。
                  どちらの方か特定できないため、スタッフマスタで表記を分けてから設定してください
                </p>
              ))}
            </div>
            <p className="text-[10px] text-text-muted">
              ※ カイポケ側は「当月のスケジュールに現れた氏名」で判定します
              （予定の無い方は判定できません）。
            </p>
          </div>
        )}
      </div>

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
