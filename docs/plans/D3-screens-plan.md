# D3: Screens (PC + Mobile) 実装計画書

## 1. 概要・目的

CareLink の全画面 (PC + Mobile) を Next.js 15 App Router + Tailwind + shadcn/ui の上に実装する。D2 が用意した土台 (`AppShell`, `Sidebar`, `Header`, `Card`, `Badge`, `ModalShell`, `Button`, `Input`, `Avatar`, `Toast`, トークン CSS 変数) を最大限再利用し、UI 開発の重複を避ける。

**Warm & Human デザイン (Teal #0D9488 / Terracotta #D97706 / Cream #FAF7F2 + Noto Serif JP / Noto Sans JP)** を全画面で厳守する。

成果物:
- 画面: ログイン / ダッシュボード / 週ビュー (中核) / マスタ管理 / 連携センター
- モーダル: AI入力, 訪問詳細, 患者固定枠編集, スタッフ「その週だけ休み」編集, スタッフ固定シフト編集
- モバイル4画面: ホーム / 今日 / 今週 / マイページ
- ロール別表示分岐 (admin / staff)
- ローディング / エラー / 空状態の網羅

## 2. 画面リスト + ルーティング表

| # | URL | 画面 | アクセス | 主要モード | モバイル対応 |
|---|---|---|---|---|---|
| 1 | `/login` | ログイン | 未認証 | — | ◎ レスポンシブ |
| 2 | `/dashboard` | ダッシュボード | admin/staff | role別 | △ モバイルは `/m/home` |
| 3 | `/weekly?week=YYYY-Www` | 週ビュー (中核) | admin/staff | integrated/staff/patient | × PCのみ |
| 4 | `/master?tab=patient\|staff\|office\|city` | マスタ管理 | admin only | 4タブ + Master-Detail | △ 一覧→詳細遷移 |
| 5 | `/integration` | 連携センター | admin only | 6パネル | △ 簡易表示 |
| 6 | `/m/home` | モバイルホーム | admin/staff | — | ◎ |
| 7 | `/m/today` | 今日の訪問 | admin/staff | — | ◎ |
| 8 | `/m/week` | 今週 | admin/staff | — | ◎ |
| 9 | `/m/me` | マイページ | admin/staff | — | ◎ |
| - | (グローバル) | AI入力 / FAB | admin/staff | input/recording/reviewing | ◎ |

ルーティング戦略:
- `app/(auth)/login/page.tsx` (フルスクリーン、AppShell 適用外)
- `app/(app)/dashboard|weekly|master|integration/page.tsx` (AppShell 配下)
- `app/(mobile)/m/home|today|week|me/page.tsx` (MobileShell 配下)
- ミドルウェアで UA ベースに `/dashboard` → `/m/home` 自動リダイレクト

## 3. 依存関係

### D1 Backend API への依存
| API | 利用画面 |
|---|---|
| `/api/auth/callback/credentials` | ログイン |
| `/api/visits/summary?week=` | ダッシュボード |
| `/api/visits/unassigned?week=` | ダッシュボード/週ビュー |
| `/api/kaipoke/status` | ダッシュボード/連携 |
| `/api/alerts` | ダッシュボード |
| `/api/activity?limit=` | ダッシュボード |
| `/api/visits?week=` | 週ビュー |
| `/api/patients/{id}/weekly-pattern` | 患者固定枠モーダル |
| `/api/patients/{id}/special-week?week=` | 患者固定枠モーダル |
| `/api/staff/{id}/weekly-overrides?week=` | スタッフ休みモーダル |
| `/api/staff/{id}/shift` | スタッフ固定シフト |
| `/api/allocate?week=` | 週ビュー |
| `/api/visits/{id}` | 週ビュー/訪問詳細 |
| `/api/patients` (CRUD) | マスタ |
| `/api/staff` (CRUD) | マスタ |
| `/api/offices`, `/api/cities` | マスタ |
| `/api/geocode` | マスタ |
| `/api/ai/interpret` | AI入力 |

### D2 Foundation 共通コンポーネント（再利用前提・再開発禁止）
- レイアウト: AppShell, Sidebar, Header, MobileShell, BottomNav
- 表示: Card, Section, Badge (tone 11種)
- 入力: Input, Textarea, Select, Switch, Checkbox, Radio, SegmentedControl
- フィードバック: Toast, Spinner, Skeleton, ConfirmDialog
- モーダル: ModalShell (width可変, ヘッダ/ボディ/フッタ)
- アバター: Avatar (24/32/54px)
- アイコン: lucide-react
- トークン: `--brand-primary` `#0D9488` 他、CSS変数

### D4 Integrations 呼び出し IF
- 連携センター画面が D4 のジョブ/差分APIを叩く
- 進捗ストリーム受信 (Phase 2 拡張で WebSocket / SSE)
- VNC: iframe 埋め込み

## 4. タスク分解 (画面単位)

### Phase A: 基盤・グローバル (2.5日)

A-1. ルーティング & ミドルウェア (0.5d) — 3 Route Group + 認証/ロール/UA リダイレクト
A-2. TanStack Query Provider (0.25d) — staleTime 30s
A-3. API クライアント基盤 (0.5d) — fetch ラッパ + 401リトライ + Zod
A-4. AI入力 FAB (0.5d) — 60×60 / radius 50% / gradient teal / Sparkles 22 / Cmd+K リスナ
A-5. キーボードショートカットフック (0.25d) — Cmd/Ctrl+K, Cmd+Shift+V, Esc, Cmd+Enter
A-6. 権限ガード/ロール判定 (0.5d) — `<RoleGate roles={['admin']}>`

### Phase B: ログイン (1日)

B-1. ログインページ (1d) — Card 400px、ハートロゴ + Serif、メール/パスワード/Remember me、NextAuth signIn

### Phase C: ダッシュボード (1.5日)

C-1. ダッシュボード + ヘッダー (0.25d) — Serif 28 + 「川原様、おかえりなさい」+ 日付チップ
C-2. SummaryCard (0.5d) — title / iconChip 32×32 / Serif 38 (tnum) / dot サブ、admin 3枚 / staff 1枚
C-3. AlertsCard + QuickActionsCard (0.5d) — アラート行 + 2×2クイック (primary 1個)
C-4. RecentActivityCard (0.25d) — grid 68/1fr/200/100、5件 + 「すべて表示」

### Phase D: 週ビュー (中核・5日)

D-1. 週ビューページ + データフェッチ (0.5d) — ISO週解釈 + 並列フェッチ
D-2. WeeklyToolbar (0.5d) — prev/next/今週 + Segmented + FilterBox + 自動割当
D-3. ScheduleGrid (1d) — `60px repeat(7,1fr)` / 30分刻み / sticky / 12:00休憩
D-4. VisitChip (0.5d) — 5色 + 3pxアクセントバー + フラグ + DnD draggable
D-5. モード別ビュー (1d) — integrated/staff/patient
D-6. UnassignedArea (0.5d) — Table grid + 「手動割当/再割当」
D-7. DnD インテグレーション (1d) — dnd-kit + 楽観更新 + NG時間拒否 + 同住所確認

### Phase E: 編集モーダル群 (3日)

E-1. PatientWeeklyPatternModal (1d) — Segmented + 7×8 グリッド + null/fixed/am/pm/all + 長押し
E-2. StaffWeeklyOverrideModal (0.75d) — 7日カード + 状態循環 + AI入力ボックス
E-3. StaffShiftModal (0.5d) — Table + Switch + Mono 時刻 input
E-4. VisitDetailModal (0.75d) — 詳細表示 + インライン編集 + キャンセル/延期/削除

### Phase F: マスタ管理 (3日)

F-1. マスタページシェル + Tabs (0.25d) — 4タブ + URL param
F-2. MasterListPanel (0.5d) — Card + 検索 + 一覧アイテム
F-3. PatientDetailForm (1d) — 6セクション + RHF + Zod + 住所→緯度経度自動 + 未保存遷移警告
F-4. StaffDetailForm (0.75d) — 6セクション + シフト編集ボタン
F-5. Office/City/AddCityDialog (0.5d) — chips + チェックボックス選択ダイアログ

### Phase G: 連携センター (4日)

G-1. ページシェル + 6カード (0.25d)
G-2. StatusCard (0.5d) — 4カラム + StatCell + 1分ポーリング
G-3. ActionCard (0.5d) — 月Select + 5ボタン + 確認モーダル
G-4. ProgressCard (0.5d) — 2-3秒ポーリング + バー + 中断
G-5. VncCard (0.75d) — 折りたたみ + iframe (Bearer Token) + 別ウィンドウ
G-6. DiffPreviewCard (1d) — Table + チェックボックス + 種別バッジ + debounce 500ms PATCH
G-7. JobHistoryCard (1d) — アコーディオン + 失敗時 [手動対応済] + スクショ + メモ

### Phase H: AI入力モーダル (2日)

H-1. AiInputModal シェル + state machine (0.5d)
H-2. input state (0.25d) — textarea + ヒント + [録音][送信]
H-3. recording state (0.5d) — Web Speech API ラッパ + 録音中表示
H-4. reviewing state (0.5d) — 元発話 + 構造化 + 信頼度 + 手動修正
H-5. 送信処理 + Toast連携 (0.25d)

### Phase I: モバイル4画面 (3日)

I-1. MobileShell (0.5d) — Header h48 + BottomNav h64 + safe-area
I-2. ホーム (0.75d) — WelcomeCard gradient + 4枚stats + お知らせ
I-3. 今日 (0.75d) — VisitCard 縦並び + borderLeft teal (現在訪問) + pull-to-refresh
I-4. 今週 (0.5d) — DayCard 7枚 + 今日にteal帯
I-5. マイページ (0.5d) — プロフィール + メニュー4項目

### Phase J: 状態・QA・テスト (2日)

J-1. ローディング状態 (0.5d) — `loading.tsx`
J-2. エラー境界 (0.25d) — `error.tsx`
J-3. 空状態 (0.25d) — Empty
J-4. ロール別検証 (0.5d)
J-5. テスト実装 (0.5d)

合計 **27人日**

## 5. 画面ごとの主要コンポーネント階層

### 週ビュー (最大)
```
WeeklyPage
├─ WeeklyToolbar (WeekNavigator + ModeSegmented + FilterBox×2 + 自動割当)
├─ ScheduleCard
│  ├─ StaffTabBar (mode='staff'のみ)
│  └─ DndProvider
│     └─ ScheduleGrid (TimeAxis + DayHeader + LunchOverlay + Cell × VisitChip)
└─ UnassignedArea (UnassignedHeader + UnassignedTable)
```

### 連携センター (6パネル)
```
IntegrationPage
├─ StatusCard
├─ ActionCard
├─ ProgressCard (実行中のみ)
├─ VncCard (Collapsible)
├─ DiffPreviewCard (DiffHeader + DiffTable × DiffRow)
└─ JobHistoryCard (Filter + JobRow × JobItemRow + Pagination)
```

## 6. データフェッチ方針 (TanStack Query)

### キャッシュキー命名
```
['<domain>', '<resource>', { ...params }]
```

### 主要キー
| キー | staleTime | refetchInterval |
|---|---|---|
| `['dashboard', 'summary', { week }]` | 30s | 1分 |
| `['kaipoke', 'status']` | 30s | 1分 |
| `['weekly', 'visits', { week, mode, filters }]` | 30s | — |
| `['patient', id, 'weekly-pattern']` | 5分 | — |
| `['staff', id, 'shift']` | 5分 | — |
| `['integration', 'job', jobId]` | 0 | 2-3秒 (実行中のみ) |
| `['integration', 'correction-sheet', 'latest']` | 60s | — |

### 楽観的更新（週ビュー DnD）
```
onMutate: cancelQueries → previous = getQueryData → setQueryData (新位置)
onError: rollback
onSettled: invalidateQueries
```

### Invalidate
- AI入力で訪問追加 → `['weekly', 'visits']`, `['mobile', 'today']`
- マスタ患者追加 → `['patients']`
- 差分適用完了 → `['integration', 'jobs']`, `['integration', 'correction-sheet']`

## 7. テスト方針

### Vitest + Testing Library
- VisitChip / SummaryCard / WeekNavigator / ScheduleGrid / 各モーダル / DiffPreviewCard / AiInputModal の振る舞い

### Playwright E2E
| シナリオ |
|---|
| ログイン (正/誤) + Remember me |
| ダッシュボード (admin/staff差分) |
| 週ビュー DnD + モード切替 |
| マスタ患者CRUD + 未保存遷移警告 |
| 差分プレビュー + 適用 → 進捗 |
| AI入力 (Cmd+K → テキスト → 解釈 → 登録) |
| モバイル iPhone viewport |

### アクセシビリティ
- axe-core を E2E に組み込み

## 8. 受入基準

各画面に対して具体的な完了条件（10〜20項目）を設定。代表例：
- **週ビュー**: 3モード切替 / グリッド 60px×7×30分 / 12:00休憩 / VisitChip 5色 / DnDで時間変更 / NG時間拒否 / 同一住所2件目確認 / 60fps 維持
- **連携センター**: 6カード表示 / VNC折りたたみ / 進捗2-3秒ポーリング / 差分全選択/失敗のみ / ジョブ履歴 [手動対応済] チェック
- **AI入力**: FAB or Cmd+K で開く / 音声リアルタイム文字起こし / 信頼度バッジ / 手動修正モード / 複数アクション解釈

## 9. リスク + 対策

| Risk | 対策 |
|---|---|
| 週ビュー DnD パフォーマンス低下 | transform GPU加速、dnd-kit Modifiers、ドラッグ中 pointer-events: none、React.memo + 仮想化 |
| 大量訪問 (200+/週) でTTI遅延 | Cell単位 React.memo、必要なら react-window |
| VNC iframe CSP拒否 | kaipoke-api 側 frame-ancestors 許可、フォールバックでターミナル風プレビュー |
| iOS Safari の Web Speech API 制約 | userActivation 必須、非対応時 fallback、PWA standalone モード検証 |
| 楽観更新 / WebSocket 競合 | mutationKey で排他、最新 updated_at 優先 |
| 長押し検知 (450ms) 誤動作 | pointer-events API 統一、touchAction: manipulation |
| 特別週⇄通常週切替時のデータ混在 | Segmented切替で確認モーダル、別 query で読み込み |
| AI解釈失敗 / 低信頼度 | 信頼度バッジ + 警告、手動修正常時提示、元発話必須表示 |
| iOS PWA キーボード覆い隠し | dvh、visualViewport API、FAB 非表示 |
| 未保存遷移時のデータロスト | useRouter 遷移インターセプト、beforeunload、dirty 監視 |
| ロール分岐漏れ | RoleGate 必須化、サーバ側再チェック、E2E両ロール検証 |
| D2 仕様変更の波及 | リリース後ロックバージョン、互換性検証 |

## 10. 想定工数

| Phase | 内容 | 工数 |
|---|---|---|
| A | 基盤・グローバル | 2.5d |
| B | ログイン | 1d |
| C | ダッシュボード | 1.5d |
| D | 週ビュー | 5d |
| E | 編集モーダル (4本) | 3d |
| F | マスタ管理 | 3d |
| G | 連携センター | 4d |
| H | AI入力モーダル | 2d |
| I | モバイル4画面 | 3d |
| J | 状態・QA・テスト | 2d |
| **合計** | | **27人日** |

### 並列化シナリオ

| 体制 | 期間 | クリティカルパス |
|---|---|---|
| 1人 | 27営業日 (約5.5週) | 直列 |
| 2人 | 16営業日 (約3週) | 週ビュー単独で5日 |
| 3人 | 12営業日 (約2.5週) | 週ビュー DnD + 連携⑤⑥ + AI入力 |

### マイルストーン
- M1 (Day 5): ログイン+ダッシュボード+ナビ → デモ可能
- M2 (Day 12): 週ビュー読み取り+マスタCRUD → 閲覧可能
- M3 (Day 18): 週ビューDnD+AI入力+モーダル4本 → 編集ワークフロー完結
- M4 (Day 23): 連携センター+モバイル4画面 → フルスコープ
- M5 (Day 27): テスト+QA → リリース可能
