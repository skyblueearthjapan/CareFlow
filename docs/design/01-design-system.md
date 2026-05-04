# 01. デザインシステム（Warm & Human）

CareLink の基礎デザイントークン。
**Teal × Terracotta on Cream** の温かいクリニカルパレット。

---

## 1-1. ブランドカラー

| トークン | HEX | 用途 |
|---|---|---|
| `--brand-primary` | **`#0D9488`** ティール | ロゴ・主要ボタン・アクティブ状態 |
| `--brand-primary-hover` | `#0F766E` | ボタン hover |
| `--brand-primary-light` | `#CCFBF1` | バッジ・選択行背景 |
| `--brand-primary-50` | `#F0FDFA` | アイコンチップ背景 |
| `--brand-accent` | **`#D97706`** テラコッタ | アクセント・強調 |
| `--brand-accent-light` | `#FEF3C7` | アクセントバッジ背景 |

ロゴは `linear-gradient(135deg, #0D9488, #14B8A6)` のティール系グラデーションを使う。

---

## 1-2. ニュートラル（クリーム基調）

| トークン | HEX | 用途 |
|---|---|---|
| `--bg-base` | `#FFFFFF` | カード・モーダル本体 |
| `--bg-surface` | `#FFFFFF` | サブ背景（薄カード） |
| `--bg-app` | **`#FAF7F2`** クリーム | アプリ全体の地色 |
| `--bg-muted` | `#F5F1EA` | 強調しない背景・休み枠 |
| `--border-default` | `#E7E5E4` | 通常枠線 |
| `--border-subtle` | `#F0EDE8` | 控えめ罫線 |
| `--border-strong` | `#D6D3D1` | 強い枠線 |
| `--text-primary` | `#1C1917` | 本文 |
| `--text-secondary` | `#57534E` | サブテキスト |
| `--text-muted` | `#A8A29E` | 無効・補足 |
| `--text-inverted` | `#FFFFFF` | 暗背景上のテキスト |

外側ウィンドウ背景は `#E7E2D8`（クリームより一段暗い）。

---

## 1-3. セマンティックカラー

| トークン | HEX | 用途 |
|---|---|---|
| `--success` | `#059669` | 成功・割当済み |
| `--success-bg` | `#D1FAE5` | 成功バッジ背景 |
| `--warning` | `#D97706` | 警告・要確認 |
| `--warning-bg` | `#FEF3C7` | 警告バッジ背景 |
| `--error` | `#DC2626` | 失敗・エラー |
| `--error-bg` | `#FEE2E2` | エラーバッジ背景 |
| `--info` | `#0D9488` | 情報（primary と同色） |
| `--info-bg` | `#CCFBF1` | 情報バッジ背景 |

---

## 1-4. データ系カラー（業務種別バッジ用）

クリーム×テラコッタに調和する Warm パレット。

| トークン | HEX (前景 / 背景) | 用途 |
|---|---|---|
| `--c-medical` | `#0F766E` / `#CCFBF1` | 医療保険訪問 |
| `--c-care` | `#92400E` / `#FEF3C7` | 介護保険訪問 |
| `--c-event` | `#9D174D` / `#FCE7F3` | イベント・会議 |
| `--c-coupled` | `#9A3412` / `#FFEDD5` | 2名体制 |
| `--c-mentor` | `#0F766E` / `#CCFBF1` | 同行指導 |
| `--c-special` | `#BE185D` / `#FCE7F3` | 特別週 |
| `--c-manager` | `#6B7280` / `#F3F4F6` | マネージャー枠 |

### 週ビューのVisitChip専用カラー（読みやすさのため標準色寄り）

| 種別 | 背景 | 枠線 | 文字色 |
|---|---|---|---|
| medical | `#EFF6FF` | `#BFDBFE` | `#1D4ED8` |
| care | `#ECFDF5` | `#A7F3D0` | `#047857` |
| event | `#F5F3FF` | `#DDD6FE` | `#6D28D9` |
| coupled | `#FFFBEB` | `#FDE68A` | `#92400E` |
| special | `#FDF2F8` | `#FBCFE8` | `#BE185D` |

VisitChip は左に 3px のアクセントバーを持ち、その色は文字色と同じ（読みやすさ確保）。

---

## 1-5. タイポグラフィ

```
--font-sans:  'Noto Sans JP', -apple-system, system-ui, sans-serif;
--font-serif: 'Noto Serif JP', 'Hiragino Mincho ProN', serif;
--font-mono:  'JetBrains Mono', ui-monospace, monospace;
```

### スケール

