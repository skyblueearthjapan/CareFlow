'use client';

import { useMemo, useState } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useCities } from '@/lib/queries/cities';
import type { Office, OfficeCreate } from '@/lib/schemas/office';
import { OfficeCreateSchema } from '@/lib/schemas/office';

interface OfficeFormProps {
  initial?: Partial<Office>;
  onSubmit: (values: OfficeCreate) => Promise<void> | void;
  submitting?: boolean;
  error?: unknown;
  submitLabel?: string;
}

export function OfficeForm({
  initial,
  onSubmit,
  submitting,
  error,
  submitLabel = '作成',
}: OfficeFormProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const [code, setCode] = useState(initial?.code ?? '');
  const [address, setAddress] = useState(initial?.address ?? '');
  const [lat, setLat] = useState<string>(initial?.lat?.toString() ?? '');
  const [lng, setLng] = useState<string>(initial?.lng?.toString() ?? '');
  const [prefecture, setPrefecture] = useState(initial?.prefecture ?? '');
  const [note, setNote] = useState(initial?.note ?? '');
  const [allowed, setAllowed] = useState<string[]>(initial?.allowed_cities ?? []);
  const [cityFilter, setCityFilter] = useState('');
  const [validationMsg, setValidationMsg] = useState<string | null>(null);

  const { cities, isLoading: citiesLoading } = useCities({
    search: cityFilter || undefined,
    prefecture: prefecture || undefined,
  });

  const prefectures = useMemo(() => {
    const set = new Set<string>();
    for (const c of cities) set.add(c.prefecture);
    return Array.from(set).sort();
  }, [cities]);

  const toggleCity = (cityId: string) => {
    setAllowed((prev) =>
      prev.includes(cityId) ? prev.filter((x) => x !== cityId) : [...prev, cityId],
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setValidationMsg(null);

    const payload: OfficeCreate = {
      name,
      code: code || undefined,
      address: address || null,
      lat: lat === '' ? null : Number(lat),
      lng: lng === '' ? null : Number(lng),
      prefecture: prefecture || null,
      note: note || null,
      allowed_cities: allowed,
    };

    const parsed = OfficeCreateSchema.safeParse(payload);
    if (!parsed.success) {
      setValidationMsg(parsed.error.issues.map((i) => i.message).join(' / '));
      return;
    }
    await onSubmit(parsed.data);
  };

  return (
    <form className="space-y-4" onSubmit={handleSubmit}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="拠点名 *">
          <Input value={name} onChange={(e) => setName(e.target.value)} required />
        </Field>
        <Field label="拠点コード">
          <Input value={code} onChange={(e) => setCode(e.target.value)} />
        </Field>
        <Field label="都道府県">
          <Input
            list="office-prefectures"
            value={prefecture}
            onChange={(e) => setPrefecture(e.target.value)}
            placeholder="例: 東京都"
          />
          <datalist id="office-prefectures">
            {prefectures.map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
        </Field>
        <Field label="住所">
          <Input value={address} onChange={(e) => setAddress(e.target.value)} />
        </Field>
        <Field label="緯度 (lat)">
          <Input
            type="number"
            step="0.0000001"
            value={lat}
            onChange={(e) => setLat(e.target.value)}
          />
        </Field>
        <Field label="経度 (lng)">
          <Input
            type="number"
            step="0.0000001"
            value={lng}
            onChange={(e) => setLng(e.target.value)}
          />
        </Field>
      </div>

      <Field label="メモ">
        <textarea
          className="flex min-h-[72px] w-full rounded-md border border-border-default bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus-visible:outline-none focus-visible:border-brand-primary focus-visible:ring-2 focus-visible:ring-brand-primary-light"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </Field>

      <Field label="担当エリア (cities)">
        <Input
          placeholder="市区町村を検索"
          value={cityFilter}
          onChange={(e) => setCityFilter(e.target.value)}
        />
        <div className="mt-2 max-h-56 overflow-y-auto rounded-md border border-border-default p-2">
          {citiesLoading ? (
            <p className="text-xs text-text-muted">読み込み中...</p>
          ) : cities.length === 0 ? (
            <p className="text-xs text-text-muted">該当する市区町村がありません</p>
          ) : (
            <ul className="space-y-1">
              {cities.slice(0, 200).map((city) => {
                const checked = allowed.includes(city.id);
                return (
                  <li key={city.id}>
                    <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-bg-muted">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleCity(city.id)}
                      />
                      <span>
                        {city.prefecture} / {city.name}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <p className="mt-1 text-xs text-text-muted">選択中: {allowed.length} 件</p>
      </Field>

      {validationMsg && (
        <Alert variant="destructive">
          <AlertTitle>入力エラー</AlertTitle>
          <AlertDescription>{validationMsg}</AlertDescription>
        </Alert>
      )}

      {error instanceof Error && (
        <Alert variant="destructive">
          <AlertTitle>送信に失敗しました</AlertTitle>
          <AlertDescription>{error.message}</AlertDescription>
        </Alert>
      )}

      <div className="flex justify-end gap-2">
        <Button type="submit" disabled={submitting}>
          {submitting ? '送信中...' : submitLabel}
        </Button>
      </div>
    </form>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-text-secondary">{label}</span>
      {children}
    </label>
  );
}
