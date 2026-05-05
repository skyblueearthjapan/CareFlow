'use client';

/**
 * Shared address + lat/lng auto-fill widget — CareFlow Wave 4-C.
 *
 * - Renders 3 inputs (住所 / 緯度 / 経度) plus a manual-refresh button.
 * - Watches the address value with `useGeocodeOnAddressChange()` and
 *   auto-fills lat/lng when the Google Geocoding relay responds.
 * - Skips the auto-write while the user is actively editing lat/lng
 *   (focus detection) so manual input is never clobbered mid-keystroke.
 * - Two integration modes:
 *     1. **Controlled** — pass `address`, `lat`, `lng`, and the
 *        corresponding `onChange*` callbacks. Used by the staff & office
 *        forms, which keep their own React state.
 *     2. **react-hook-form** — pass `formMethods` (a `UseFormReturn`)
 *        plus `addressFieldName`, `latFieldName`, `lngFieldName`. Used
 *        by `PatientForm` so registered field validation keeps working.
 */

import * as React from 'react';
import type { UseFormReturn } from 'react-hook-form';

import { Input } from '@/components/ui/input';
import { useGeocodeOnAddressChange } from '@/lib/queries/geocoding';

interface CommonProps {
  /** Wraps the row; defaults to a 2-column grid that matches the host forms. */
  className?: string;
  /** Surface label for the address field (default "住所"). */
  addressLabel?: string;
  /** Visual label for the latitude field (default "緯度 (lat)"). */
  latLabel?: string;
  /** Visual label for the longitude field (default "経度 (lng)"). */
  lngLabel?: string;
  /** Disable all inputs (e.g. while form submitting). */
  disabled?: boolean;
  /** Debounce in milliseconds (default 800). */
  debounceMs?: number;
}

interface ControlledProps extends CommonProps {
  mode?: 'controlled';
  address: string;
  lat: string;
  lng: string;
  onAddressChange: (next: string) => void;
  onLatChange: (next: string) => void;
  onLngChange: (next: string) => void;
  // RHF props absent in this mode.
  formMethods?: never;
  addressFieldName?: never;
  latFieldName?: never;
  lngFieldName?: never;
}

interface FormProps<TFieldValues extends Record<string, unknown>>
  extends CommonProps {
  mode: 'rhf';
  formMethods: UseFormReturn<TFieldValues>;
  addressFieldName: keyof TFieldValues & string;
  latFieldName: keyof TFieldValues & string;
  lngFieldName: keyof TFieldValues & string;
  // Controlled props absent in this mode.
  address?: never;
  lat?: never;
  lng?: never;
  onAddressChange?: never;
  onLatChange?: never;
  onLngChange?: never;
}

export type AddressGeocodeFieldProps<
  TFieldValues extends Record<string, unknown> = Record<string, unknown>,
> = ControlledProps | FormProps<TFieldValues>;

export function AddressGeocodeField<
  TFieldValues extends Record<string, unknown> = Record<string, unknown>,
