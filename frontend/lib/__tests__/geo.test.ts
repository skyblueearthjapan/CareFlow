import { describe, it, expect } from 'vitest';

import { haversineMeters } from '@/lib/geo';

describe('haversineMeters', () => {
  it('同一点は 0m', () => {
    expect(haversineMeters({ lat: 35.1, lng: 140.1 }, { lat: 35.1, lng: 140.1 })).toBe(0);
  });

  it('近距離 (~111m / 緯度 0.001度) を概算する', () => {
    const d = haversineMeters({ lat: 35.0, lng: 140.0 }, { lat: 35.001, lng: 140.0 });
    expect(d).toBeGreaterThan(105);
    expect(d).toBeLessThan(115);
  });

  it('遠距離 (>300m) を 300 超で返す', () => {
    const d = haversineMeters({ lat: 35.0, lng: 140.0 }, { lat: 35.01, lng: 140.0 });
    expect(d).toBeGreaterThan(300);
  });

  it('対称である (a→b == b→a)', () => {
    const a = { lat: 35.6812, lng: 139.7671 };
    const b = { lat: 34.6937, lng: 135.5023 };
    expect(haversineMeters(a, b)).toBeCloseTo(haversineMeters(b, a), 3);
  });
});
