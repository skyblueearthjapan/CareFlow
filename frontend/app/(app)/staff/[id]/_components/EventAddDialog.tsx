'use client';

/**
 * Add a new staff event (研修日 / イベント).
 *
 * Hooks: `useCreateEvent(staffId)` (POST /api/v1/staff/:id/events)
 * Validation: `eventCreateSchema` zod refine — start_time < end_time;
 *   title 1-100 chars enforced inline by react-hook-form via zodResolver.
 *
 * Wave 2-D (staff-event-history-design.md §2 Phase 2/3 ・
 * docs/mockups/event-add-dialog-mock.html): 最上部に 📋 ひな形プルダウン
 * (共通 + このスタッフの個人)、フッター上に ☆ひな形に保存 / 📌毎週固定化。
 * 🔒(blocking) はひな形経由でのみ入る (独立チェックボックスは置かない)。
 */
import { useEffect, useRef, useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useSession } from 'next-auth/react';
import { useForm, type Resolver } from 'react-hook-form';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
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
import {
  EventDialogOptions,
  EventTemplateBar,
  eventTypeToTemplateType,
  initialOptionsValue,
  templateTypeToEventType,
  weekdayFromIsoDate,
  type EventDialogOptionsValue,
} from '@/components/events/EventDialogExtras';
import { useCreateEventTemplate } from '@/lib/queries/event-templates';
import { useBulkCreateEventDefaults } from '@/lib/queries/staff-event-defaults';
import { useCreateEvent } from '@/lib/queries/staff-events';
import { isAdminRole } from '@/lib/rbac';
import { eventCreateSchema, type EventCreate } from '@/lib/schemas/staff-events';

interface EventAddDialogProps {
  staffId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Optional default date (yyyy-MM-dd) to prefill — e.g. today. */
  defaultDate?: string;
  /** T-2 ②-a: タイムライン空き枠からの起動時に開始/終了時刻をプレフィルする。 */
  defaultStart?: string;
  defaultEnd?: string;
}

