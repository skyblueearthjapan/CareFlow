# D3 Screens クロスレビュー A（技術観点）

**VERDICT: ACCEPT-WITH-RESERVATIONS**

## 1. 総評

D3計画は設計MDの全画面・モーダル・モバイルを高い網羅性でカバーし、タスク粒度・再利用方針・キャッシュ設計ともに実務的。ただし、API パス不一致が複数、訪問詳細モーダル仕様の欠落、DnD の iOS Safari 対策の薄さ、D1 `override_type` enum と UI 状態マッピングに齟齬。いずれも実装前に解消可能で構造的欠陥ではない。

## 2. Major Findings

### [MAJOR-1] D3 と D1 の API パス不一致が複数
- D3 は `GET /api/visits/today` 等で `/api/v1/` prefix が欠落、D1 は `/api/v1/...`
- D3 は `/api/geocode`、D4 は `/api/geocoding/forward` で異なる
- → 実装初日に 404 連発、修正コスト跳ねる
- **Fix**: §3 依存表を `/api/v1/` で統一、`/api/geocode` を `/api/v1/geocoding/forward` に修正

### [MAJOR-2] D1 の `staff_weekly_overrides.override_type` enum vs D3/設計 UI 状態の齟齬
- D1: `enum('off','custom_time')` 2値
- D3/設計 06: クリック循環 `on → off → half-am → half-pm → on` 4値
- → 午前休み・午後休みの永続化方法不明
- **Fix**: D1 enum を `('off','half_am','half_pm')` 拡張 or `custom_time` + start/end のマッピング明記

### [MAJOR-3] 訪問詳細モーダル (E-4 VisitDetailModal) のワイヤーフレーム・フィールド仕様が設計MDに不在
- `06-screen-weekly-view.md` 187 行で「02-common-components の AI承認モーダル相当 + 編集情報」とあるが、ワイヤーフレームなし
- 他のモーダル4本は全て設計MDに詳細あるのに、訪問詳細だけ仕様なし
- **Fix**: 06-screen-weekly-view.md に訪問詳細モーダルのワイヤー追加、または D3 内に暫定仕様

## 3. Minor Findings

1. §1「モーダル5つ」と §11「4モーダル」で数の不一致
2. `['weekly', 'visits']` の refetchInterval なし、複数管理者 DnD の同時更新が反映されない
3. Phase D D-7 (DnD) の 1日工数が攻めすぎ（楽観更新+NG拒否+同住所確認で 1.5d 推奨）
4. AI入力 H-1〜H-5 で error state, multi state のタスクが切り出されていない
5. 連携センター G-6 の grid columns 定義が設計MDと不整合

## 4. Missing / Edge Cases

- iOS Safari での dnd-kit タッチ DnD 制約（300ms 長押し、scroll/drag 競合、`touch-action: none`）が言及なし。タブレット利用シナリオ不明
- D1 `/api/visits/today` がD3 §3 依存表に未記載（モバイル I-2/I-3 で必要）
- pull-to-refresh の Next.js + PWA 実装方法が「一言」のみ
- 通知ベル実装が D3 タスクに含まれていない（A-1, C-1 にもなし）
- D2 `/m/this-week` vs D3 `/m/week` のパス名不一致
- Cmd+Shift+V → recording state の連携が不明

## 5. Ambiguity Risks

- D-3 ScheduleGrid の時間範囲（09:00-18:00 か 08:00-20:00 か）未明記
- E-1 「7×8 グリッド」の「8」が時間スロット数か行数か曖昧

## 6. 依存ドメイン整合性

| 依存先 | 整合状態 | 備考 |
|---|---|---|
| D1 API パス | **要修正** | `/api/v1/` prefix 欠落、geocode パス不一致 |
| D1 データモデル | **要確認** | `override_type` enum と UI 4状態 |
| D2 コンポーネント | 良好 | 再利用リスト正確 |
| D2 ルーティング | **要修正** | `/m/this-week` vs `/m/week` |
| D4 連携 API | 概ね良好 | Geocoding パスのみ不一致 |
| D4 VNC/CSP | 良好 | iframe + Bearer + frame-ancestors 認識一致 |

## 7. 再レビュー推奨ポイント

1. D1/D4 との API パス最終同期後、§3 依存表再検証
2. 訪問詳細モーダル仕様追加後、E-4 工数妥当性
3. DnD 実装着手後の 60fps 達成可否と react-window 導入判断
4. iOS Safari での dnd-kit タッチ操作テスト（iPad 含む）
5. `override_type` enum 拡張方針確定後、E-2 詳細

**Verdict Justification**: CRITICAL なし、MAJOR 3件は同種の整合性問題で、実装着手前 1-2時間の同期作業で解消可能。REJECT ではなく ACCEPT-WITH-RESERVATIONS。
