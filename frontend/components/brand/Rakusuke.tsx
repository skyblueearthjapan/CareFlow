/**
 * らく助マスコット — 共通コンポーネント (リブランディング R-4)
 *
 * 配置原則: マスコットは「状態の瞬間」(空状態・完了・エラー・確認) に出す。
 * 高密度の業務グリッド (スケジュール表・モニター等) には常駐させない。
 * ポーズは意味で選ぶ:
 *   joy=空(ポジティブ) / calendar=予定・スケジュール / clap=完了・提出 /
 *   cheer=大きな達成 / heart=安心・ねぎらい / idea=提案 / puzzled=エラー・迷子 /
 *   think=検索ヒットなし・思案 / visit=訪問・移動中 / wave=挨拶・おつかれさま
 */
import { cn } from '@/lib/utils';

export type RakusukePose =
  | 'wave'
  | 'joy'
  | 'calendar'
  | 'clap'
  | 'cheer'
  | 'heart'
  | 'idea'
  | 'puzzled'
  | 'think'
  | 'visit';

const POSE_SRC: Record<RakusukePose, string> = {
  wave: '/brand/rakusuke-pose-wave.png',
  joy: '/brand/rakusuke-pose-joy.png',
  calendar: '/brand/rakusuke-pose-calendar.png',
  clap: '/brand/rakusuke-pose-clap.png',
  cheer: '/brand/rakusuke-pose-cheer.png',
  heart: '/brand/rakusuke-pose-heart.png',
  idea: '/brand/rakusuke-pose-idea.png',
  puzzled: '/brand/rakusuke-pose-puzzled.png',
  think: '/brand/rakusuke-pose-think.png',
  visit: '/brand/rakusuke-pose-visit.png',
};

/** らく助の素の画像 (装飾・スクリーンリーダー非公開)。高さは className で指定する。 */
export function Rakusuke({ pose, className }: { pose: RakusukePose; className?: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- 静的ブランド画像 (装飾)
    <img src={POSE_SRC[pose]} alt="" aria-hidden className={cn('w-auto', className)} />
  );
}

interface RakusukeTitleProps {
  pose: RakusukePose;
  /** ページタイトル (h1・font-serif text-2xl。全ページ共通の型)。 */
  title: React.ReactNode;
  /** サブタイトル (text-sm text-text-secondary)。 */
  subtitle?: React.ReactNode;
  className?: string;
}

/**
 * ページ上部のタイトルブロック: らく助 + h1 + サブタイトル (R-6・PO要望 2026-07-10)。
 * 各ページの header 左側 (h1+p) をこのコンポーネントに置き換えて使う。
 * ポーズはページの意味で選ぶ (POSE_SRC のコメント参照)。
 */
export function RakusukeTitle({ pose, title, subtitle, className }: RakusukeTitleProps) {
  return (
    <div className={cn('flex items-center gap-3', className)}>
      <Rakusuke pose={pose} className="h-11 shrink-0" />
      <div className="space-y-0.5">
        <h1 className="font-serif text-2xl font-bold text-text-primary">{title}</h1>
        {subtitle && <p className="text-sm text-text-secondary">{subtitle}</p>}
      </div>
    </div>
  );
}

interface RakusukeNoteProps {
  pose: RakusukePose;
  /** 主文 (例: 「本日の訪問はありません」) */
  title?: React.ReactNode;
  /** らく助の一言 (例: 「おつかれさまでした！」) */
  comment?: React.ReactNode;
  size?: 'sm' | 'md';
  className?: string;
}

/** 空状態・完了などの「状態の瞬間」ブロック: らく助 + 主文 + 一言。 */
export function RakusukeNote({ pose, title, comment, size = 'md', className }: RakusukeNoteProps) {
  return (
    <div className={cn('flex flex-col items-center gap-0.5 py-2 text-center', className)}>
      <Rakusuke pose={pose} className={size === 'sm' ? 'h-14' : 'h-20'} />
      {title && <p className="mt-1.5 text-sm font-medium text-text-primary">{title}</p>}
      {comment && <p className="text-xs text-text-muted">{comment}</p>}
    </div>
  );
}
