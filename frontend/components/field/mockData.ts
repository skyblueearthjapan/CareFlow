/**
 * CareFlow Mobile — 現場ボード モックデータ (Phase 1)
 *
 * `mocks/design_bundle/carelink/project/careflow-data.js` を TypeScript へ移植。
 * Warm & Human パレットはコンポーネント側で適用するため、ここは純データのみ。
 *
 * ⚠️ Phase 1 はフロント完結・ダミーデータ。バックエンドには接続していない。
 */

// ── 型定義 ──────────────────────────────────────────────────────────────────

export type InsuranceKind = 'med' | 'care';
export type OfficeKey = 'INAGE' | 'TSUGA';
export type Dow = '月' | '火' | '水' | '木' | '金' | '土' | '日';

export interface Patient {
  code: string;
  name: string;
  kana: string;
  sex: string;
  age: number;
  addr: string;
  area: string;
  ins: InsuranceKind;
  svc: number;
  type: string;
  fixed: string;
  /** 希望曜日 (1 = 希望) */
  dow: Record<string, 0 | 1>;
  note: string;
}

/** スロットは患者キー (CF_PATIENTS のキー) か null (空き枠) */
export type SlotKey = keyof typeof CF_PATIENTS | null;

export interface Course {
  id: string;
  /** コース色キー (A〜M) */
  c: CourseColorKey;
  staff: string;
  slots: (string | null)[];
  /** スロット index に対応する時刻 (任意) */
  times?: (string | null)[];
  /** 同住所ペア: [先頭 slotIdx, 連結 slotIdx][] */
  pairs?: [number, number][];
}

export type CourseColorKey = 'A' | 'B' | 'C' | 'D' | 'E' | 'M';

export interface PendingPlacement {
  courseIdx: number;
  slotIdx: number;
  /** CF_PATIENTS のキー */
  patient: string;
  who: string;
}

// ── 患者マスタ ──────────────────────────────────────────────────────────────

