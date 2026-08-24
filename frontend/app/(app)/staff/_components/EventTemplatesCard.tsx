'use client';

/**
 * EventTemplatesCard — 「イベントひな形」管理カード (Phase 2)。
 *
 * 正典 = docs/plans/staff-event-history-design.md §2 Phase 2 /
 *        docs/mockups/event-templates-mock.html。
 *
 * 共通 (事業所全体) と 個人 (そのスタッフ) の **両スコープを 1 コンポーネント**で
 * 扱う。`scope` に 'shared' を渡すとスタッフ一覧上部の共通カード、staff id を
 * 渡すとスタッフ詳細の個人カードになる。
 *
 * ひな形は「入力の型」でしかない — 無効化・削除しても登録済みイベントには
 * 影響しない (プルダウンに出るかどうかだけが変わる)。
 *
 * モックとの差分: 並べ替えはモックの ⠿ ドラッグではなく **↑↓ ボタン**。
 * reorder API が ordered_ids の一括更新なので、↑↓ で組んだ並びをそのまま送る。
 */
import { useEffect, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, Eye, EyeOff, History, Pencil, Plus } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import {
  useCreateEventTemplate,
  useDeleteEventTemplate,
  useEventTemplateHistorySuggestions,
  useEventTemplates,
  useReorderEventTemplates,
  useUpdateEventTemplate,
  type EventTemplateRead,
} from '@/lib/queries/event-templates';

/** `scope` の共通スコープを表すセンチネル (staff id は UUID なので衝突しない)。 */
export const SHARED_TEMPLATE_SCOPE = 'shared';

/** 'shared' = 事業所共通 / それ以外 = そのスタッフ (staff id) の個人ひな形。 */
export type EventTemplateScope = string;

function scopeStaffId(scope: EventTemplateScope): string | null {
  return scope === SHARED_TEMPLATE_SCOPE ? null : scope;
}

const TYPE_LABEL: Record<'event' | 'training', string> = {
  event: 'イベント',
  training: '研修',
};

function timeLabel(t: Pick<EventTemplateRead, 'start_time' | 'end_time'>): string {
  if (!t.start_time || !t.end_time) return '時間はその場で入力';
  return `${t.start_time}〜${t.end_time}`;
}

/** 'YYYY-MM-DD' → 'M/D' (履歴パネルの「直近」表示)。 */
function shortDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return `${Number(m[2])}/${Number(m[3])}`;
}

// ───────────────────────────────────────────────────────────────────────────
// 追加 / 編集ダイアログ
// ───────────────────────────────────────────────────────────────────────────

export interface EventTemplateFormInitial {
  title?: string;
  event_type?: 'event' | 'training';
  start_time?: string | null;
  end_time?: string | null;
  blocking?: boolean;
  note?: string | null;
}

export interface EventTemplateFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 編集対象。null / 未指定なら新規作成。 */
  template?: EventTemplateRead | null;
  /** 新規作成時の保存先スコープ。 */
  scope: EventTemplateScope;
  /**
   * 保存先 (共通 / このスタッフの個人) を選ばせる場合に渡す。
   * EventsCard の「☆ ひな形に保存」から使う。編集時は無視。
   */
  scopeChoice?: { staffId: string; staffLabel?: string } | null;
  /** 新規作成時の初期値 (イベントからの引き継ぎなど)。 */
  initial?: EventTemplateFormInitial;
  onSaved?: () => void;
}

/**
 * ひな形の追加 / 編集ダイアログ。編集時はフッタに削除ボタン (confirm 付き) が出る。
 * 時刻は「両方入力 or 両方空」 — 片方だけだと BE が 422 になるので送信前に弾く。
 */
