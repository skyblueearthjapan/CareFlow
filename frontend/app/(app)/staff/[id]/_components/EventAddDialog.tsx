'use client';

/**
 * Add a new staff event (研修日 / イベント).
 *
 * Hooks: `useCreateEvent(staffId)` (POST /api/v1/staff/:id/events)
 * Validation: `eventCreateSchema` zod refine — start_time < end_time;
 *   title 1-100 chars enforced inline by react-hook-form via zodResolver.
 */
import { useEffect } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
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
import { useCreateEvent } from '@/lib/queries/staff-events';
import {
  eventCreateSchema,
  type EventCreate,
} from '@/lib/schemas/staff-events';

interface EventAddDialogProps {
  staffId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Optional default date (yyyy-MM-dd) to prefill — e.g. today. */
  defaultDate?: string;
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
}: EventAddDialogProps) {
  const create = useCreateEvent(staffId);

  const form = useForm<EventCreate>({
    // ZodEffects (from `.refine`) doesn't infer cleanly through zodResolver;
    // cast matches the pattern used in `PatientForm.tsx`.
    resolver: zodResolver(eventCreateSchema) as Resolver<EventCreate>,
    defaultValues: {
      date: defaultDate ?? todayIso(),
      title: '',
      start_time: '09:00',
      end_time: '17:00',
      type: '研修',
      note: '',
    },
  });

  // Reset whenever the dialog re-opens (defaultDate may have changed).
  useEffect(() => {
    if (open) {
      form.reset({
        date: defaultDate ?? todayIso(),
        title: '',
        start_time: '09:00',
        end_time: '17:00',
        type: '研修',
        note: '',
      });
    }
  }, [open, defaultDate, form]);

  const onSubmit = async (values: EventCreate) => {
    try {
      const payload: EventCreate = {
        ...values,
        note:
          values.note && values.note.trim() !== '' ? values.note.trim() : null,
      };
      await create.mutateAsync(payload);
      toast.success('イベントを追加しました');
      onOpenChange(false);
    } catch (err) {
      toast.error(
        `追加に失敗しました: ${err instanceof Error ? err.message : '不明なエラー'}`,
      );
    }
  };

  const isBusy = create.isPending;

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
          <DialogDescription>
            日付・時刻・種別を入力して保存してください。
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="grid gap-4 py-2"
          >
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
                  <Select
                    value={field.value}
                    onValueChange={field.onChange}
                  >
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
                    <Textarea
                      rows={3}
                      {...field}
                      value={field.value ?? ''}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
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
