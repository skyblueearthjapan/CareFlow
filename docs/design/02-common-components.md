# 02. 共通コンポーネント（Warm & Human）

CareLink の共通部品集。shadcn/ui をベースに Warm & Human トーンに揃える。

---

## 2-1. Button

### バリエーション

| variant | 背景 | 文字 | 枠線 |
|---|---|---|---|
| `primary` | `--brand-primary` (teal) | `#FFFFFF` | なし |
| `secondary` | `--bg-base` | `--brand-primary` | `--brand-primary` |
| `ghost` | 透明 | `--text-primary` | なし |
| `outline` | `--bg-base` | `--text-primary` | `--border-strong` |
| `danger` | `--error` | `#FFFFFF` | なし |

### サイズ

| size | 高さ | padding-x | font-size |
|---|---|---|---|
| `sm` | 30px | 10px | 12px |
| `md` | 36px | 14px | 13px |
| `lg` | 42px | 18px | 14px |

### 共通 Spec
- border-radius: **6px**
- font-weight: 500
- アイコン+テキストの gap: 6px
- hover: 背景1段濃く（primary→hover, outline→bg-muted）
- focus: フォーカスリング 2px brand-primary + offset 2px

### Wireframe

```
[primary]                  [secondary]
┌────────────────┐         ┌────────────────┐
│   保存する     │         │  キャンセル    │
└────────────────┘         └────────────────┘
ティール背景・白文字       白背景・ティール枠

[ghost]                    [outline]                  [danger]
┌────────────────┐         ┌────────────────┐         ┌────────────┐
│  詳細を見る    │         │  削除          │         │  削除する  │
└────────────────┘         └────────────────┘         └────────────┘
透明背景                    白背景・グレー枠            赤背景・白文字
```

---

## 2-2. Input

### バリエーション
text / email / password / number / search / date / time / textarea

### サイズ
| size | 高さ |
|---|---|
| `sm` | 30px |
| `md` | 32px (デフォルト) |
| `lg` | 40px (ログイン等) |

### Spec
- padding: 0 12px（textarea: 12px）
- border: 1px solid `--border-default`
- background: `--bg-base`
- border-radius: **6px**
- font-size: 13-14px
- focus: 枠線 `--brand-primary` + box-shadow 0 0 0 3px `--brand-primary-light`
- placeholder: `--text-muted`
- error 時: 枠線 `--error`、下にエラーメッセージ赤

### Wireframe

```
[default]                          [filled]
┌─────────────────────────┐        ┌─────────────────────────┐
│ 患者名を入力             │        │ 田中 太郎               │
└─────────────────────────┘        └─────────────────────────┘

[focused]
┌─────────────────────────┐  ← 枠線ティール、外周にライトリング
│ 田中太郎│                │
└─────────────────────────┘

[error]
┌─────────────────────────┐
│ 田中太郎                 │  ← 枠線赤
└─────────────────────────┘
✗ 名前は必須です            (赤文字)

[search with icon]
┌─────────────────────────┐
│ 🔍 検索...               │
└─────────────────────────┘

[textarea]
┌─────────────────────────┐
│ 備考を入力              │
│                         │
│                         │
└─────────────────────────┘
```

---

## 2-3. Select / Dropdown

### Wireframe

```
[default]
┌─────────────────────────┐
│ 拠点を選択           ▼  │  ← 右にカスタム矢印（SVG）
└─────────────────────────┘

[open]
┌─────────────────────────┐
│ 稲毛                 ▲  │
└─────────────────────────┘
┌─────────────────────────┐
│ ▸ 稲毛                  │  ← ハイライト
│   都賀                  │
│   千葉                  │
└─────────────────────────┘
```

### Spec
- 標準サイズ: 高さ 30px
- padding: 0 22px 0 10px（右に矢印の余白）
- 矢印は SVG: `<path d='m3 5 3 3 3-3' stroke='%239CA3AF'/>`
- ドロップダウン: shadow-md、radius 10px

---

## 2-4. Checkbox / Radio / Switch

```
[Checkbox]
□ 未選択      ☑ 選択      ⊟ 部分選択
accent-color: var(--brand-primary)

[Radio]
○ 未選択      ⦿ 選択

[Switch]
○─ OFF        ─● ON (teal背景)
```

