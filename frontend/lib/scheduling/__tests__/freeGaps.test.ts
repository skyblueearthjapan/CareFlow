/**
 * freeGaps.ts 単体テスト — Phase G-55.
 *
 * モバイル現場ボード (FieldBoard) と親機スケジュール (CourseDayTable) が共有する
 * 「空き時間帯」算出ロジックを検証する。営業枠 09:30–12:00 / 13:00–18:00、
 * 60 分未満の gap は省略、start 昇順、という不変条件を固定する。
 */
import { describe, it, expect } from 'vitest';

import { BUSINESS_BLOCKS, MIN_FREE_GAP_MIN, computeFreeGaps, fmtHM, parseHM } from '../freeGaps';

const v = (start: string | null | undefined, end: string | null | undefined) => ({
  start_time: start,
  end_time: end,
});

describe('freeGaps 定数', () => {
  it('営業枠は AM 09:30–12:00 / PM 13:00–18:00', () => {
    expect(BUSINESS_BLOCKS).toEqual([
      [9 * 60 + 30, 12 * 60],
      [13 * 60, 18 * 60],
    ]);
  });

  it('最小 gap 閾値は 60 分', () => {
    expect(MIN_FREE_GAP_MIN).toBe(60);
  });
});

describe('parseHM / fmtHM', () => {
  it('HH:MM を分に変換する', () => {
    expect(parseHM('09:30')).toBe(570);
    expect(parseHM('18:00')).toBe(1080);
  });

  it('HH:MM:SS も先頭の HH:MM を読む', () => {
    expect(parseHM('13:00:00')).toBe(780);
  });

  it('不正値 / null は null', () => {
    expect(parseHM(null)).toBeNull();
    expect(parseHM(undefined)).toBeNull();
    expect(parseHM('')).toBeNull();
    expect(parseHM('foo')).toBeNull();
    expect(parseHM('9')).toBeNull();
  });

  it('分を HH:MM に戻す', () => {
    expect(fmtHM(570)).toBe('09:30');
    expect(fmtHM(1080)).toBe('18:00');
    expect(fmtHM(780)).toBe('13:00');
  });
});

describe('computeFreeGaps', () => {
  it('訪問ゼロなら AM / PM の 2 帯 (09:30〜12:00 / 13:00〜18:00)', () => {
    const gaps = computeFreeGaps([]);
    expect(gaps.map((g) => g.label)).toEqual(['09:30〜12:00', '13:00〜18:00']);
  });

  it('AM の中盤 1 件を除いた前後 gap を返す (前後とも ≥60分)', () => {
    // 09:30〜10:30 占有 → AM 残り 10:30〜12:00 (90分) + PM 13:00〜18:00 (300分)。
    const gaps = computeFreeGaps([v('09:30', '10:30')]);
    expect(gaps.map((g) => g.label)).toEqual(['10:30〜12:00', '13:00〜18:00']);
  });

  it('60分未満の gap は省略する', () => {
    // 占有 09:30〜10:30 / 11:00〜12:00 → AM 残り 10:30〜11:00 (30分) は省略。
    // PM 13:00〜18:00 (300分) のみ残る。
    const gaps = computeFreeGaps([v('09:30', '10:30'), v('11:00', '12:00')]);
    expect(gaps.map((g) => g.label)).toEqual(['13:00〜18:00']);
  });

  it('開始時刻順に interleave する (訪問が PM 中盤のとき前後に gap)', () => {
    // 11:00〜12:00 占有 → AM 09:30〜11:00 (90分) + PM 13:00〜18:00 (300分)。start 昇順。
    const gaps = computeFreeGaps([v('11:00', '12:00')]);
    expect(gaps.map((g) => g.startMin)).toEqual([570, 780]);
    expect(gaps.map((g) => g.label)).toEqual(['09:30〜11:00', '13:00〜18:00']);
  });

  it('AM/PM を実時刻で埋め切ると gap ゼロ', () => {
    const gaps = computeFreeGaps([v('09:30', '12:00'), v('13:00', '18:00')]);
    expect(gaps).toEqual([]);
  });

  it('同住所2名ペア(同キー・同start)は90分占有に底上げ → 空きは占有後(17:30)から', () => {
    // 安永/菅原 16:00-16:35 同住所ペア → 占有16:00-17:30. PM 残り 13:00-16:00 (180分)。
    // 17:30-18:00 (30分) は <60 で省略。生の16:35で計算すると誤って16:35〜が出る。
    const gaps = computeFreeGaps([
      { start_time: '16:00', end_time: '16:35', same_address_key: 'addr-k1' },
      { start_time: '16:00', end_time: '16:35', same_address_key: 'addr-k1' },
    ]);
    expect(gaps.map((g) => g.label)).toEqual(['09:30〜12:00', '13:00〜16:00']);
  });

  it('同住所キーが1件のみ(ペアでない)は90分延長せず生の終了を使う', () => {
    const gaps = computeFreeGaps([
      { start_time: '16:00', end_time: '16:35', same_address_key: 'addr-k1' },
    ]);
    // 占有16:00-16:35のみ → PM 13:00-16:00 + 16:35-18:00 (85分≥60)。
    expect(gaps.map((g) => g.label)).toEqual(['09:30〜12:00', '13:00〜16:00', '16:35〜18:00']);
  });

  it('same_address_key 未指定 (旧呼び出し) は従来どおり生の [start,end)', () => {
    const gaps = computeFreeGaps([v('16:00', '16:35'), v('16:00', '16:35')]);
    expect(gaps.map((g) => g.label)).toEqual(['09:30〜12:00', '13:00〜16:00', '16:35〜18:00']);
  });

  it('end <= start の不正な占有は無視する', () => {
    const gaps = computeFreeGaps([v('10:00', '10:00'), v('12:00', '09:00')]);
    // 不正占有を無視 → 空コース扱いで AM / PM 2 帯。
    expect(gaps.map((g) => g.label)).toEqual(['09:30〜12:00', '13:00〜18:00']);
  });

  it('時刻文字列が解析不能な占有は無視する', () => {
    const gaps = computeFreeGaps([v(null, null), v('foo', 'bar')]);
    expect(gaps.map((g) => g.label)).toEqual(['09:30〜12:00', '13:00〜18:00']);
  });

  it('昼休み (12:00–13:00) は営業枠外なので gap に含まれない', () => {
    const gaps = computeFreeGaps([]);
    expect(gaps.some((g) => g.label.includes('12:00〜13:00'))).toBe(false);
  });
});
