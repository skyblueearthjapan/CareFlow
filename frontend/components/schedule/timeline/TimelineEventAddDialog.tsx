'use client';

/**
 * TimelineEventAddDialog — タイムラインからの「スタッフの打合せ・イベント追加」(D-1)。
 *
 * 旧: 空き枠クリック→イベント切替は EventAddDialog (その列の担当スタッフ 1 名宛固定)。
 * PO要望 (2026-07-08): 「他のコースに割り振られているスタッフ・アプリに登録されている
 * 管理スタッフも含めて打合せを追加したい」→ スタッフマスタ全員から**複数選択**して
 * 一括登録できるダイアログに置き換える (例:「3人だけの会議」)。
 *
 * - 既定選択 = 起動元の列の担当スタッフ (未割当列からは選択なしで開く)
 * - 登録は選択スタッフごとに POST (useCreateEventForStaff)。部分失敗時は
 *   成功済みスタッフをチェックから外してダイアログを維持 (再送で二重登録しない)
 * - イベントは staff_events = **カイポケ反映外** (説明文に明示・既存方針)
 */
import * as React from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm, type Resolver } from 'react-hook-form';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useCreateEventForStaff } from '@/lib/queries/staff-events';
import { eventCreateSchema, type EventCreate } from '@/lib/schemas/staff-events';

export interface TimelineEventStaffOption {
  id: string;
  name: string;
}

export interface TimelineEventAddDialogProps {
  open: boolean;
  onClose: () => void;
  /** 選択候補 = スタッフマスタ全員 (他コース担当・管理職含む)。 */
  staffOptions: TimelineEventStaffOption[];
  /** 既定で選択しておくスタッフ (起動元の列の担当)。 */
  defaultStaffIds?: string[];
  /** yyyy-MM-dd */
  defaultDate?: string;
  defaultStart?: string;
  defaultEnd?: string;
}