export const CF_PATIENTS: Record<string, Patient> = {
  aoyagi: {
    code: 'P-1042',
    name: '青柳 あい',
    kana: 'アオヤギ アイ',
    sex: '女',
    age: 78,
    addr: '千葉市稲毛区小仲台6-2-1',
    area: '稲毛区',
    ins: 'med',
    svc: 60,
    type: '予防',
    fixed: '稲A・月/金 午前',
    dow: { 月: 1, 火: 1, 水: 0, 木: 0, 金: 1, 土: 0 },
    note: '玄関の鍵は植木鉢の下。猫に注意。',
  },
  iizuka: {
    code: 'P-1088',
    name: '飯塚 太輝',
    kana: 'イイヅカ タイキ',
    sex: '男',
    age: 79,
    addr: '千葉市稲毛区轟町2-1-5',
    area: '稲毛区',
    ins: 'med',
    svc: 60,
    type: '医療',
    fixed: '稲A・月/水/金',
    dow: { 月: 1, 火: 0, 水: 1, 木: 0, 金: 1, 土: 0 },
    note: 'ご夫婦で訪問看護。奥様（本田花子様）と同住所・同時刻に連続訪問。',
  },
  hisamatsu: {
    code: 'P-1120',
    name: '久松 由伽里',
    kana: 'ヒサマツ ユカリ',
    sex: '女',
    age: 82,
    addr: '千葉市若葉区都賀3-5-8',
    area: '若葉区',
    ins: 'care',
    svc: 60,
    type: '予防',
    fixed: '稲C・火/木',
    dow: { 月: 0, 火: 1, 水: 0, 木: 1, 金: 0, 土: 1 },
    note: '耳が遠いのでゆっくり大きな声で。',
  },
  ebisawa: {
    code: 'P-1156',
    name: '海老澤 均尚',
    kana: 'エビサワ キンナオ',
    sex: '男',
    age: 71,
    addr: '千葉市稲毛区轟町2-1',
    area: '稲毛区',
    ins: 'med',
    svc: 30,
    type: '医療',
    fixed: '稲A・月 午前',
    dow: { 月: 1, 火: 0, 水: 1, 木: 0, 金: 1, 土: 0 },
    note: '褥瘡処置。毎回写真記録。',
  },
  takao: {
    code: 'P-1190',
    name: '高尾 美咲',
    kana: 'タカオ ミサキ',
    sex: '女',
    age: 69,
    addr: '千葉市花見川区検見川町5-3',
    area: '花見川区',
    ins: 'care',
    svc: 60,
    type: '予防',
    fixed: '稲D・月',
    dow: { 月: 1, 火: 1, 水: 0, 木: 0, 金: 0, 土: 0 },
    note: 'リハビリ重視。歩行訓練。',
  },
  okada: {
    code: 'P-1203',
    name: '岡田 誠一',
    kana: 'オカダ セイイチ',
    sex: '男',
    age: 75,
    addr: '千葉市稲毛区園生町8-4',
    area: '稲毛区',
    ins: 'med',
    svc: 60,
    type: '医療',
    fixed: '稲A・月',
    dow: { 月: 1, 火: 0, 水: 0, 木: 1, 金: 0, 土: 0 },
    note: '血圧高め。服薬確認。',
  },
  sano: {
    code: 'P-1222',
    name: '佐野 ふみ',
    kana: 'サノ フミ',
    sex: '女',
    age: 88,
    addr: '千葉市稲毛区轟町4-2',
    area: '稲毛区',
    ins: 'care',
    svc: 60,
    type: '予防',
    fixed: '稲A・月/水',
    dow: { 月: 1, 火: 0, 水: 1, 木: 0, 金: 0, 土: 0 },
    note: '認知症あり。声かけ丁寧に。',
  },
  mori: {
    code: 'P-1240',
    name: '森 健太郎',
    kana: 'モリ ケンタロウ',
    sex: '男',
    age: 60,
    addr: '千葉市稲毛区小仲台7-1',
    area: '稲毛区',
    ins: 'care',
    svc: 30,
    type: '予防',
    fixed: '稲B・月',
    dow: { 月: 1, 火: 0, 水: 0, 木: 0, 金: 1, 土: 0 },
    note: '仕事前9時までを希望。',
  },
  ueno: {
    code: 'P-1255',
    name: '上野 さくら',
    kana: 'ウエノ サクラ',
    sex: '女',
    age: 73,
    addr: '千葉市稲毛区作草部1-9',
    area: '稲毛区',
    ins: 'med',
    svc: 60,
    type: '医療',
    fixed: '稲B・月',
    dow: { 月: 1, 火: 0, 水: 1, 木: 0, 金: 0, 土: 0 },
    note: '点滴管理。清潔操作徹底。',
  },
  kira: {
    code: 'P-1301',
    name: '吉良 のぞみ',
    kana: 'キラ ノゾミ',
    sex: '女',
    age: 67,
    addr: '千葉市稲毛区天台2-3-7',
    area: '稲毛区',
    ins: 'care',
    svc: 60,
    type: '予防',
    fixed: '稲C・月',
    dow: { 月: 1, 火: 0, 水: 0, 木: 0, 金: 1, 土: 0 },
    note: '明るくお話好き。1人で訪問中。',
  },
  honda: {
    code: 'P-1089',
    name: '本田 花子',
    kana: 'ホンダ ハナコ',
    sex: '女',
    age: 76,
    addr: '千葉市稲毛区轟町2-1-5',
    area: '稲毛区',
    ins: 'med',
    svc: 60,
    type: '予防',
    fixed: '稲A・月/水/金',
    dow: { 月: 1, 火: 0, 水: 1, 木: 0, 金: 1, 土: 0 },
    note: 'ご夫婦で訪問看護。ご主人（飯塚太輝様）と同住所・同時刻に連続訪問。旧姓のため姓が異なります。',
  },
  newp: {
    code: 'P-NEW',
    name: '新規 太郎',
    kana: 'シンキ タロウ',
    sex: '男',
    age: 70,
    addr: '千葉市稲毛区小仲台6-2-1',
    area: '稲毛区',
    ins: 'med',
    svc: 60,
    type: '医療',
    fixed: '未確定（提案中）',
    dow: { 月: 0, 火: 1, 水: 0, 木: 1, 金: 0, 土: 0 },
    note: '自動提案からの新規候補。',
  },
  newpair: {
    code: 'P-NEW2',
    name: '新海 結衣',
    kana: 'シンカイ ユイ',
    sex: '女',
    age: 74,
    addr: '千葉市稲毛区天台2-3-7',
    area: '稲毛区',
    ins: 'care',
    svc: 60,
    type: '予防',
    fixed: '未確定（提案中）',
    dow: { 月: 1, 火: 0, 水: 0, 木: 0, 金: 1, 土: 0 },
    note: '自動提案からの新規候補。吉良のぞみ様と同住所（天台2-3-7）→ ペア候補。',
  },
};

