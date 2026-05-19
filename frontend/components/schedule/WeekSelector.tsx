'use client';

/**
 * Week selector — prev / today / next buttons + a DatePicker for arbitrary
 * weeks. Works in ISO weekday convention where Monday = day 0.
 */
import { format } from 'date-fns';
import { ja } from 'date-fns/locale';
import { ChevronLeft, ChevronRight } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { DatePicker } from '@/components/ui/date-picker';

interface WeekSelectorProps {
  /** Monday of the selected week. */
  weekStart: Date;
  onChange: (date: Date) => void;
}

/** Snap any Date to the Monday of its ISO week (Mon=0..Sun=6). */
export function toWeekStart(d: Date): Date {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  // JS getDay(): Sun=0..Sat=6. Map Sun→6, Mon→0, …, Sat→5.
  const dow = (x.getDay() + 6) % 7;
  x.setDate(x.getDate() - dow);
  return x;
}

export function addDays(d: Date, days: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + days);
  return x;
}

export function WeekSelector({ weekStart, onChange }: WeekSelectorProps) {
  const weekEnd = addDays(weekStart, 6);
  const today = toWeekStart(new Date());

  const goPrev = () => onChange(addDays(weekStart, -7));
  const goNext = () => onChange(addDays(weekStart, 7));
  const goToday = () => onChange(today);

  const handlePick = (d: Date | undefined) => {
    if (!d) return;
    onChange(toWeekStart(d));
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button type="button" variant="outline" size="icon" onClick={goPrev} aria-label="前週">
        <ChevronLeft className="h-4 w-4" />
      </Button>
      <Button type="button" variant="outline" size="sm" onClick={goToday}>
        今週
      </Button>
      <Button type="button" variant="outline" size="icon" onClick={goNext} aria-label="次週">
        <ChevronRight className="h-4 w-4" />
      </Button>
      <div className="min-w-[220px]">
        <DatePicker value={weekStart} onChange={handlePick} formatStr="yyyy-MM-dd" />
      </div>
      <span className="tnum text-sm text-text-secondary">
        {format(weekStart, 'M/d', { locale: ja })} 〜 {format(weekEnd, 'M/d', { locale: ja })}
      </span>
    </div>
  );
}