function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`;
}

export function TimelineEventAddDialog({
  open,
  onClose,
  staffOptions,
  defaultStaffIds,
  defaultDate,
  defaultStart,
  defaultEnd,
}: TimelineEventAddDialogProps) {
  const createForStaff = useCreateEventForStaff();
  const [selected, setSelected] = React.useState<Set<string>>(new Set(defaultStaffIds ?? []));
  const [staffError, setStaffError] = React.useState<string | null>(null);
  const [filter, setFilter] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const form = useForm<EventCreate>({
    resolver: zodResolver(eventCreateSchema) as Resolver<EventCreate>,
    defaultValues: {
      date: defaultDate ?? todayIso(),
      title: '',
      start_time: defaultStart ?? '09:00',
      end_time: defaultEnd ?? '17:00',
      type: 'イベント',
      note: '',
    },
  });

  // 開き直しでフォームと選択をリセット (defaults が変わり得るため)。
  React.useEffect(() => {
    if (open) {
      form.reset({
        date: defaultDate ?? todayIso(),
        title: '',
        start_time: defaultStart ?? '09:00',
        end_time: defaultEnd ?? '17:00',
        type: 'イベント',
        note: '',
      });
      setSelected(new Set(defaultStaffIds ?? []));
      setStaffError(null);
      setFilter('');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, defaultDate, defaultStart, defaultEnd, (defaultStaffIds ?? []).join(',')]);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setStaffError(null);
  };

  const filtered = filter.trim()
    ? staffOptions.filter((s) => s.name.includes(filter.trim()))
    : staffOptions;

  const onSubmit = async (values: EventCreate) => {
    const targets = staffOptions.filter((s) => selected.has(s.id));
    if (targets.length === 0) {
      setStaffError('スタッフを 1 名以上選択してください');
      return;
    }
    const payload: EventCreate = {
      ...values,
      note: values.note && values.note.trim() !== '' ? values.note.trim() : null,
    };
    setSubmitting(true);
    // ID で成否を追跡する (同姓同名スタッフを名前で誤マッチさせない・レビューHIGH対応)。
    const okIds: string[] = [];
    const ngIds: string[] = [];
    // 逐次 POST (件数は数名規模。部分失敗をスタッフ単位で特定できるよう並列にしない)。
    for (const s of targets) {
      try {
        await createForStaff.mutateAsync({ staffId: s.id, payload });
        okIds.push(s.id);
      } catch {
        ngIds.push(s.id);
      }
    }
    setSubmitting(false);
    if (ngIds.length === 0) {
      toast.success(`「${payload.title}」を ${okIds.length} 名に登録しました（カイポケ反映外）`);
      onClose();
      return;
    }
    // 部分失敗: 成功済みを選択から外してダイアログ維持 (再送しても二重登録しない)。
    setSelected(new Set(ngIds));
    const ngNames = targets.filter((s) => ngIds.includes(s.id)).map((s) => s.name);
    toast.error(
      `${ngNames.join('・')} への登録に失敗しました${
        okIds.length > 0 ? `（${okIds.length} 名は登録済み）` : ''
      }。もう一度お試しください`,
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && submitting) return;
        if (!next) onClose();
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>スタッフの打合せ・イベントを追加</DialogTitle>
          <DialogDescription>
            参加するスタッフを選んで登録します（複数選択可・カイポケには反映されません）。
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="grid gap-3 py-1">
            {/* 参加スタッフ (複数選択・全スタッフ = 他コース担当/管理職含む) */}
            <div>
              <div className="mb-1 flex items-baseline justify-between">
                <span className="text-sm font-medium">参加スタッフ</span>
                <span className="tnum text-xs text-text-muted" data-testid="tl-event-staff-count">
                  {selected.size} 名選択中
                </span>
              </div>
              <Input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="名前で絞り込み"
                className="mb-1 h-8 text-sm"
                aria-label="スタッフを名前で絞り込み"
              />
              <div
                className="max-h-40 overflow-y-auto rounded border border-border-default p-1"
                data-testid="tl-event-staff-list"
              >
                {filtered.length === 0 ? (
                  <div className="px-2 py-3 text-xs text-text-muted">
                    該当するスタッフがいません
                  </div>
                ) : (
                  filtered.map((s) => (
                    <label
                      key={s.id}
                      className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-bg-muted"
                    >
                      <Checkbox
                        checked={selected.has(s.id)}
                        onCheckedChange={() => toggle(s.id)}
                        aria-label={`${s.name} を選択`}
                        data-testid={`tl-event-staff-${s.id}`}
                      />
                      <span className="truncate">{s.name}</span>
                    </label>
                  ))
                )}
              </div>
              {staffError ? (
                <p
                  className="mt-1 text-xs font-medium text-error"
                  data-testid="tl-event-staff-error"
                >
                  {staffError}
                </p>
              ) : null}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <FormField
                control={form.control}
                name="date"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>日付</FormLabel>
                    <FormControl>
                      <Input type="date" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="type"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>種別</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger aria-label="種別を選択">
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="イベント">イベント</SelectItem>
                        <SelectItem value="研修">研修</SelectItem>
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>タイトル</FormLabel>
                  <FormControl>
                    <Input maxLength={100} placeholder="例: 週次打合せ" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid grid-cols-2 gap-3">
              <FormField
                control={form.control}
                name="start_time"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>開始時刻</FormLabel>
                    <FormControl>
                      <Input type="time" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="end_time"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>終了時刻</FormLabel>
                    <FormControl>
                      <Input type="time" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="note"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>備考</FormLabel>
                  <FormControl>
                    <Textarea rows={2} {...field} value={field.value ?? ''} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
                キャンセル
              </Button>
              <Button type="submit" disabled={submitting} data-testid="tl-event-submit">
                {submitting ? '登録中…' : `この内容で登録（${selected.size} 名）`}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