// ── 週次ボード ────────────────────────────────────────────────────────────────

export const CF_WEEK: Record<OfficeKey, Record<string, Course[]>> = {
  INAGE: {
    月: [
      {
        id: '稲A',
        c: 'A',
        staff: '佐藤 Ns',
        slots: ['aoyagi', 'iizuka', 'honda', 'ebisawa', null, null],
        times: ['09:30', '10:30', '10:30', '13:00', null, null],
        pairs: [[1, 2]],
      },
      { id: '稲B', c: 'B', staff: '鈴木 Ns', slots: ['mori', 'ueno', null, null, null, null] },
      {
        id: '稲C',
        c: 'C',
        staff: '田中 Ns',
        slots: ['hisamatsu', 'kira', 'sano', null, null, null],
        times: ['09:00', '10:00', '11:15', null, null, null],
      },
      { id: '稲D', c: 'D', staff: '高橋 Ns', slots: ['takao', null, null, null, null, null] },
      { id: '稲E', c: 'E', staff: '伊藤 Ns', slots: ['ueno', 'aoyagi', null, null, null, null] },
    ],
    火: [
      {
        id: '稲A',
        c: 'A',
        staff: '佐藤 Ns',
        slots: ['hisamatsu', 'kira', 'takao', null, null, null],
      },
      {
        id: '稲B',
        c: 'B',
        staff: '鈴木 Ns',
        slots: ['ebisawa', 'okada', 'sano', 'mori', null, null],
      },
      { id: '稲M', c: 'M', staff: '渡辺 Ns', slots: ['aoyagi', 'ueno', null, null, null, null] },
    ],
    水: [
      {
        id: '稲A',
        c: 'A',
        staff: '佐藤 Ns',
        slots: ['ebisawa', 'sano', 'iizuka', 'ueno', 'aoyagi', 'mori'],
      },
      { id: '稲B', c: 'B', staff: '鈴木 Ns', slots: ['okada', 'takao', 'kira', null, null, null] },
      { id: '稲C', c: 'C', staff: '田中 Ns', slots: ['hisamatsu', null, null, null, null, null] },
      { id: '稲D', c: 'D', staff: '高橋 Ns', slots: ['aoyagi', 'sano', null, null, null, null] },
    ],
    木: [
      {
        id: '稲A',
        c: 'A',
        staff: '佐藤 Ns',
        slots: ['hisamatsu', 'okada', 'kira', null, null, null],
      },
      { id: '稲B', c: 'B', staff: '鈴木 Ns', slots: ['takao', 'ueno', null, null, null, null] },
    ],
    金: [
      {
        id: '稲A',
        c: 'A',
        staff: '佐藤 Ns',
        slots: ['aoyagi', 'ebisawa', 'iizuka', 'mori', 'kira', null],
      },
      { id: '稲B', c: 'B', staff: '鈴木 Ns', slots: ['ueno', 'sano', 'okada', null, null, null] },
      {
        id: '稲C',
        c: 'C',
        staff: '田中 Ns',
        slots: ['takao', 'hisamatsu', null, null, null, null],
      },
    ],
    土: [
      { id: '稲M', c: 'M', staff: '渡辺 Ns', slots: ['hisamatsu', null, null, null, null, null] },
    ],
    日: [],
  },
  TSUGA: {
    月: [
      {
        id: '津A',
        c: 'A',
        staff: '山本 Ns',
        slots: ['hisamatsu', 'takao', null, null, null, null],
      },
      { id: '津M', c: 'M', staff: '中村 Ns', slots: ['kira', null, null, null, null, null] },
    ],
    火: [
      {
        id: '津A',
        c: 'A',
        staff: '山本 Ns',
        slots: ['takao', 'hisamatsu', 'kira', null, null, null],
      },
    ],
    水: [
      { id: '津A', c: 'A', staff: '山本 Ns', slots: ['hisamatsu', null, null, null, null, null] },
    ],
    木: [{ id: '津A', c: 'A', staff: '山本 Ns', slots: ['takao', null, null, null, null, null] }],
    金: [{ id: '津A', c: 'A', staff: '山本 Ns', slots: ['kira', 'takao', null, null, null, null] }],
    土: [],
    日: [],
  },
};

