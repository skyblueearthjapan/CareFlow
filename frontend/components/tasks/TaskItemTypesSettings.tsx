'use client';

/**
 * やることの「項目の設定」(管理者・設計 §4)。
 *
 * 事業所の項目名はコードに持たないので、事務所がここで足す・名前を変える・
 * 使わないにする・並び順を変える。使わない項目は付けるときのチップに出ない
 * (付いているやることはそのまま)。
 */
import { useState } from 'react';
import { ArrowDown, ArrowUp } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { toast } from '@/components/ui/sonner';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import {
  useCreateTaskItemType,
  useTaskItemTypes,
  useUpdateTaskItemType,
} from '@/lib/queries/tasks';
import type { TaskItemTypeRead } from '@/lib/schemas/patient-task';

export function TaskItemTypesSettings() {
  const query = useTaskItemTypes({ includeInactive: true });
  const items = [...(query.data ?? [])].sort(
    (a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name, 'ja'),
  );
  const create = useCreateTaskItemType();
  const update = useUpdateTaskItemType();
  const [newName, setNewName] = useState('');
  const busy = create.isPending || update.isPending;

  const onError = (fallback: string) => (err: unknown) =>
    toast.error(fallback, { description: apiErrorMessage(err) });

  const add = () => {
    const name = newName.trim();
    if (!name || busy) return;
    create.mutate(
      { name },
      {
        onSuccess: () => {
          setNewName('');
          toast.success(`「${name}」を足しました`);
        },
        onError: onError('項目を足せませんでした'),
      },
    );
  };

  /** 隣と並び順を入れ替える (同じ番号なら 1 つずらす)。 */
  const move = (idx: number, dir: -1 | 1) => {
    const a = items[idx];
    const b = items[idx + dir];
    if (!a || !b || busy) return;
    // 2 回の更新のどちらかが失敗すると並び順が片方だけ変わるので、一覧を読み直す。
    const onMoveError = (err: unknown) => {
      onError('並び順を変えられませんでした')(err);
      void query.refetch();
    };
    if (a.sort_order === b.sort_order) {
      update.mutate(
        { id: a.id, payload: { sort_order: b.sort_order + dir } },
        { onError: onMoveError },
      );
      return;
    }
    update.mutate(
      { id: a.id, payload: { sort_order: b.sort_order } },
      {
        onSuccess: () =>
          update.mutate(
            { id: b.id, payload: { sort_order: a.sort_order } },
            { onError: onMoveError },
          ),
        onError: onMoveError,
      },
    );
  };

  return (
    <div className="space-y-3" data-testid="task-item-types-settings">
      {query.isLoading ? (
        <p className="text-sm text-text-muted">読み込み中…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-text-muted">項目はまだありません。下の欄から足してください。</p>
      ) : (
        <ul className="m-0 list-none space-y-2 p-0">
          {items.map((it, idx) => (
            <ItemRow
              key={`${it.id}:${it.name}`}
              item={it}
              first={idx === 0}
              last={idx === items.length - 1}
              busy={busy}
              onMove={(dir) => move(idx, dir)}
              onRename={(name) =>
                update.mutate(
                  { id: it.id, payload: { name } },
                  {
                    onSuccess: () => toast.success('名前を変えました'),
                    onError: onError('名前を変えられませんでした'),
                  },
                )
              }
              onToggleActive={(next) =>
                update.mutate(
                  { id: it.id, payload: { is_active: next } },
                  { onError: onError('切り替えられませんでした') },
                )
              }
            />
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="text"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) add();
          }}
          maxLength={100}
          placeholder="新しい項目（例：訪問看護計画書の控えを渡す）"
          className="h-10 min-w-0 flex-1 rounded-md border border-border-default bg-bg-base px-3 text-sm"
          data-testid="task-item-type-new"
        />
        <Button type="button" disabled={!newName.trim() || busy} onClick={add}>
          足す
        </Button>
      </div>
    </div>
  );
}

function ItemRow({
  item,
  first,
  last,
  busy,
  onMove,
  onRename,
  onToggleActive,
}: {
  item: TaskItemTypeRead;
  first: boolean;
  last: boolean;
  busy: boolean;
  onMove: (dir: -1 | 1) => void;
  onRename: (name: string) => void;
  onToggleActive: (next: boolean) => void;
}) {
  const [name, setName] = useState(item.name);
  const changed = name.trim() !== item.name && name.trim().length > 0;
  return (
    <li
      className="flex flex-wrap items-center gap-2 rounded-md border border-border-default px-3 py-2"
      data-testid={`task-item-type-${item.id}`}
    >
      <div className="flex gap-1">
        <Button
          type="button"
          variant="outline"
          size="icon"
          disabled={first || busy}
          onClick={() => onMove(-1)}
          aria-label={`${item.name} を上へ`}
          className="h-9 w-9"
        >
          <ArrowUp className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="icon"
          disabled={last || busy}
          onClick={() => onMove(1)}
          aria-label={`${item.name} を下へ`}
          className="h-9 w-9"
        >
          <ArrowDown className="h-4 w-4" />
        </Button>
      </div>
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={100}
        aria-label="項目の名前"
        className="h-9 min-w-[12rem] flex-1 rounded-md border border-border-default bg-bg-base px-3 text-sm"
      />
      {changed && (
        <Button
          type="button"
          size="sm"
          disabled={busy}
          onClick={() => onRename(name.trim())}
          className="h-9"
        >
          名前を変える
        </Button>
      )}
      <label className="flex items-center gap-2 text-sm text-text-secondary">
        <Switch
          checked={item.is_active}
          disabled={busy}
          onCheckedChange={onToggleActive}
          aria-label={`${item.name} を使う`}
        />
        {item.is_active ? '使う' : '使わない'}
      </label>
    </li>
  );
}