### Spec
- Checkbox / Radio: 16-18px、`accentColor: var(--brand-primary)`
- Switch: 36×22px、padding 2px、radius 999、トグル白丸 18px
- ON時の背景: `--brand-primary` (teal)
- OFF時の背景: `--border-strong`

---

## 2-5. Badge / Tag

業務種別やステータスを示す小ピル。`<Badge tone="..." dot={true|false}>...</Badge>`

### Wireframe

```
[業務種別]
┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐
│ 医療 │  │ 介護 │  │ 同行 │  │ 特  │
└──────┘  └──────┘  └──────┘  └──────┘
ティール   テラコッタ アンバー   ピンク

[ステータス（dot付き）]
┌─────────┐  ┌─────────┐  ┌─────────┐
│ ● 稼働  │  │ ● 休止  │  │ ● 失敗  │
└─────────┘  └─────────┘  └─────────┘
緑           アンバー    赤
```

### Spec
- 高さ: 自然高（line-height 1.5）
- padding: 2px 8px
- border-radius: 999px (full pill)
- font-size: 11px
- font-weight: 500
- gap: 4px（dotとテキスト間）
- dot: 6×6px circle、文字色と同色

### tone マッピング

| tone | bg | color |
|---|---|---|
| `neutral` | `--bg-muted` | `--text-secondary` |
| `medical` | `--c-medical-bg` | `--c-medical` |
| `care` | `--c-care-bg` | `#047857` |
| `event` | `--c-event-bg` | `#6D28D9` |
| `coupled` | `--c-coupled-bg` | `#92400E` |
| `mentor` | `--c-mentor-bg` | `#0F766E` |
| `special` | `--c-special-bg` | `#BE185D` |
| `success` | `--success-bg` | `#047857` |
| `warning` | `--warning-bg` | `#92400E` |
| `error` | `--error-bg` | `#B91C1C` |
| `info` | `--info-bg` | `--brand-primary` |

---

## 2-6. Card

```
┌──────────────────────────────────────┐
│ [ヘッダー: アイコン + タイトル + 右]│
│ ─────────────────────────────         │
│                                      │
│ コンテンツ                            │
│                                      │
└──────────────────────────────────────┘
```

### Spec
- background: `--bg-base`
- border: 1px solid `--border-default`
- **border-radius: 16px**（カード本体）
- shadow: `--shadow-sm`
- padding: 14px（小）/ 18px（標準）/ 20-24px（大）

### CardTitle
- アイコン (Lucide 14-16px) + タイトル (13.5px / 600) + 右補助
- 下に 1px subtle 罫線（任意）

---

## 2-7. Modal / Dialog

```
[overlay: rgba(28,25,23,0.45) + backdrop-blur(4px)]

              ┌──────────────────────────────────┐
              │ ✕  タイトル(Serif 20/700)         │
              │    サブタイトル (12 muted)         │
              ├──────────────────────────────────┤
              │                                  │
              │ コンテンツ（padding 20-24px）   │
              │                                  │
              ├──────────────────────────────────┤
              │             [キャンセル] [保存] │
              └──────────────────────────────────┘
              radius 18px、shadow-xl + 1px border
```

### Spec
- max-width: 460-780px（用途別）
- background: `--bg-base`
- border-radius: **18px**
- shadow: `0 24px 60px rgba(28,25,23,0.20), 0 0 0 1px var(--border-default)`
- ヘッダー: padding 18 24 16、border-bottom
- ボディ: padding 20-24、scrollable
- フッター: padding 14 24、border-top、bg `--bg-app`、右寄せボタン
- ESC で閉じる、外側クリックで閉じる
- 入りアニメ: 200ms ease-out

### サイズ別
- 460px: 訪問詳細
- 520px: AI入力
- 580px: スタッフ固定シフト
- 680px: スタッフその週だけシフト
- 780px: 患者固定枠カレンダー

---

## 2-8. Toast / Notification

```
[右上から表示・自動消滅 4秒]
┌──────────────────────────────┐
│ ✓ 保存しました                │
│ 田中太郎の情報を更新          │
└──────────────────────────────┘
緑系背景、shadow-md、radius 10

[エラー]
┌──────────────────────────────┐
│ ✗ エラー                      │
│ 保存できませんでした          │
│ [再試行]                      │
└──────────────────────────────┘
赤系背景、消えない（手動で閉じる）
```