export const CF_DOWS: Dow[] = ['月', '火', '水', '木', '金', '土', '日'];
export const CF_DATES = ['6/1', '6/2', '6/3', '6/4', '6/5', '6/6', '6/7'];

/** 承認モードで表示される仮配置 */
export const CF_PENDING: Record<OfficeKey, Record<string, PendingPlacement>> = {
  INAGE: {
    月: { courseIdx: 1, slotIdx: 2, patient: 'newp', who: '新規 太郎' },
    火: { courseIdx: 2, slotIdx: 2, patient: 'takao', who: '高尾 美咲（曜日変更）' },
  },
  TSUGA: {},
};

// ── 自動提案ランキング ────────────────────────────────────────────────────────

export interface RankScheduleRow {
  t?: string;
  name?: string;
  ins?: InsuranceKind;
  /** この行が提案枠 (空き枠ハイライト) */
  here?: boolean;
  /** 同住所ペアの提案枠 (ミント表示) */
  pair?: boolean;
  /** 同住所アンカー (紫バッジ) */
  anchor?: boolean;
}

export interface RankCandidate {
  course: string;
  staff: string;
  when: string;
  score: string;
  pair?: boolean;
  /** [ラベル, 背景色, 文字色][] */
  badges: [string, string, string][];
  schedule: RankScheduleRow[];
}

export const RANK_PAIR: RankCandidate[] = [
  {
    course: '稲C',
    staff: '田中 Ns',
    when: '月 10:00〜',
    score: '同住所連続・距離0m・空き2枠',
    pair: true,
    badges: [
      ['同住所ペア', '#F0E7F5', '#8B5C9E'],
      ['移動0分', '#D7F2EE', '#0E8472'],
    ],
    schedule: [
      { t: '09:00', name: '久松 由伽里', ins: 'care' },
      { t: '10:00', name: '吉良 のぞみ', ins: 'care', anchor: true },
      { t: '10:00', here: true, pair: true },
      { t: '11:15', name: '佐野 ふみ', ins: 'care' },
    ],
  },
  {
    course: '稲A',
    staff: '佐藤 Ns',
    when: '水 13:00〜',
    score: '同エリア・空き3枠',
    badges: [
      ['同エリア', '#E2EDF8', '#2F6FB0'],
      ['余裕あり', '#F7EDD4', '#B0831F'],
    ],
    schedule: [
      { t: '09:30', name: '海老澤 均尚', ins: 'med' },
      { t: '11:00', name: '佐野 ふみ', ins: 'care' },
      { t: '13:00', here: true },
      { t: '14:30', name: '上野 さくら', ins: 'med' },
    ],
  },
  {
    course: '稲E',
    staff: '伊藤 Ns',
    when: '月 14:30〜',
    score: '希望曜日一致・空き4枠',
    badges: [['曜日一致', '#E2EDF8', '#2F6FB0']],
    schedule: [
      { t: '10:00', name: '上野 さくら', ins: 'med' },
      { t: '11:30', name: '青柳 あい', ins: 'med' },
      { t: '14:30', here: true },
    ],
  },
];

