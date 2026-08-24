'use client';

/**
 * Edit (or delete) an existing staff event.
 *
 * Hooks: `useUpdateEvent` (PATCH) + `useDeleteEvent` (DELETE).
 * Delete is gated behind a nested `<DeleteConfirmModal>` (AlertDialog
 * equivalent) so an accidental tap can't drop the row.
 *
 * 「☆ ひな形にする」(PO 2026-08-25): フッター左の 1 ボタンで、**いま入力欄に
 * ある内容** (保存前の手直し込み) を `EventTemplateFormDialog` へ引き継いで
 * ひな形化する。保存先 (共通 / このスタッフの個人) はそのダイアログで選ぶ。
 * イベント本体の保存とは独立 (ひな形にしてもイベントは更新されない)。
 */
import { useEffect, useState } from 'react';
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
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useDeleteEvent, useUpdateEvent } from '@/lib/queries/staff-events';
import { eventCreateSchema, type EventCreate, type EventRead } from '@/lib/schemas/staff-events';

import { DeleteConfirmModal } from '../../_components/DeleteConfirmModal';
import {
  EventTemplateFormDialog,
  SHARED_TEMPLATE_SCOPE,
  type EventTemplateFormInitial,
} from '../../_components/EventTemplatesCard';

interface EventEditDialogProps {
  staffId: string;
  event: EventRead | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function EventEditDialog({ staffId, event, open, onOpenChange }: EventEditDialogProps) {
  const update = useUpdateEvent(staffId);
  const remove = useDeleteEvent(staffId);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // 🔒絶対に潰せないイベント (2段階提案): ON なら提案エンジンのフォールバック
  // (イベント無視の再算出) でも占有として扱われ、衝突提案が出ない。
  const [blocking, setBlocking] = useState(false);
  // ☆ ひな形にする — 押した瞬間の入力欄の内容をスナップショットして渡す
  // (null = ひな形ダイアログは閉じている)。
  const [templateInitial, setTemplateInitial] = useState<EventTemplateFormInitial | null>(null);
  // 親ダイアログが閉じたらひな形ダイアログも必ず閉じる (取り残し・次回の古い値の再利用を防ぐ)。
  useEffect(() => {
    if (!open) setTemplateInitial(null);
  }, [open]);

  // Re-use the create schema for edits — every field is required when
  // present in the form, and the API accepts the full body for PATCH too.
  const form = useForm<EventCreate>({
    // ZodEffects (from `.refine`) doesn't infer cleanly through zodResolver;
    // cast matches the pattern used in `PatientForm.tsx`.
    resolver: zodResolver(eventCreateSchema) as Resolver<EventCreate>,
    defaultValues: {
      date: event?.date ?? '',
      title: event?.title ?? '',
      start_time: event?.start_time ?? '09:00',
      end_time: event?.end_time ?? '17:00',
      type: event?.type ?? '研修',
      note: event?.note ?? '',
    },
  });

  // Reset whenever a new event is bound (open) so old values don't bleed.
  useEffect(() => {
    if (event && open) {
      form.reset({
        date: event.date,
        title: event.title,
        start_time: event.start_time,
        end_time: event.end_time,
        type: event.type,
        note: event.note ?? '',
      });
      setBlocking(event.blocking ?? false);
    }
  }, [event, open, form]);

  const onSubmit = async (values: EventCreate) => {
    if (!event) return;
    try {
      await update.mutateAsync({
        eventId: event.id,
        payload: {
          ...values,
          note: values.note && values.note.trim() !== '' ? values.note.trim() : null,
          blocking,
        },
      });
      toast.success('イベントを更新しました');
      onOpenChange(false);
    } catch (err) {
      toast.error(`更新に失敗しました: ${err instanceof Error ? err.message : '不明なエラー'}`);
    }
  };

  const onDelete = async () => {
    if (!event) return;
    try {
      await remove.mutateAsync(event.id);
      toast.success('イベントを削除しました');
      setConfirmDelete(false);
      onOpenChange(false);
    } catch (err) {
      toast.error(`削除に失敗しました: ${err instanceof Error ? err.message : '不明なエラー'}`);
    }
  };

  const isBusy = update.isPending || remove.isPending;

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next && isBusy) return;
          onOpenChange(next);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>研修・イベントを編集</DialogTitle>
            <DialogDescription>内容を変更するか、削除できます。</DialogDescription>
          </DialogHeader>

          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="grid gap-4 py-2">
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

              {/* 🔒 絶対に潰せないイベント (イベント考慮2段階提案・PO確定 2026-07-27) */}
              <div className="flex items-start justify-between gap-3 rounded-md border border-border-default bg-bg-muted/50 px-3 py-2.5">
                <div className="space-y-0.5">
                  <p className="text-sm font-medium text-text-primary">
                    🔒 この時間は絶対に空けておく
                  </p>
                  <p className="text-xs text-text-muted">
                    ONにすると、空き枠がない場合でもこのイベントに重ねた配置提案は出ません。 （OFF:
                    空き枠がないときだけ「イベントを動かす前提」の提案が出ます）
                  </p>
                </div>
                <Switch
                  checked={blocking}
                  onCheckedChange={setBlocking}
                  aria-label="この時間は絶対に空けておく"
                  data-testid="event-blocking-toggle"
                />
              </div>

              <DialogFooter className="sm:justify-between">
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="destructive"
                    onClick={() => setConfirmDelete(true)}
                    disabled={isBusy}
                  >
                    削除
                  </Button>
                  {/* ☆ ひな形にする — いま入力欄にある内容 (保存前の手直し込み) を引き継ぐ */}
                  <Button
                    type="button"
                    variant="outline"
                    disabled={isBusy}
                    data-testid="event-edit-save-template"
                    onClick={() => {
                      const v = form.getValues();
                      setTemplateInitial({
                        title: v.title,
                        event_type: v.type === '研修' ? 'training' : 'event',
                        start_time: v.start_time,
                        end_time: v.end_time,
                        blocking,
                        note: v.note && v.note.trim() !== '' ? v.note.trim() : null,
                      });
                    }}
                  >
                    ☆ ひな形にする
                  </Button>
                </div>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => onOpenChange(false)}
                    disabled={isBusy}
                  >
                    キャンセル
                  </Button>
                  <Button type="submit" disabled={isBusy}>
                    {update.isPending ? '保存中…' : '保存'}
                  </Button>
                </div>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>

      <DeleteConfirmModal
        open={confirmDelete}
        title="イベントを削除しますか？"
        description="この操作は元に戻せません。"
        confirming={remove.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={onDelete}
      />

      {/* ☆ ひな形にする — 保存先 (共通 / このスタッフの個人) を選んで登録 */}
      <EventTemplateFormDialog
        open={templateInitial !== null}
        onOpenChange={(next) => {
          if (!next) setTemplateInitial(null);
        }}
        scope={SHARED_TEMPLATE_SCOPE}
        scopeChoice={{ staffId }}
        initial={templateInitial ?? undefined}
      />
    </>
  );
}