---

## 2-9. Table

```
┌─────┬───────────┬───────────┬────────┬──────┐
│ ☐   │ 患者名 ▲  │ 拠点      │ 状態   │ 操作 │
├─────┼───────────┼───────────┼────────┼──────┤
│ ☐   │ 田中太郎  │ 稲毛       │ ●稼働 │ ⋯   │
│ ☐   │ 山田花子  │ 都賀       │ ●休止 │ ⋯   │
└─────┴───────────┴───────────┴────────┴──────┘
                                   [<] 1/5 [>]
```

### Spec
- 行高: 44-56px
- 罫線: 1px solid `--border-subtle`
- ヘッダ背景: `--bg-app`
- ヘッダ文字: 11px / 600 / uppercase / letter-spacing 0.04em
- 行 hover: `--bg-muted`
- 選択行: 左に 3px 縦バー `--brand-primary` + 背景 `--brand-primary-light`

### grid templating（連携センターの例）
```
gridTemplateColumns: '40px 1.4fr 80px 90px 1.6fr 1fr'
   ☐   利用者   日付   種別   詳細   メモ
```

---

## 2-10. Tabs

```
┌──────────┬──────────┬──────────┬──────────┐
│ 患者 30  │ スタッフ12│ 拠点 2   │ 市区50   │
└──────────┴──────────┴──────────┴──────────┘
   ↑ティール下線2px                   ← アクティブ

[コンテンツエリア]
```

### Spec
- アクティブ: text-primary、border-bottom 2px `--brand-primary`、margin-bottom -1px
- 非アクティブ: text-secondary
- ボタン padding: 10px 16px、font 13/500
- 件数: 小さく薄色 `--text-muted`、左マージン4px

---

## 2-11. Segmented Control

```
[統合 / スタッフ別 / 患者別]
┌────────────────────────────┐
│  統合 │スタッフ別│患者別  │
└──────╥──────────────────────┘
       ↑アクティブ：白背景＋shadow-xs
```

### Spec
- 外殻: `--bg-muted` + radius 6 + padding 2
- アクティブ: `--bg-base` + shadow-xs + radius 4
- ボタン: padding 5px 12px、font 12/500
- 切替アニメ: 100ms ease

---

## 2-12. Accordion

```
[折りたたみ]
┌──────────────────────────────────┐
│ ▶ セクション                     │
└──────────────────────────────────┘

[展開]
┌──────────────────────────────────┐
│ ▼ セクション                     │
│ ──────────────                   │
│ コンテンツ                        │
└──────────────────────────────────┘
```

### Spec
- transition: 200ms ease
- border: 1px `--border-default`、radius 8
- ヘッダ padding: 12-14px

---

## 2-13. Tooltip

```
┌──────────────────┐
│ クリックで保存    │
└────────┬─────────┘
         ▼
       [ボタン]
```

### Spec
- background: `--text-primary` (黒)
- color: `#FFFFFF`
- max-width: 240px
- padding: 4px 8px
- font-size: 12px
- 表示遅延: 500ms
- アニメ: fade 100ms

---

## 2-14. Skeleton Loader

```
┌──────────────────────────────┐
│ ████████████░░░             │
│ ███░░░░░░░░░░░░░             │
│ ████████░░░░░░░             │
└──────────────────────────────┘
グレープレースホルダー、shimmer
```

### Spec
- 背景: `--bg-muted`
- shimmer: linear-gradient + 1.5s loop

---

## 2-15. Empty State

```
        🗒  (Lucide ClipboardList)
   患者がまだ登録されていません

      [+ 患者を追加]
```

### Spec
- 中央配置、padding 60px
- アイコン: 48px、色 `--text-muted`
- メッセージ: 14px / 500 / `--text-secondary`
- アクション: 通常ボタン

---

## 2-16. Avatar / Initials

```
[未設定]    [写真]      [複数]
   👤        [📸]       [田][山][佐]+2

[サイズ違い]
20  24  32  48
```

