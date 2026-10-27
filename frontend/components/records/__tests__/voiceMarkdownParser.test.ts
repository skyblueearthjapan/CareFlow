/**
 * 音声記録 v2 の本文（Markdown）の読み取りと、要約の 1 行目・facts の読み取りの vitest。
 */
import { describe, it, expect } from 'vitest';

import {
  aroundLabel,
  markdownFirstLine,
  parseInline,
  parseTimeLabel,
  parseVoiceMarkdown,
  seekStartSec,
} from '../voiceMarkdownParser';
import { summaryDisplayMode, summaryFirstLine, summaryToText, v2Facts } from '../recordFormat';
import type { VisitRecordingRead } from '@/lib/queries/visit-recordings';

describe('parseVoiceMarkdown', () => {
  it('見出し・段落・箇条書き（入れ子 1 段）・番号・表を分ける', () => {
    const blocks = parseVoiceMarkdown(
      [
        '# 訪問記録',
        '状態は安定 [0:45]',
        '',
        '- 血圧 132/78 [3:05]',
        '  - 座位で測定',
        '・食事は半分',
        '1. 足浴',
        '2) 服薬確認',
        '',
        '| 項目 | 値 |',
        '|---|:-:|',
        '| 体温 | 36.5 |',
        '---',
      ].join('\n'),
    );
    expect(blocks.map((b) => b.kind)).toEqual(['heading', 'para', 'bullets', 'numbers', 'table']);
    const bullets = blocks[2];
    expect(bullets?.kind === 'bullets' && bullets.items.length).toBe(2);
    expect(bullets?.kind === 'bullets' && bullets.items[0]?.children.length).toBe(1);
    const numbers = blocks[3];
    expect(numbers?.kind === 'numbers' && numbers.items.map((i) => i.num)).toEqual([1, 2]);
    const table = blocks[4];
    expect(table?.kind === 'table' && table.rows.length).toBe(1);
    expect(table?.kind === 'table' && table.head.length).toBe(2);
  });

  it('太字と時刻を分け、閉じていない ** は文字のまま残す', () => {
    expect(parseInline('**むくみ** あり [12:05]')).toEqual([
      { kind: 'text', text: 'むくみ', strong: true },
      { kind: 'text', text: ' あり ', strong: false },
      { kind: 'time', sec: 725, strong: false },
    ]);
    expect(parseInline('a **b')).toEqual([
      { kind: 'text', text: 'a ', strong: false },
      { kind: 'text', text: '**b', strong: false },
    ]);
    // 1 時間以上は [h:mm:ss]
    expect(parseInline('[1:02:05]')).toEqual([{ kind: 'time', sec: 3725, strong: false }]);
  });

  it('タグやリンクは読み取りでも文字のまま（HTML を作らない）', () => {
    const blocks = parseVoiceMarkdown('<script>alert(1)</script> [x](javascript:alert(1))');
    expect(blocks).toEqual([
      {
        kind: 'para',
        inlines: [
          {
            kind: 'text',
            text: '<script>alert(1)</script> [x](javascript:alert(1))',
            strong: false,
          },
        ],
      },
    ]);
  });

  it('時刻: 「ごろ」の文字・15 秒前から（0 未満は 0）', () => {
    expect(parseTimeLabel('3:05')).toBe(185);
    expect(parseTimeLabel('[1:00:00]')).toBe(3600);
    expect(parseTimeLabel('3:75')).toBeNull();
    expect(parseTimeLabel('')).toBeNull();
    expect(aroundLabel(725)).toBe('12:05 ごろ');
    expect(aroundLabel(3725)).toBe('1:02:05 ごろ');
    expect(seekStartSec(725)).toBe(710);
    expect(seekStartSec(10)).toBe(0);
  });

  it('最初の意味のある行: 見出しより本文を先に・記号と時刻を除く', () => {
    expect(markdownFirstLine('# 訪問記録\n\n- **血圧** 132/78 [3:05]\n本文')).toBe('血圧 132/78');
    expect(markdownFirstLine('## 見出しだけ')).toBe('見出しだけ');
    expect(markdownFirstLine('')).toBe('');
  });
});

const V2_SUMMARY = {
  format: 'v2',
  body: '# 訪問記録\n\n状態は安定しています [0:45]',
  facts: {
    vitals: { 血圧: '132/78', 体温: '', 脈拍: '', SpO2: '97', 血糖: '' },
    handover: ['足のむくみを見てください'],
    next: '金曜 10 時',
    needs_check: [
      { text: '薬の量', time: '5:10' },
      { text: '時刻なし', time: '' },
    ],
  },
};

function rec(over: Partial<VisitRecordingRead> = {}): VisitRecordingRead {
  return {
    id: 'r',
    recorded_at: '2026-10-27T01:00:00Z',
    status: 'summarized',
    summary: V2_SUMMARY,
    summary_text: V2_SUMMARY.body,
    ...over,
  } as VisitRecordingRead;
}

describe('recordFormat の v2', () => {
  it('facts: 語られたバイタルだけ・要確認は時刻を秒に', () => {
    expect(v2Facts(V2_SUMMARY)).toEqual({
      vitals: [
        ['血圧', '132/78'],
        ['SpO2', '97'],
      ],
      needsCheck: [
        { text: '薬の量', sec: 310 },
        { text: '時刻なし', sec: null },
      ],
    });
    expect(v2Facts({ 主訴: ['v1'] })).toEqual({ vitals: [], needsCheck: [] });
  });

  it('一覧の 1 行目: v2 は記号を除いた最初の意味のある行・v1 は今のまま', () => {
    expect(summaryFirstLine(rec())).toBe('状態は安定しています');
    expect(
      summaryFirstLine(rec({ summary: { 主訴: [] }, summary_text: '【主訴】\n・足が重い' })),
    ).toBe('【主訴】');
  });

  it('一覧の 1 行目: 人が本文を空にした v2 は AI の本文へ戻さない', () => {
    const edited = rec({ summary_text: '', summary_edited_at: '2026-10-27T02:00:00Z' });
    // 状態に応じた代替文（空の 1 行目）になり、AI の本文の 1 行目は出ない。
    expect(summaryFirstLine(edited)).not.toBe('状態は安定しています');
  });

  it('描き方: v2 / 人手で直した v2 / 空にした v2', () => {
    expect(summaryDisplayMode(rec())).toBe('v2');
    expect(summaryDisplayMode(rec({ summary_edited_at: '2026-10-27T02:00:00Z' }))).toBe(
      'v2-edited',
    );
    expect(
      summaryDisplayMode(rec({ summary_edited_at: '2026-10-27T02:00:00Z', summary_text: '' })),
    ).toBe('empty');
  });

  it('編集の初期値: v2 は本文（JSON を平たくしない）', () => {
    expect(summaryToText(rec({ summary_text: null }))).toBe(V2_SUMMARY.body);
  });
});
