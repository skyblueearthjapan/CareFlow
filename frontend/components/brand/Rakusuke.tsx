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

interface RakusukeSaysProps {
  pose: RakusukePose;
  /** らく助の一言 (話し言葉・断定しない・警告でも責めない — R-10文言規約)。 */
  message: React.ReactNode;
  tone?: 'normal' | 'warn';
  size?: 'sm' | 'md';
  className?: string;
  children?: React.ReactNode;
}

/**
 * らく助の吹き出し (R-10 アドバイザーUX)。提案・診断ダイアログ/パネルのヘッダに
 * 1つだけ置く。既存コンテンツは children にそのまま格納 (データ表示は不変)。
 * 設計書: docs/plans/rakusuke-advisor-ux-design.md
 */
export function RakusukeSays({
  pose,
  message,
  tone = 'normal',
  size = 'md',
  className,
  children,
}: RakusukeSaysProps) {
  const warn = tone === 'warn';
  return (
    <div className={cn('flex items-start gap-2.5', className)}>
      <Rakusuke pose={pose} className={cn('shrink-0', size === 'sm' ? 'h-12' : 'h-16')} />
      <div
        className={cn(
          'relative min-w-0 flex-1 rounded-xl border px-3 py-2',
          warn ? 'border-warning bg-warning-bg' : 'border-brand-primary-light bg-brand-primary-50',
        )}
      >
        <span
          aria-hidden
          className={cn(
            'absolute -left-1.5 top-4 h-3 w-3 rotate-45 border-b border-l',
            warn
              ? 'border-warning bg-warning-bg'
              : 'border-brand-primary-light bg-brand-primary-50',
          )}
        />
        <p
          className={cn(
            'text-sm font-bold',
            warn ? 'text-warning-strong' : 'text-brand-primary-hover',
          )}
        >
          {message}
        </p>
        {children && <div className="mt-1.5 text-sm text-text-primary">{children}</div>}
      </div>
    </div>
  );
}

interface RakusukeWorkingProps {
  /** らく助の一言 (例: 「カイポケに入力しています」)。 */
  message: React.ReactNode;
  /** 補足行 (例: 「そのまま見守っていて大丈夫です」)。 */
  sub?: React.ReactNode;
  pose?: RakusukePose;
  className?: string;
}

/**
 * らく助が作業中のアニメーション演出 (R-11)。連携RPAの実行中カード等に置く。
 * ゆらゆら動くらく助 + 点滅する「…」で「代わりに入力してくれている」感を出す。
 */
export function RakusukeWorking({
  message,
  sub,
  pose = 'calendar',
  className,
}: RakusukeWorkingProps) {
  return (
    <div className={cn('flex items-center gap-2.5', className)}>
      <Rakusuke pose={pose} className="rakusuke-bob h-14 shrink-0" />
      <div className="relative min-w-0 flex-1 rounded-xl border border-brand-primary-light bg-brand-primary-50 px-3 py-2">
        <span
          aria-hidden
          className="absolute -left-1.5 top-4 h-3 w-3 rotate-45 border-b border-l border-brand-primary-light bg-brand-primary-50"
        />
        <p className="text-sm font-bold text-brand-primary-hover">
          {message}
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="rakusuke-dot"
              style={{ animationDelay: `${i * 0.2}s` }}
              aria-hidden
            >
              .
            </span>
          ))}
        </p>
        {sub && <p className="mt-0.5 text-xs text-text-secondary">{sub}</p>}
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