```
text-xs    11-12px  / 16px / 400  キャプション・補足・バッジ
text-sm    13px     / 20px / 400  サブテキスト・メニュー
text-base  14px     / 22px / 500  本文・標準ボタン
text-md    16-17px  / 24px / 600  セクション見出し
text-lg    18px     / 28px / 700  カード見出し（**Serif**）
text-xl    20px     / 28px / 700  モーダル見出し（**Serif**）
text-2xl   28px     / 36px / 700  ページ見出し（**Serif**）
text-num   38-44px  / 1.0  / 700  大きな数字（**Serif**・tabular-nums）
```

### 使い分け

- **Serif**：ページ見出し、モーダル見出し、ロゴワードマーク、ダッシュボードの大きな数字
- **Sans**：本文、メニュー、ボタン、入力、テーブル
- **Mono**：時刻（時計）、ID、コード片、VNCコンソール

### tabular-nums

数字を縦揃えするため `font-variant-numeric: tabular-nums` を `.tnum` クラスで適用。
（時刻、件数、緯度経度、日付など）

---

## 1-6. スペーシング（4px ベース）

```
0  → 0px
1  → 4px       最小ギャップ
2  → 8px       アイコン↔テキスト
3  → 12px      小要素間
4  → 14-16px   標準ギャップ
5  → 18-20px   セクション内
6  → 24px      セクション間
8  → 32px      大セクション間
```

CareLink 推奨ベース：**14px / 16px / 20px / 24px** を多用。

---

## 1-7. ボーダー半径（やや大きめ・温かい）

```
--radius-sm:  8px      バッジ・小ボタン
--radius:     10px     入力・標準ボタン
--radius-md:  12px     カード内要素
--radius-lg:  16px     カード本体
--radius-xl:  20px     外枠ウィンドウカード
円形:        50%       アバター・ボタンFAB
ピル:        9999px    バッジ
```

---

## 1-8. 影（柔らかい・多層）

土と紙のような落ち着いた影。`rgba(28,25,23,...)` の温かい黒を使う。

```
--shadow-xs:  0 1px 2px rgba(28,25,23,0.04)
--shadow-sm:  0 1px 3px rgba(28,25,23,0.06), 0 1px 2px rgba(28,25,23,0.04)
--shadow-md:  0 4px 12px rgba(28,25,23,0.06), 0 2px 4px rgba(28,25,23,0.04)
--shadow-lg:  0 12px 24px rgba(28,25,23,0.08), 0 4px 8px rgba(28,25,23,0.04)
--shadow-xl:  0 24px 48px rgba(28,25,23,0.10)
```

外枠カードは `0 1px 2px rgba(28,25,23,0.04), 0 8px 24px rgba(28,25,23,0.05)` の合成。

FAB のリング光彩：`0 8px 24px rgba(13,148,136,0.40), 0 0 0 6px rgba(13,148,136,0.10)`

---

## 1-9. ブレークポイント

```
mobile     ~ 767px
tablet     768 ~ 1023px
desktop    1024 ~ 1379px
wide       1380px ~      （最適化フレーム）
```

PCプロトタイプは **1380px × 900px** ベース。

---

## 1-10. モーション

```
duration-fast    100ms    hover、focus
duration-normal  200ms    通常遷移、モーダル
duration-slow    300ms    ページ遷移、サイドバー折りたたみ

ease-out:    cubic-bezier(0, 0, 0.2, 1)
ease-in-out: cubic-bezier(0.4, 0, 0.2, 1)
```

サイドバー折りたたみは 200ms ease。
モーダルは 200ms ease-out + backdrop blur 4px。

---

## 1-11. アクセシビリティ

- フォーカスリング: `2px solid var(--brand-primary)` + offset 2px、3px radius
- 入力フォーカス: 枠線 brand-primary + box-shadow 3px brand-primary-light
- 最小タッチターゲット: 44×44px（モバイル必須）
- コントラスト比: 本文 4.5:1 以上、大文字 3:1 以上
- キーボード操作: 全画面 Tab 移動可能、Cmd/Ctrl+K で AI入力起動
- スクリーンリーダー: aria 属性必須

---

## 1-12. 密度（density）切替

shadcn/ui 同様、密度モードを CSS 変数で切替可能。

```
[data-density="compact"]   --row-h: 36px / --pad-card: 16px / --gap: 8px
[data-density="standard"]  --row-h: 44px / --pad-card: 20px / --gap: 12px
[data-density="comfy"]     --row-h: 56px / --pad-card: 28px / --gap: 16px
```

CareLink デフォルトは **standard**。