### Spec
- 円形（radius 50%）
- 背景色: スタッフごとにシード固定（sky/rose/amber/emerald/violet/cyan/fuchsia/indigo/pink/teal/lime/slate）
- 文字: 白、頭文字1〜2字
- font-size: size × 0.45
- font-weight: 600

### カラー定義
```
sky:'#0EA5E9', rose:'#F43F5E', amber:'#F59E0B',
emerald:'#10B981', violet:'#8B5CF6', cyan:'#06B6D4',
fuchsia:'#D946EF', indigo:'#6366F1', pink:'#EC4899',
teal:'#14B8A6', lime:'#84CC16', slate:'#64748B'
```

---

## 2-17. Calendar / Date Picker

```
┌──────────────────────────────┐
│  ◀  2026年4月  ▶             │
├──────────────────────────────┤
│  日 月 火 水 木 金 土         │
│              1  2  3  4       │
│   5  6  7  8  9 10 11        │
│  12 13 14 15 16 17 18         │
│  19 20 21 22 23 24 25         │
│  26 27 28 29 30               │
└──────────────────────────────┘
```

### スタイル
- 今日: ティール円
- 選択: ティール背景・白文字
- 無効: グレーアウト

---

## 2-18. AI入力フローティングボタン（FAB・独自）

全画面右下に常駐するグローバル要素。

```
                                    ┌───────────┐
                                    │   ✦       │  ← Sparkles icon 22px
                                    │  AI入力  │     gradient teal
                                    └───────────┘
                                    60×60 円
                                    リング光彩
```

### Spec
- 位置: PC = 右 28px / 下 28px（メイン領域内）
- モバイル: 右 16px / 下 88px（ボトムナビ上）
- サイズ: 60×60px (PC) / 64×64px (モバイル)
- 背景: `linear-gradient(135deg, var(--brand-primary), #14B8A6)`
- shadow: `0 8px 24px rgba(13,148,136,0.40), 0 0 0 6px rgba(13,148,136,0.10)`
- アイコン: Lucide Sparkles 22px、白
- z-index: 20-100
- hover: scale 1.05、200ms

---

## 2-19. Lucide系アイコンセット

CareLink で使う代表的アイコン（`window.Icon` でアクセス可能）：

```
Heart        ロゴ
Home         ダッシュボード
Calendar     週ビュー
CalendarDays 今週・カレンダー
ClipboardList マスタ管理
Refresh      連携・同期
Bell         通知
ChevronDown/Up/Left/Right ナビ
ArrowRight   遷移
PanelLeft    サイドバー切替
Plus / X     追加・閉じる
Search       検索
Sparkles     AI入力
AlertCircle  警告
CheckCircle  成功
XCircle      失敗
CheckLg      チェックマーク
Edit / Trash 編集・削除
Mic / Send   音声入力・送信
Eye / Mail / Lock 認証系
User / Users 個人・複数
Settings / LogOut 設定・退出
Server / Activity / Zap ステータス系
Pause / PlayCircle 制御
Monitor      VNC画面
Download / Upload エクスポート/取込
GitCompare   差分検出
ExternalLink 外部リンク
Clock        時刻
Phone        連絡
MapPin       住所
Coffee       休憩
```

### Spec
- 線スタイル: `strokeWidth: 1.75`（標準）/ `2.2`（アクティブ）
- 色: 親要素から `currentColor` で継承
- サイズ: 12 / 14 / 15 / 16 / 18 / 22 / 28（用途別）

---

## 2-20. Section（マスタ詳細用）

マスタ管理の詳細パネルでフィールドをグループ化する区切り。

```
基本情報                          [アクション]
─────────────────────────────────
名前         [────入力────]
フリガナ     [────入力────]
性別         ⦿ 男 ○ 女
状態         ●稼働 (バッジ)

通常週パターン                  カレンダーで編集 →
─────────────────────────────────
設定中の枠   月 10:00-11:00
             水 14:00-15:00
             金 10:00-11:00
```

### Spec
- セクション見出し: 11px / 600 / uppercase / letter-spacing 0.06em / `--text-muted`
- セクション下罫: 1px `--border-subtle`、padding-bottom 8px
- フィールド grid: `'140px 1fr'` / gap `12px 20px`
- `Lab` (label): 12px / `--text-muted` / padding-top 6px
- `Val` (value): 13px / `--text-primary`
