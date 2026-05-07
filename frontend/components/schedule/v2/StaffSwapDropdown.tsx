'use client';

/**
 * StaffSwapDropdown — コース行の主担当スタッフを差し替える dropdown (W15-FE D-3).
 *
 * UI:
 *   👤 [山本] ▼  ← クリックでスタッフ一覧 popover
 *
 * 動作:
 *   onChange で `PATCH /api/v1/courses/{id}` を呼び assigned_staff_id を更新する。
 *   楽観的更新は行わず、useUpdateCourse の onSuccess で courses キャッシュを
 *   invalidate して反映する (Wave 15 ではシンプルに保つ)。
 *
 * RBAC:
 *   canEdit=false の場合は表示のみ (popover を開けない).
 */
import { useState } from 'react';
import { ChevronDown, User as UserIcon } from 'lucide-react';
import { toast } from 'sonner';

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { useUpdateCourse } from '@/lib/queries/courses';
import type { StaffRead } from '@/lib/schemas/staff';
import { cn } from '@/lib/utils';

export interface StaffSwapDropdownProps {
  /** 当該コースの id (= CourseV2Read.id). 未存在 (まだ Course レコードが無い) なら null. */
  courseId: string | null;
  /** 現在の assigned_staff_id (null = 未割当). */
  currentStaffId: string | null;
  /** 全 active staff 一覧 (主担当候補). */
  staffList: StaffRead[];
  canEdit: boolean;
  /** 表示用バリアント. compact = 32px サークル, default = ラベル付き横長. */
  variant?: 'compact' | 'default';
  /** "👤" の代わりに使うラベル接頭辞 (例: "+" for 補佐). */
  iconPrefix?: string;
  /** 親側で楽観的に表示更新したい時のコールバック. */
  onChanged?: (nextStaffId: string | null) => void;
}

export function StaffSwapDropdown({
  courseId,
  currentStaffId,
  staffList,
  canEdit,
  variant = 'default',
  iconPrefix,
  onChanged,
}: StaffSwapDropdownProps) {
  const [open, setOpen] = useState(false);
  const updateMut = useUpdateCourse();

  const current = currentStaffId ? (staffList.find((s) => s.id === currentStaffId) ?? null) : null;
  const label = current ? (current.code ?? current.name) : '未割当';

  const handleSelect = async (nextId: string | null) => {
    setOpen(false);
    if (!courseId) {
      toast.warning('当該コースのレコードがまだ作成されていません');
      return;
    }
    if (nextId === currentStaffId) return;
    try {
      await updateMut.mutateAsync({
        id: courseId,
        patch: { assigned_staff_id: nextId },
      });
      onChanged?.(nextId);
      toast.success(
        nextId
          ? `スタッフを「${staffList.find((s) => s.id === nextId)?.name ?? nextId}」に変更しました`
          : 'スタッフ割当を解除しました',
      );
    } catch (err) {
      toast.error(`スタッフ差替に失敗しました: ${err instanceof Error ? err.message : '不明'}`);
    }
  };

  const triggerCls = cn(
    'inline-flex items-center gap-1 rounded border border-border-default bg-bg-base px-1.5 py-0.5 text-[10px] font-semibold leading-tight transition-colors',
    canEdit ? 'hover:bg-bg-muted cursor-pointer' : 'cursor-default opacity-70',
    variant === 'compact' ? 'min-w-[32px] justify-center' : 'min-w-[60px]',
  );

  return (
    <Popover open={open} onOpenChange={canEdit ? setOpen : undefined}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={triggerCls}
          disabled={!canEdit}
          aria-label={`担当スタッフ: ${label}`}
          title={current?.name ?? label}
        >
          {iconPrefix ? (
            <span aria-hidden>{iconPrefix}</span>
          ) : (
            <UserIcon className="h-3 w-3" aria-hidden />
          )}
          <span className="truncate">{label}</span>
          {canEdit ? <ChevronDown className="h-3 w-3 shrink-0 opacity-60" aria-hidden /> : null}
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-56 p-0" align="start">
        <div className="max-h-72 overflow-y-auto py-1">
          <button
            type="button"
            className={cn(
              'block w-full px-3 py-1.5 text-left text-xs hover:bg-bg-muted',
              currentStaffId === null && 'bg-bg-muted font-semibold',
            )}
            onClick={() => void handleSelect(null)}
          >
            — 未割当 —
          </button>
          {staffList.map((s) => (
            <button
              key={s.id}
              type="button"
              className={cn(
                'block w-full px-3 py-1.5 text-left text-xs hover:bg-bg-muted',
                currentStaffId === s.id && 'bg-bg-muted font-semibold',
              )}
              onClick={() => void handleSelect(s.id)}
            >
              <span className="font-semibold">{s.code ?? s.name}</span>
              {s.code ? <span className="ml-1 text-text-muted">{s.name}</span> : null}
            </button>
          ))}
          {staffList.length === 0 ? (
            <p className="px-3 py-2 text-xs text-text-muted">スタッフが登録されていません</p>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