export function EventTemplateFormDialog({
  open,
  onOpenChange,
  template = null,
  scope,
  scopeChoice = null,
  initial,
  onSaved,
}: EventTemplateFormDialogProps) {
  const isEdit = template !== null;

  const [title, setTitle] = useState('');
  const [eventType, setEventType] = useState<'event' | 'training'>('event');
  const [hasTime, setHasTime] = useState(false);
  const [start, setStart] = useState('09:00');
  const [end, setEnd] = useState('10:00');
  const [blocking, setBlocking] = useState(false);
  const [note, setNote] = useState('');
  const [saveScope, setSaveScope] = useState<EventTemplateScope>(scope);

  // 開くたびに初期値へ戻す (前回の入力が残らないように)。
  useEffect(() => {
    if (!open) return;
    const src: EventTemplateFormInitial = template ?? initial ?? {};
    setTitle(src.title ?? '');
    setEventType(src.event_type ?? 'event');
    const both = !!src.start_time && !!src.end_time;
    setHasTime(both);
    setStart(both ? src.start_time! : '09:00');
    setEnd(both ? src.end_time! : '10:00');
    setBlocking(src.blocking ?? false);
    setNote(src.note ?? '');
    setSaveScope(scope);
  }, [open, template, initial, scope]);

  const create = useCreateEventTemplate();
  const update = useUpdateEventTemplate();
  const remove = useDeleteEventTemplate();

  const trimmed = title.trim();
  const timeInvalid = hasTime && (!start || !end || start >= end);
  const pending = create.isPending || update.isPending || remove.isPending;

  async function handleSubmit() {
    const payload = {
      title: trimmed,
      event_type: eventType,
      start_time: hasTime ? start : null,
      end_time: hasTime ? end : null,
      blocking,
      note: note.trim() || null,
    };
    try {
      if (isEdit) {
        await update.mutateAsync({ id: template.id, payload });
        toast.success('ひな形を更新しました');
      } else {
        await create.mutateAsync({ ...payload, staff_id: scopeStaffId(saveScope) });
        toast.success('ひな形に保存しました');
      }
      onSaved?.();
      onOpenChange(false);
    } catch (e) {
      toast.error('保存に失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  async function handleDelete() {
    if (!template) return;
    if (
      !window.confirm(
        `ひな形「${template.title}」を削除しますか？\n登録済みのイベントには影響しません（プルダウンから消えるだけです）。`,
      )
    )
      return;
    try {
      await remove.mutateAsync(template.id);
      toast.success('ひな形を削除しました');
      onSaved?.();
      onOpenChange(false);
    } catch (e) {
      toast.error('削除に失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onOpenChange(false)}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{isEdit ? 'ひな形を編集' : 'ひな形を追加'}</DialogTitle>
          <DialogDescription>
            ここで登録した内容がイベント追加のプルダウンに並びます。
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          {!isEdit && scopeChoice && (
            <div className="space-y-1">
              <Label htmlFor="tpl-scope">保存先</Label>
              <select
                id="tpl-scope"
                value={saveScope}
                onChange={(e) => setSaveScope(e.target.value)}
                data-testid="event-template-scope"
                className="h-10 w-full rounded-md border border-border-default bg-bg-base px-3 text-sm"
              >
                <option value={SHARED_TEMPLATE_SCOPE}>共通（全スタッフのプルダウンに出る）</option>
                <option value={scopeChoice.staffId}>
                  {scopeChoice.staffLabel ?? 'このスタッフ'}の個人ひな形
                </option>
              </select>
            </div>
          )}
          <div className="space-y-1">
            <Label htmlFor="tpl-title">タイトル</Label>
            <Input
              id="tpl-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={255}
              placeholder="例: カンファレンス"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="tpl-type">種別</Label>
            <select
              id="tpl-type"
              value={eventType}
              onChange={(e) => setEventType(e.target.value as 'event' | 'training')}
              className="h-10 w-full rounded-md border border-border-default bg-bg-base px-3 text-sm"
            >
              <option value="event">イベント</option>
              <option value="training">研修</option>
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={hasTime}
              onCheckedChange={(v) => setHasTime(v === true)}
              aria-label="時刻を決めておく"
            />
            時刻を決めておく（外すと「時間はその場で入力」）
          </label>
          {hasTime && (
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label htmlFor="tpl-start">開始</Label>
                <Input
                  id="tpl-start"
                  type="time"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="tpl-end">終了</Label>
                <Input
                  id="tpl-end"
                  type="time"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                />
              </div>
            </div>
          )}
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={blocking}
              onCheckedChange={(v) => setBlocking(v === true)}
              aria-label="絶対に潰せないイベントにする"
            />
            🔒 絶対に潰せないイベントにする（提案エンジンの対象外）
          </label>
          <div className="space-y-1">
            <Label htmlFor="tpl-note">備考</Label>
            <Textarea
              id="tpl-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={500}
              rows={2}
            />
          </div>
        </div>
        <DialogFooter className="sm:justify-between">
          {isEdit ? (
            <Button
              type="button"
              variant="destructive"
              onClick={() => void handleDelete()}
              disabled={pending}
              data-testid="event-template-delete"
            >
              削除
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              キャンセル
            </Button>
            <Button
              type="button"
              onClick={() => void handleSubmit()}
              disabled={!trimmed || timeInvalid || pending}
              data-testid="event-template-save"
            >
              {pending ? '保存中…' : '保存する'}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// 履歴から追加パネル
// ───────────────────────────────────────────────────────────────────────────

function HistoryPanel({ scope, canEdit }: { scope: EventTemplateScope; canEdit: boolean }) {
  const staffId = scopeStaffId(scope);
  const { data, isLoading, isError, error } = useEventTemplateHistorySuggestions(staffId);
  const create = useCreateEventTemplate();
  const [added, setAdded] = useState<string[]>([]);

  async function handleAdd(s: NonNullable<typeof data>[number]) {
    const both = !!s.last_start_time && !!s.last_end_time;
    try {
      await create.mutateAsync({
        staff_id: staffId,
        title: s.title,
        event_type: s.event_type,
        start_time: both ? s.last_start_time : null,
        end_time: both ? s.last_end_time : null,
      });
      setAdded((prev) => [...prev, `${s.event_type}/${s.title}`]);
    } catch (e) {
      toast.error('ひな形の追加に失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  return (
    <div
      className="mt-3 rounded-lg border border-dashed border-brand-primary bg-brand-primary-50 p-3"
      data-testid="event-template-history-panel"
    >
      <h4 className="text-sm font-semibold text-text-primary">過去のイベント履歴から追加</h4>
      <p className="mb-2 text-xs text-text-muted">
        直近6ヶ月の履歴をタイトルで集約（朝会・休みなどの定例は除外）。＋を押すとひな形に登録されます。
      </p>
      {isLoading && <Skeleton className="h-12 w-full" />}
      {isError && (
        <Alert variant="destructive">
          <AlertTitle>履歴の取得に失敗しました</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      )}
      {data && data.length === 0 && (
        <p className="text-sm text-text-muted">ひな形にできる履歴がありません</p>
      )}
      {data && data.length > 0 && (
        <ul>
          {data.map((s) => {
            const done = added.includes(`${s.event_type}/${s.title}`);
            const times =
              s.last_start_time && s.last_end_time
                ? ` ${s.last_start_time}〜${s.last_end_time}`
                : '';
            return (
              <li
                key={s.title}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-bg-base"
                data-testid="event-template-history-row"
              >
                <span
                  className={cn('font-semibold', done ? 'text-success' : 'text-text-primary')}
                >
                  {s.title}
                </span>
                <span className="text-xs text-text-muted">
                  × {s.count}回　直近 {shortDate(s.last_date)}
                  {times}
                </span>
                <span className="flex-1" />
                {done ? (
                  <span className="text-xs font-bold text-success">✓ 追加しました</span>
                ) : (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!canEdit || create.isPending}
                    onClick={() => void handleAdd(s)}
                    data-testid={`event-template-history-add-${s.title}`}
                  >
                    ＋ ひな形にする
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// カード本体
// ───────────────────────────────────────────────────────────────────────────

export interface EventTemplatesCardProps {
  /** 'shared' = 事業所共通 / staff id = そのスタッフの個人ひな形。 */
  scope: EventTemplateScope;
  canEdit: boolean;
  /** 見出し。省略時はスコープに応じた既定文言。 */
  heading?: string;
}

export function EventTemplatesCard({ scope, canEdit, heading }: EventTemplatesCardProps) {
  const staffId = scopeStaffId(scope);
  const isShared = staffId === null;

  const { data, isLoading, isError, error } = useEventTemplates({
    staffId,
    includeInactive: true,
  });
  const update = useUpdateEventTemplate();
  const reorder = useReorderEventTemplates();

  const [historyOpen, setHistoryOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [editing, setEditing] = useState<EventTemplateRead | null>(null);

  // BE はフラット配列で共通+個人を返す。**必ず is_shared で切り分ける** —
  // sort_order はスコープ内で独立した連番なので混ぜると並びが壊れる。
  const rows = useMemo(() => {
    const scoped = (data ?? []).filter((t) => t.is_shared === isShared);
    return [...scoped].sort((a, b) => a.sort_order - b.sort_order);
  }, [data, isShared]);

  async function move(index: number, delta: number) {
    const next = [...rows];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    const [moved] = next.splice(index, 1);
    next.splice(target, 0, moved!);
    try {
      await reorder.mutateAsync({ staffId, orderedIds: next.map((t) => t.id) });
    } catch (e) {
      toast.error('並べ替えに失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  async function toggleActive(t: EventTemplateRead) {
    try {
      // PATCH のスキーマは title 必須 (部分更新でも型を満たすため現値を添える)。
      await update.mutateAsync({
        id: t.id,
        payload: { title: t.title, is_active: !t.is_active },
      });
      toast.success(t.is_active ? 'ひな形を無効にしました' : 'ひな形を有効にしました');
    } catch (e) {
      toast.error('変更に失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  return (
    <Card data-testid="event-templates-card">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>{heading ?? (isShared ? 'イベントひな形（共通）' : 'ひな形（個人）')}</CardTitle>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!canEdit}
            title={canEdit ? undefined : '編集権限がありません'}
            onClick={() => setHistoryOpen((v) => !v)}
            data-testid="event-template-history-toggle"
          >
            <History className="h-4 w-4" />
            履歴から追加
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!canEdit}
            title={canEdit ? undefined : '編集権限がありません'}
            onClick={() => setAddOpen(true)}
            data-testid="event-template-add"
          >
            <Plus className="h-4 w-4" />
            手入力で追加
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <p className="mb-3 text-xs text-text-muted">
          {isShared
            ? 'ここに登録したひな形は、スケジュール画面・スタッフマスタの「イベント追加」のプルダウンに全スタッフ共通で表示されます。↑↓ で並べ替え＝プルダウンの表示順。'
            : 'このスタッフを選んだときだけプルダウンに出るひな形です。↑↓ で並べ替え＝プルダウンの表示順。'}
        </p>

        {isLoading && <Skeleton className="h-16 w-full" />}
        {isError && (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        )}
        {data && rows.length === 0 && (
          <p className="text-sm text-text-muted">ひな形はまだありません</p>
        )}
        {rows.length > 0 && (
          <ul className="space-y-1.5">
            {rows.map((t, i) => (
              <li
                key={t.id}
                data-testid={`event-template-row-${t.id}`}
                className={cn(
                  'flex items-center gap-2 rounded-lg border border-border-default bg-bg-base px-3 py-2',
                  !t.is_active && 'opacity-50',
                )}
              >
                <span
                  className={cn(
                    'shrink-0 rounded-full border px-2 py-0.5 text-[11px]',
                    t.event_type === 'training'
                      ? 'border-warning-strong bg-warning-bg text-warning-strong'
                      : 'border-border-default bg-bg-muted text-text-secondary',
                  )}
                >
                  {TYPE_LABEL[t.event_type]}
                </span>
                <span
                  className={cn(
                    'truncate font-bold text-text-primary',
                    !t.is_active && 'line-through',
                  )}
                >
                  {t.title}
                </span>
                <span className="tnum shrink-0 text-xs text-text-secondary">{timeLabel(t)}</span>
                {t.blocking && (
                  <span className="shrink-0 text-xs" title="提案エンジンでも潰されません">
                    🔒
                  </span>
                )}
                <span className="flex-1" />
                {!t.is_active && (
                  <span className="shrink-0 text-[11px] text-text-muted">
                    無効（プルダウンに出ない）
                  </span>
                )}
                {canEdit && (
                  <>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 shrink-0 p-0"
                      aria-label={`${t.title} を上へ`}
                      disabled={i === 0 || reorder.isPending}
                      onClick={() => void move(i, -1)}
                      data-testid={`event-template-up-${t.id}`}
                    >
                      <ArrowUp className="h-4 w-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 shrink-0 p-0"
                      aria-label={`${t.title} を下へ`}
                      disabled={i === rows.length - 1 || reorder.isPending}
                      onClick={() => void move(i, 1)}
                      data-testid={`event-template-down-${t.id}`}
                    >
                      <ArrowDown className="h-4 w-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 shrink-0 p-0"
                      aria-label={`${t.title} を編集`}
                      onClick={() => setEditing(t)}
                      data-testid={`event-template-edit-${t.id}`}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 shrink-0 p-0"
                      aria-label={t.is_active ? `${t.title} を無効化` : `${t.title} を有効化`}
                      onClick={() => void toggleActive(t)}
                      disabled={update.isPending}
                      data-testid={`event-template-toggle-${t.id}`}
                    >
                      {t.is_active ? (
                        <Eye className="h-4 w-4" />
                      ) : (
                        <EyeOff className="h-4 w-4" />
                      )}
                    </Button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}

        {historyOpen && canEdit && <HistoryPanel scope={scope} canEdit={canEdit} />}
      </CardContent>

      {canEdit && (
        <>
          <EventTemplateFormDialog open={addOpen} onOpenChange={setAddOpen} scope={scope} />
          <EventTemplateFormDialog
            open={editing !== null}
            onOpenChange={(o) => {
              if (!o) setEditing(null);
            }}
            template={editing}
            scope={scope}
          />
        </>
      )}
    </Card>
  );
}