export const RANK_NORMAL: RankCandidate[] = [
  {
    course: '稲A',
    staff: '佐藤 Ns',
    when: '火 09:30〜',
    score: '同エリア・希望曜日一致・空き3枠',
    badges: [
      ['同エリア', '#D7F2EE', '#0E8472'],
      ['曜日一致', '#E2EDF8', '#2F6FB0'],
    ],
    schedule: [
      { t: '09:30', here: true },
      { t: '10:45', name: '久松 由伽里', ins: 'care' },
      { t: '13:00', name: '高尾 美咲', ins: 'care' },
      { t: '14:15', name: '吉良 のぞみ', ins: 'care' },
    ],
  },
  {
    course: '稲B',
    staff: '鈴木 Ns',
    when: '木 10:30〜',
    score: '希望時間帯一致・空き4枠',
    badges: [['午前OK', '#F7EDD4', '#B0831F']],
    schedule: [
      { t: '09:30', name: '高尾 美咲', ins: 'care' },
      { t: '10:30', here: true },
      { t: '13:00', name: '上野 さくら', ins: 'med' },
    ],
  },
  {
    course: '稲M',
    staff: '渡辺 Ns',
    when: '火 13:30〜',
    score: '空き多め・調整しやすい',
    badges: [['余裕あり', '#F7EDD4', '#B0831F']],
    schedule: [
      { t: '09:30', name: '青柳 あい', ins: 'med' },
      { t: '11:00', name: '上野 さくら', ins: 'med' },
      { t: '13:30', here: true },
    ],
  },
];

// ── Warm & Human パレット ───────────────────────────────────────────────────

export const CF_THEME = {
  TEAL: '#0D9488',
  TEAL_DEEP: '#0F766E',
  TERRA: '#D97706',
  TERRA_DEEP: '#B45309',
  PLUM: '#8B5C9E',
  MINT: '#0E9F6E',
  INK: '#1C1917',
  INK2: '#57534E',
  INK3: '#A8A29E',
  CREAM: '#FAF7F2',
  LINE: '#EAE3D8',
  PANEL: '#FFFFFF',
  GOLD: '#C08A1E',
} as const;

/** コース色 (Warm-aligned, muted on cream) */
export const CF_COURSE: Record<CourseColorKey, { c: string; bg: string; soft: string }> = {
  A: { c: '#0D8478', bg: '#D7F2EE', soft: '#EBF9F7' },
  B: { c: '#2F6FB0', bg: '#E2EDF8', soft: '#F0F5FB' },
  C: { c: '#8B5C9E', bg: '#F0E7F5', soft: '#F7F2F9' },
  D: { c: '#C75C77', bg: '#FAE4E9', soft: '#FCEFF2' },
  E: { c: '#B0831F', bg: '#F7EDD4', soft: '#FBF5E6' },
  M: { c: '#D97706', bg: '#FCEBD6', soft: '#FDF4E8' },
};

export const cc = (k: string): { c: string; bg: string; soft: string } =>
  CF_COURSE[k as CourseColorKey] || CF_COURSE.M;

/**
 * 患者キーから患者を取得。モックデータは整合済みで、ボード上に現れるキーは
 * 必ず CF_PATIENTS に存在する前提だが、`noUncheckedIndexedAccess` 下では
 * undefined 可能性を潰すため明示ヘルパーを用意する。存在しないキーは
 * バグなので開発時に気付けるよう throw する。
 */
export function getPatient(pk: string): Patient {
  const p = CF_PATIENTS[pk];
  if (!p) {
    throw new Error(`CF_PATIENTS に存在しない患者キー: ${pk}`);
  }
  return p;
}
