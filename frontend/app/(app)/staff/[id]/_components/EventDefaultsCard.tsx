'use client';

/**
 * EventDefaultsCard — 「毎週の固定イベント」カード (Phase 2)。
 *
 * 正典 = docs/plans/kaipoke-event-two-way-design.md §3-②。
 * 朝会などの毎週決まったイベントを 1 回定義すると、週生成のたびに当該週の
 * イベント (source='fixed') として自動展開される。カイポケへは職員スケジュール
 * タブの「カイポケへ送る」で反映できる。
 * 定義の変更・削除は「次の週展開から」効く (展開済みの週はイベント側で調整)。
 */
import { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
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
import {
  useCreateEventDefault,
  useDeleteEventDefault,
  useStaffEventDefaults,
} from '@/lib/queries/staff-event-defaults';

const WEEKDAY_OPTIONS = ['月', '火', '水', '木', '金', '土'] as const;

export function EventDefaultsCard({ staffId, canEdit }: { staffId: string; canEdit: boolean }) {
  const { data, isLoading, isError, error } = useStaffEventDefaults(staffId);
  const create = useCreateEventDefault(staffId);
  const remove = useDeleteEventDefault(staffId);

  const [addOpen, setAddOpen] = useState(false);
  const [weekday, setWeekday] = useState(0);
  const [start, setStart] = useState('09:00');
  const [end, setEnd] = useState('09:15');
  const [title, setTitle] = useState('朝会');
  const [blocking, setBlocking] = useState(false);

  async function handleAdd() {
    try {
      await create.mutateAsync({
        weekday,
        start_time: start,
        end_time: end,
        title: title.trim(),
        blocking,
      });
      toast.success(
        `毎週${WEEKDAY_OPTIONS[weekday]}曜の「${title.trim()}」を登録しました（次の週生成から自動で入ります）`,
      );
      setAddOpen(false);
    } catch (e) {
      toast.error('登録に失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  async function handleDelete(id: string, label: string) {
    if (
      !window.confirm(
        `${label} を削除しますか？\n次の週生成から自動展開されなくなります（展開済みの週のイベントはそのまま残ります）。`,
      )
    )
      return;
    try {
      await remove.mutateAsync(id);
      toast.success('固定イベントを削除しました');
    } catch (e) {
      toast.error('削除に失敗しました', {
        description: e instanceof Error ? e.message : String(e),
      });
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>毎週の固定イベント</CardTitle>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!canEdit}
          title={canEdit ? undefined : '編集権限がありません'}
          onClick={() => setAddOpen(true)}
          data-testid="event-default-add-button"
        >
          <Plus className="h-4 w-4" />
          追加
        </Button>
      </CardHeader>
      <CardContent>
        <p className="mb-3 text-xs text-text-muted">
          朝会など毎週決まったイベントを登録すると、週生成のたびに自動でその週へ入ります。
          カイポケへは職員スケジュールの「カイポケへ送る」で反映できます。
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
        {data && data.length === 0 && (
          <p className="text-sm text-text-muted">固定イベントはまだありません</p>
        )}
        {data && data.length > 0 && (
          <ul className="space-y-2">
            {data.map((d) => {
              const label = `毎週${d.weekday_label}曜 ${d.start_time}〜${d.end_time} ${d.title}`;
              return (
                <li
                  key={d.id}
                  className="flex items-center justify-between gap-2 rounded-md border border-border-default bg-bg-base px-3 py-2"
                  data-testid={`event-default-row-${d.id}`}
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-text-primary">
                      <Badge variant="secondary" className="mr-2">
                        毎週{d.weekday_label}曜
                      </Badge>
                      <span className="tnum">{d.start_time}〜{d.end_time}</span>
                      <span className="ml-2">{d.title}</span>
                      {d.blocking && (
                        <span className="ml-2 text-xs" title="提案エンジンでも絶対に潰されません">
                          🔒
                        </span>
                      )}
                    </p>
                    {d.note && <p className="truncate text-xs text-text-muted">{d.note}</p>}
                  </div>
                  {canEdit && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 shrink-0 p-0 text-text-muted hover:text-destructive"
                      aria-label={`${label} を削除`}
                      onClick={() => void handleDelete(d.id, label)}
                      disabled={remove.isPending}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>

      <Dialog open={addOpen} onOpenChange={(o) => !o && setAddOpen(false)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>毎週の固定イベントを追加</DialogTitle>
            <DialogDescription>
              登録すると次の週生成から毎週自動でイベントが入ります。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label htmlFor="ed-weekday">曜日</Label>
              <select
                id="ed-weekday"
                value={weekday}
                onChange={(e) => setWeekday(Number(e.target.value))}
                className="h-10 w-full rounded-md border border-border-default bg-bg-base px-3 text-sm"
              >
                {WEEKDAY_OPTIONS.map((w, i) => (
                  <option key={w} value={i}>
                    毎週{w}曜
                  </option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label htmlFor="ed-start">開始</Label>
                <Input
                  id="ed-start"
                  type="time"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="ed-end">終了</Label>
                <Input id="ed-end" type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="ed-title">名称</Label>
              <Input
                id="ed-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={255}
                placeholder="例: 朝会"
              />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={blocking} onCheckedChange={(v) => setBlocking(v === true)} />
              🔒 絶対に潰せないイベントにする（提案エンジンの対象外）
            </label>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setAddOpen(false)}>
              キャンセル
            </Button>
            <Button
              type="button"
              onClick={() => void handleAdd()}
              disabled={!title.trim() || !start || !end || end < start || create.isPending}
              data-testid="event-default-add-confirm"
            >
              {create.isPending ? '登録中…' : '登録する'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