function todayIso(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

export function EventAddDialog({
  staffId,
  open,
  onOpenChange,
  defaultDate,
  defaultStart,
  defaultEnd,
}: EventAddDialogProps) {
  const create = useCreateEvent(staffId);
  const createTemplate = useCreateEventTemplate();
  const bulkCreateDefaults = useBulkCreateEventDefaults();
  const { data: session } = useSession();
  const canEditMasters = isAdminRole(session?.user?.role);

  const [options, setOptions] = useState<EventDialogOptionsValue>(() =>
    initialOptionsValue(defaultDate),
  );
  // 再送で ☆/📌 が二重に走らないようにする (後続処理は初回の成功時のみ)。
  const extrasDoneRef = useRef(false);
  const [extrasRunning, setExtrasRunning] = useState(false);

  const form = useForm<EventCreate>({
    // ZodEffects (from `.refine`) doesn't infer cleanly through zodResolver;
    // cast matches the pattern used in `PatientForm.tsx`.
    resolver: zodResolver(eventCreateSchema) as Resolver<EventCreate>,
    defaultValues: {
      date: defaultDate ?? todayIso(),
      title: '',
      start_time: defaultStart ?? '09:00',
      end_time: defaultEnd ?? '17:00',
      type: '研修',
      note: '',
      blocking: false,
    },
  });

  // Reset whenever the dialog re-opens (defaults may have changed).
  useEffect(() => {
    if (open) {
      form.reset({
        date: defaultDate ?? todayIso(),
        title: '',
        start_time: defaultStart ?? '09:00',
        end_time: defaultEnd ?? '17:00',
        type: '研修',
        note: '',
        blocking: false,
      });
      setOptions(initialOptionsValue(defaultDate));
      extrasDoneRef.current = false;
    }
  }, [open, defaultDate, defaultStart, defaultEnd, form]);

  const handleOptionsChange = (next: EventDialogOptionsValue) => {
    // 📌 を ON にした瞬間の既定曜日 = いま入力中の日付の曜日。
    if (next.fixWeekly && !options.fixWeekly) {
      setOptions({ ...next, weekdays: [weekdayFromIsoDate(form.getValues('date'))] });
      return;
    }
    setOptions(next);
  };

  /**
   * ☆ひな形保存 / 📌固定イベント (対象はこのスタッフのみ)。
   * イベント本体が作れなかった時は呼ばれない。失敗しても本体は覆さず toast のみ。
   */
  const runExtras = async (values: EventCreate) => {
    if (options.saveTemplate) {
      try {
        await createTemplate.mutateAsync({
          staff_id: options.templateScope === 'personal' ? staffId : null,
          title: values.title,
          event_type: eventTypeToTemplateType(values.type),
          start_time: values.start_time,
          end_time: values.end_time,
          blocking: values.blocking ?? false,
          note: values.note && values.note.trim() !== '' ? values.note.trim() : null,
        });
        toast.success('ひな形に保存しました');
      } catch {
        toast.error('ひな形の保存に失敗しました');
      }
    }
    if (options.fixWeekly && options.weekdays.length > 0) {
      try {
        const res = await bulkCreateDefaults.mutateAsync({
          staff_ids: [staffId],
          weekdays: options.weekdays,
          start_time: values.start_time,
          end_time: values.end_time,
          title: values.title,
          blocking: values.blocking ?? false,
        });
        toast.success(
          `固定イベントを ${res.created}件 登録しました` +
            (res.skipped > 0 ? `（${res.skipped}件は登録済みのためスキップ）` : ''),
        );
      } catch {
        toast.error('固定イベントの登録に失敗しました');
      }
    }
  };

  const onSubmit = async (values: EventCreate) => {
    const payload: EventCreate = {
      ...values,
      note: values.note && values.note.trim() !== '' ? values.note.trim() : null,
    };
    try {
      await create.mutateAsync(payload);
    } catch (err) {
      toast.error(`追加に失敗しました: ${err instanceof Error ? err.message : '不明なエラー'}`);
      return;
    }
    toast.success('イベントを追加しました');
    if (!extrasDoneRef.current) {
      extrasDoneRef.current = true;
      setExtrasRunning(true);
      await runExtras(payload);
      setExtrasRunning(false);
    }
    onOpenChange(false);
  };

  const isBusy = create.isPending || extrasRunning;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && isBusy) return;
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>研修・イベントを追加</DialogTitle>
          <DialogDescription>日付・時刻・種別を入力して保存してください。</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="grid gap-4 py-2">
            {/* 📋 ひな形バー (共通 + このスタッフの個人)。ひな形 0 件なら描かない。 */}
            <EventTemplateBar
              staffId={staffId}
              staffName={null}
              testIdPrefix="staff-event"
              onApply={(t) => {
                form.setValue('title', t.title, { shouldValidate: true });
                form.setValue('type', templateTypeToEventType(t));
                // ひな形の時刻が null (=その場で入力) なら現在値を保持する。
                if (t.start_time) form.setValue('start_time', t.start_time.slice(0, 5));
                if (t.end_time) form.setValue('end_time', t.end_time.slice(0, 5));
                form.setValue('note', t.note ?? '');
                form.setValue('blocking', t.blocking);
              }}
            />

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
                      <SelectItem value="研修">研修</SelectItem>
                      <SelectItem value="イベント">イベント</SelectItem>
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="title"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>タイトル</FormLabel>
                  <FormControl>
                    <Input maxLength={100} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid grid-cols-2 gap-4">
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
                    <Textarea rows={3} {...field} value={field.value ?? ''} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <EventDialogOptions
              value={options}
              onChange={handleOptionsChange}
              canEdit={canEditMasters}
              personalScopeLabel="このスタッフの個人ひな形"
              fixWeeklyTargetLabel="このスタッフ"
              testIdPrefix="staff-event"
            />

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isBusy}
              >
                キャンセル
              </Button>
              <Button type="submit" disabled={isBusy}>
                {isBusy ? '保存中…' : '保存'}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