>(props: AddressGeocodeFieldProps<TFieldValues>) {
  const {
    className,
    addressLabel = '住所',
    latLabel = '緯度 (lat)',
    lngLabel = '経度 (lng)',
    disabled,
    debounceMs = 800,
  } = props;

  // --- Mode bridging ----------------------------------------------------
  const isRhf = props.mode === 'rhf';
  const formMethods = isRhf ? props.formMethods : null;
  const addressFieldName = isRhf ? props.addressFieldName : null;
  const latFieldName = isRhf ? props.latFieldName : null;
  const lngFieldName = isRhf ? props.lngFieldName : null;

  const watchedAddress = isRhf
    ? (formMethods!.watch(addressFieldName as never) as unknown as string)
    : (props as ControlledProps).address;

  const watchedLat = isRhf
    ? (formMethods!.watch(latFieldName as never) as unknown as string)
    : (props as ControlledProps).lat;

  const watchedLng = isRhf
    ? (formMethods!.watch(lngFieldName as never) as unknown as string)
    : (props as ControlledProps).lng;

  // --- Focus detection — never overwrite a field the user is editing -----
  const latRef = React.useRef<HTMLInputElement | null>(null);
  const lngRef = React.useRef<HTMLInputElement | null>(null);
  const [latFocused, setLatFocused] = React.useState(false);
  const [lngFocused, setLngFocused] = React.useState(false);

  const setLatValue = React.useCallback(
    (value: string) => {
      if (isRhf) {
        formMethods!.setValue(
          latFieldName as never,
          value as never,
          { shouldValidate: true, shouldDirty: true },
        );
      } else {
        (props as ControlledProps).onLatChange(value);
      }
    },
    [isRhf, formMethods, latFieldName, props],
  );

  const setLngValue = React.useCallback(
    (value: string) => {
      if (isRhf) {
        formMethods!.setValue(
          lngFieldName as never,
          value as never,
          { shouldValidate: true, shouldDirty: true },
        );
      } else {
        (props as ControlledProps).onLngChange(value);
      }
    },
    [isRhf, formMethods, lngFieldName, props],
  );

  const setAddressValue = React.useCallback(
    (value: string) => {
      if (isRhf) {
        formMethods!.setValue(
          addressFieldName as never,
          value as never,
          { shouldValidate: true, shouldDirty: true },
        );
      } else {
        (props as ControlledProps).onAddressChange(value);
      }
    },
    [isRhf, formMethods, addressFieldName, props],
  );

  // --- Geocode hook -----------------------------------------------------
  const {
    data: geo,
    error: geoError,
    isLoading: geoLoading,
    refresh,
  } = useGeocodeOnAddressChange(watchedAddress ?? '', debounceMs, {
    enabled: !disabled,
  });

  // Apply geocode results to lat/lng — only when:
  //   - We have a fresh result
  //   - The user is NOT actively editing the corresponding field
  // This mirrors the careflow-scheduler `forceRows` pattern: the address
  // change ALWAYS triggers a refetch, but the user can override either
  // value manually without it being clobbered.
  const lastAppliedHashRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!geo) return;
    if (geo.address_hash === lastAppliedHashRef.current) return;
    lastAppliedHashRef.current = geo.address_hash;

    if (!latFocused) {
      setLatValue(String(geo.lat));
    }
    if (!lngFocused) {
      setLngValue(String(geo.lng));
    }
    // Intentionally narrow deps; setLat/setLng are stable enough.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geo]);

  const handleManualRefresh = () => {
    refresh();
  };

  // Friendly error text — keep saving the address even if geocoding fails.
  const errorText = geoError
    ? geoError.message.includes('404')
      ? '住所が見つかりませんでした (緯度経度は手動入力してください)'
      : geoError.message.includes('503')
        ? 'Geocoding 利用上限に達しました。少し時間を置いて再試行してください。'
        : `Geocoding に失敗しました: ${geoError.message}`
    : null;

  return (
    <div className={className ?? 'grid grid-cols-1 gap-4 md:grid-cols-2'}>
      <label className="flex flex-col gap-1 text-sm md:col-span-2">
        <span className="font-medium text-text-secondary">{addressLabel}</span>
        <Input
          value={watchedAddress ?? ''}
          onChange={(e) => setAddressValue(e.target.value)}
          placeholder="例: 東京都新宿区西新宿2-8-1"
          disabled={disabled}
        />
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-text-secondary">{latLabel}</span>
        <Input
          ref={latRef}
          type="number"
          step="any"
          value={watchedLat ?? ''}
          onChange={(e) => setLatValue(e.target.value)}
          onFocus={() => setLatFocused(true)}
          onBlur={() => setLatFocused(false)}
          disabled={disabled}
        />
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-text-secondary">{lngLabel}</span>
        <Input
          ref={lngRef}
          type="number"
          step="any"
          value={watchedLng ?? ''}
          onChange={(e) => setLngValue(e.target.value)}
          onFocus={() => setLngFocused(true)}
          onBlur={() => setLngFocused(false)}
          disabled={disabled}
        />
      </label>

      <div className="flex items-center gap-2 text-xs md:col-span-2">
        <button
          type="button"
          onClick={handleManualRefresh}
          disabled={disabled || geoLoading || !(watchedAddress?.trim().length)}
          className="rounded-md border border-border-default bg-bg-base px-3 py-1.5 text-xs text-text-primary hover:bg-bg-muted disabled:cursor-not-allowed disabled:opacity-50"
        >
          {geoLoading ? '取得中…' : '住所から緯度経度を再取得'}
        </button>
        {geo && !geoError && (
          <span className="text-text-muted">
            {geo.cached ? 'キャッシュから取得' : 'Google Maps から取得'}
            {geo.formatted_address ? ` / ${geo.formatted_address}` : ''}
          </span>
        )}
        {errorText && <span className="text-error">{errorText}</span>}
      </div>
    </div>
  );
}
