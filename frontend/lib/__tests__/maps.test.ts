import { describe, expect, it } from 'vitest';

import {
  MAX_ROUTE_STOPS,
  googleMapsDayRouteUrl,
  googleMapsDirectionsUrl,
  googleMapsPlaceUrl,
  googleMapsRouteUrl,
  isSameBuilding,
} from '../maps';

const ADDR = '千葉県千葉市稲毛区小仲台1-2-3 ○○マンション101';

describe('Google マップのリンク', () => {
  it('道案内は住所の文字を行き先に入れ、日本語で開く', () => {
    const url = googleMapsDirectionsUrl(ADDR)!;
    expect(url.startsWith('https://www.google.com/maps/dir/?api=1&destination=')).toBe(true);
    expect(new URL(url).searchParams.get('destination')).toBe(ADDR);
    expect(new URL(url).searchParams.get('hl')).toBe('ja');
  });

  it('場所は住所の文字で検索する', () => {
    expect(new URL(googleMapsPlaceUrl(ADDR)!).searchParams.get('query')).toBe(ADDR);
  });

  it('経路は出発地と目的地の両方に住所を入れる', () => {
    const u = new URL(googleMapsRouteUrl('千葉市中央区都町1-1', ADDR)!);
    expect(u.pathname).toBe('/maps/dir/');
    expect(u.searchParams.get('origin')).toBe('千葉市中央区都町1-1');
    expect(u.searchParams.get('destination')).toBe(ADDR);
  });

  it('住所が空・空白・null なら null (ボタンを出さない)', () => {
    expect(googleMapsDirectionsUrl(null)).toBeNull();
    expect(googleMapsPlaceUrl('   ')).toBeNull();
    expect(googleMapsRouteUrl(ADDR, '')).toBeNull();
    expect(googleMapsRouteUrl(undefined, ADDR)).toBeNull();
  });

  it('1 日の経路: 出発地・経由地 (| 区切り)・目的地', () => {
    const r = googleMapsDayRouteUrl(['A', 'B', 'C', 'D'])!;
    const u = new URL(r.url);
    expect(u.searchParams.get('origin')).toBe('A');
    expect(u.searchParams.get('waypoints')).toBe('B|C');
    expect(u.searchParams.get('destination')).toBe('D');
    expect(r).toMatchObject({ stops: 4, truncated: false });
  });

  it('1 日の経路: 2 か所なら経由地なし・1 か所なら null', () => {
    expect(
      new URL(googleMapsDayRouteUrl(['A', null, 'B'])!.url).searchParams.has('waypoints'),
    ).toBe(false);
    expect(googleMapsDayRouteUrl(['A', 'A', ' '])).toBeNull();
  });

  it('1 日の経路: 上限を超えたら先頭から上限まで', () => {
    const many = Array.from({ length: 14 }, (_, i) => `住所${i}`);
    const r = googleMapsDayRouteUrl(many)!;
    expect(r).toMatchObject({ stops: MAX_ROUTE_STOPS, truncated: true });
    expect(new URL(r.url).searchParams.get('destination')).toBe(`住所${MAX_ROUTE_STOPS - 1}`);
  });

  it('移動手段は車・住所の中の | は経由地を割らない', () => {
    expect(new URL(googleMapsDirectionsUrl('A町1')!).searchParams.get('travelmode')).toBe(
      'driving',
    );
    const r = googleMapsDayRouteUrl(['A', 'B|棟', 'C'])!;
    expect(new URL(r.url).searchParams.get('waypoints')).toBe('B 棟');
  });

  // 本番 10/17: 同じ建物どうしで「運転ルートを計算できませんでした」。
  describe('同じ建物の判定', () => {
    const GH = '千葉県千葉市稲毛区宮野木町818-2障がい者グループホームよりより';
    it('建物名・郵便番号・全角・都道府県の有無が違っても同じ建物', () => {
      expect(isSameBuilding('千葉県千葉市稲毛区宮野木町818-2', GH)).toBe(true);
      expect(isSameBuilding('〒263-0054 千葉県千葉市稲毛区宮野木町８１８−２', GH)).toBe(true);
      expect(isSameBuilding('千葉市稲毛区宮野木町818-2', GH)).toBe(true);
      expect(isSameBuilding('千葉市若葉区都賀1-1-1', '千葉県千葉市若葉区都賀1-1-1-508')).toBe(true);
    });
    it('番地が違えば別の建物', () => {
      expect(isSameBuilding('千葉市稲毛区宮野木町818-2', '千葉市稲毛区宮野木町818-3')).toBe(false);
      expect(isSameBuilding('千葉市稲毛区宮野木町818', '千葉市稲毛区宮野木町8181')).toBe(false);
      expect(isSameBuilding('千葉市稲毛区宮野木町969', '千葉市稲毛区宮野木台969')).toBe(false);
      expect(
        isSameBuilding(
          '千葉県千葉市美浜区幸町2丁目11-5-505',
          '千葉市美浜区幸町2-12-1しょうじゅレジデンス427',
        ),
      ).toBe(false);
      expect(isSameBuilding('千葉市美浜区幸町2丁目11番5号', '千葉市美浜区幸町2-11-5-505')).toBe(
        true,
      );
    });
    it('同じ建物どうしの経路は出さない・1 日の経路では 1 か所にまとめる', () => {
      expect(googleMapsRouteUrl('千葉市稲毛区宮野木町818-2', GH)).toBeNull();
      const r = googleMapsDayRouteUrl(['A町1', '千葉市稲毛区宮野木町818-2', GH, 'C町3'])!;
      const u = new URL(r.url);
      expect(u.searchParams.get('waypoints')).toBe('千葉市稲毛区宮野木町818-2');
      expect(r.stops).toBe(3);
    });
  });
});
