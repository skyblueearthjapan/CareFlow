'use client';

// NOTE: react-day-picker is pinned at ~8.10.1.
// shadcn/ui v9 of this component is incompatible (classNames keys + IconLeft/Right replaced by Chevron).
// To upgrade to v9 see ADR-002 (TBD).

import * as React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { DayPicker } from 'react-day-picker';
import { ja } from 'date-fns/locale';

import { cn } from '@/lib/utils';
import { buttonVariants } from '@/components/ui/button';

export type CalendarProps = React.ComponentProps<typeof DayPicker>;

export function Calendar({
  className,
  classNames,
  showOutsideDays = true,
  locale = ja,
  ...props
}: CalendarProps) {
  return (
    <DayPicker
      showOutsideDays={showOutsideDays}
      locale={locale}
      className={cn('p-3', className)}
      classNames={{
        months: 'flex flex-col sm:flex-row gap-4',
        month: 'flex flex-col gap-4',
        caption: 'flex justify-center pt-1 relative items-center',
        caption_label: 'text-sm font-medium',
        nav: 'flex items-center gap-1',
        nav_button: cn(
          buttonVariants({ variant: 'outline', size: 'icon' }),
          'h-7 w-7 bg-transparent p-0 opacity-50 hover:opacity-100',
        ),
        nav_button_previous: 'absolute left-1',
        nav_button_next: 'absolute right-1',
        table: 'w-full border-collapse space-y-1',
        head_row: 'flex',
        head_cell: 'text-text-muted rounded-md w-9 font-normal text-[0.8rem]',
        row: 'flex w-full mt-2',
        cell: cn(
          'relative p-0 text-center text-sm focus-within:relative focus-within:z-20',
          '[&:has([aria-selected])]:bg-bg-muted',
          '[&:has([aria-selected].day-outside)]:bg-bg-muted/50',
          '[&:has([aria-selected].day-range-end)]:rounded-r-md',
          'first:[&:has([aria-selected])]:rounded-l-md',
          'last:[&:has([aria-selected])]:rounded-r-md',
        ),
        day: cn(
          buttonVariants({ variant: 'ghost' }),
          'h-9 w-9 p-0 font-normal aria-selected:opacity-100',
        ),
        day_range_end: 'day-range-end',
        day_selected:
          'bg-brand-primary text-white hover:bg-brand-primary hover:text-white focus:bg-brand-primary focus:text-white',
        day_today: 'bg-bg-muted text-text-primary',
        day_outside:
          'day-outside text-text-muted aria-selected:bg-bg-muted/50 aria-selected:text-text-muted',
        day_disabled: 'text-text-muted opacity-50',
        day_range_middle: 'aria-selected:bg-bg-muted aria-selected:text-text-primary',
        day_hidden: 'invisible',
        ...classNames,
      }}
      components={{
        IconLeft: ({ className: iconCls, ...iconProps }) => (
          <ChevronLeft className={cn('h-4 w-4', iconCls)} {...iconProps} />
        ),
        IconRight: ({ className: iconCls, ...iconProps }) => (
          <ChevronRight className={cn('h-4 w-4', iconCls)} {...iconProps} />
        ),
      }}
      {...props}
    />
  );
}
Calendar.displayName = 'Calendar';
