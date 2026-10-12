'use client';

/** 右の欄の左の端の境目（左右にドラッグ・←→・ダブルクリックで既定の幅）。 */
import type { useResizableWidth } from '@/components/agent/useResizableWidth';

export function ResizeHandle({
  handleProps,
  label,
  testId,
}: {
  handleProps: ReturnType<typeof useResizableWidth>['handleProps'];
  label: string;
  testId?: string;
}) {
  return (
    <div
      {...handleProps}
      aria-label={label}
      data-testid={testId}
      className="group relative hidden w-3 flex-none cursor-col-resize touch-none select-none outline-none lg:block"
    >
      <span
        aria-hidden
        className="absolute inset-y-2 left-1/2 w-px -translate-x-1/2 bg-border-default transition-colors group-hover:w-0.5 group-hover:bg-brand-primary group-focus-visible:w-0.5 group-focus-visible:bg-brand-primary group-data-[dragging=true]:w-0.5 group-data-[dragging=true]:bg-brand-primary"
      />
    </div>
  );
}
