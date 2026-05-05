'use client';

import * as React from 'react';
import { Command as CommandPrimitive } from 'cmdk';
import { Check, ChevronsUpDown, Search, X } from 'lucide-react';

import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Badge } from '@/components/ui/badge';

export interface ComboboxOption {
  label: string;
  value: string;
  disabled?: boolean;
}

interface ComboboxBaseProps {
  options: ComboboxOption[];
  placeholder?: string;
  searchPlaceholder?: string;
  emptyText?: string;
  disabled?: boolean;
  className?: string;
}

interface ComboboxSingleProps extends ComboboxBaseProps {
  multiple?: false;
  value?: string;
  onChange?: (value: string) => void;
}

interface ComboboxMultipleProps extends ComboboxBaseProps {
  multiple: true;
  value?: string[];
  onChange?: (value: string[]) => void;
}

export type ComboboxProps = ComboboxSingleProps | ComboboxMultipleProps;

const cmdGroupCls =
  'overflow-hidden p-1 text-text-primary [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-text-muted';

const cmdItemCls =
  'relative flex cursor-default select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none data-[selected=true]:bg-bg-muted data-[selected=true]:text-text-primary data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50';

export function Combobox(props: ComboboxProps) {
  const {
    options,
    placeholder = '選択してください',
    searchPlaceholder = '検索...',
    emptyText = '該当なし',
    disabled,
    className,
  } = props;
  const [open, setOpen] = React.useState(false);

  if (props.multiple) {
    const selected = props.value ?? [];
    const labelMap = React.useMemo(
      () => new Map(options.map((o) => [o.value, o.label])),
      [options],
    );

    const toggle = (value: string) => {
      const next = selected.includes(value)
        ? selected.filter((v) => v !== value)
        : [...selected, value];
      props.onChange?.(next);
    };

    const remove = (value: string) => {
      props.onChange?.(selected.filter((v) => v !== value));
    };

    return (
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="outline"
            role="combobox"
            aria-expanded={open}
            disabled={disabled}
            className={cn(
              'w-full justify-between min-h-10 h-auto py-1.5',
              !selected.length && 'text-text-muted',
              className,
            )}
          >
            <span className="flex flex-wrap gap-1">
              {selected.length === 0 ? (
                placeholder
              ) : (
                selected.map((v) => (
                  <Badge
                    key={v}
                    variant="secondary"
                    className="gap-1"
                    onClick={(e) => {
                      e.stopPropagation();
                      remove(v);
                    }}
                  >
                    {labelMap.get(v) ?? v}
                    <X className="h-3 w-3" />
                  </Badge>
                ))
              )}
            </span>
            <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-[--radix-popover-trigger-width] p-0">
          <CommandPrimitive className="flex h-full w-full flex-col overflow-hidden rounded-md bg-bg-base text-text-primary">
            <div className="flex items-center border-b border-border-default px-3">
              <Search className="mr-2 h-4 w-4 shrink-0 opacity-50" />
              <CommandPrimitive.Input
                placeholder={searchPlaceholder}
                className="flex h-10 w-full rounded-md bg-transparent py-3 text-sm outline-none placeholder:text-text-muted disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>
            <CommandPrimitive.List className="max-h-[300px] overflow-y-auto overflow-x-hidden">
              <CommandPrimitive.Empty className="py-6 text-center text-sm text-text-muted">
                {emptyText}
              </CommandPrimitive.Empty>
              <CommandPrimitive.Group className={cmdGroupCls}>
                {options.map((opt) => {
                  const isSelected = selected.includes(opt.value);
                  return (
                    <CommandPrimitive.Item
                      key={opt.value}
                      value={opt.label}
                      disabled={opt.disabled}
                      onSelect={() => toggle(opt.value)}
                      className={cmdItemCls}
                    >
                      <div
                        className={cn(
                          'flex h-4 w-4 items-center justify-center rounded-sm border border-border-default',
                          isSelected
                            ? 'bg-brand-primary text-white border-brand-primary'
                            : 'opacity-50',
                        )}
                      >
                        {isSelected ? <Check className="h-3 w-3" /> : null}
                      </div>
                      <span>{opt.label}</span>
                    </CommandPrimitive.Item>
                  );
                })}
              </CommandPrimitive.Group>
            </CommandPrimitive.List>
          </CommandPrimitive>
        </PopoverContent>
      </Popover>
    );
  }

  // single-select
  const value = props.value;
  const selectedLabel = options.find((o) => o.value === value)?.label;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className={cn(
            'w-full justify-between',
            !value && 'text-text-muted',
            className,
          )}
        >
          {selectedLabel ?? placeholder}
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] p-0">
        <CommandPrimitive className="flex h-full w-full flex-col overflow-hidden rounded-md bg-bg-base text-text-primary">
          <div className="flex items-center border-b border-border-default px-3">
            <Search className="mr-2 h-4 w-4 shrink-0 opacity-50" />
            <CommandPrimitive.Input
              placeholder={searchPlaceholder}
              className="flex h-10 w-full rounded-md bg-transparent py-3 text-sm outline-none placeholder:text-text-muted disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>
          <CommandPrimitive.List className="max-h-[300px] overflow-y-auto overflow-x-hidden">
            <CommandPrimitive.Empty className="py-6 text-center text-sm text-text-muted">
              {emptyText}
            </CommandPrimitive.Empty>
            <CommandPrimitive.Group className={cmdGroupCls}>
              {options.map((opt) => (
                <CommandPrimitive.Item
                  key={opt.value}
                  value={opt.label}
                  disabled={opt.disabled}
                  onSelect={() => {
                    props.onChange?.(opt.value);
                    setOpen(false);
                  }}
                  className={cmdItemCls}
                >
                  <Check
                    className={cn(
                      'mr-2 h-4 w-4',
                      value === opt.value ? 'opacity-100' : 'opacity-0',
                    )}
                  />
                  {opt.label}
                </CommandPrimitive.Item>
              ))}
            </CommandPrimitive.Group>
          </CommandPrimitive.List>
        </CommandPrimitive>
      </PopoverContent>
    </Popover>
  );
}
Combobox.displayName = 'Combobox';
