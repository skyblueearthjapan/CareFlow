# 引き継ぎ書：2026-07-08b セッション総合（T-4/T-6パリティ完遂・モニター刷新M-4・RBAC UI統一）

作成 2026-07-08 / **本番 HEAD = `8749c6a`（backend+frontend デプロイ済み）** / DB = **migration 0058（本セッションでDB変更なし）** / healthz 内外とも正常。
**次のエージェントはまずこのファイルを読む。** 前セッション正典 `docs/plans/session-2026-07-08-HANDOFF.md` の後継。

関連正典（順に読む）:
- `docs/plans/schedule-timeline-redesign-design.md` — タイムライン刷新の全体計画（T-0〜T-6）
- 自動メモリ `careflow-timeline-redesign.md` / `careflow-monitor-ui-unification.md` / `careflow-rbac-ui-unification.md` — 本セッションの詳細・教訓集

体制は全コミットで「実装 → 独立 code-reviewer → 全指摘反映 → デプロイ → healthz」（PO反復時のみレビュー並行・追いデプロイ）。

---

## 0. TL;DR — このセッションで何をしたか（20コミット・全て本番稼働）

| commit | 内容 |
|---|---|
| `6f19d4f` | **R-1根治**: 日TL下端切れの真因 = 曜日タブパネルの flex 高さチェーン欠落（app-vh ではなかった） |
| `e69363d` | **A-3 (T-4) 完了**: 提案系4画面+一括投入をカード視覚言語へ（TimelineRow optional メタ・FE join） |
| `bb3990f` | T-4追随: プール候補ミニスケジュール（MiniRow×4）もカード化 |
| `fcf02e4` | **性別ウォッシュ実データ結線（BE込み）**: mini_schedule に BE から sex 加算・一括投入は FE join |
| `3d4cc1f`〜`dea018b` | **CornerPushPin**: ピン留め=カード右上に打ち込む画鋲（はみ出し配置・ペア行サイズ統一） |
| `87c801d` | **T-6パリティ①**: プールカード→タイムライン直接ドロップ（仮想セル合成で既存フロー流用） |
| `9b2e5ea` | **T-6パリティ②**: イベント帯のDnD移動（案X/Q/K流用・クリック編集と共存） |
| `70ac7b6` | **D-1完了**: 複数スタッフへ打合せ一括登録（在籍全スタッフから複数選択・部分失敗=ID追跡） |
| `0c763cd` | **T-6パリティ③**: 同住所ペアの1名ずつ個別ドラッグ（⠿ハンドル・tl-visit名前空間流用） |
| `eed070e`〜`b70a606` | イベント帯PO反復: 情報増強（高さ段階表示）・grab/grabbingカーソル・種別前置廃止・バッジ3段目専用 |
| `95e0783` | **M-4a+b（BE込み）**: モニターをカード視覚言語へ（予定=性別ウォッシュ2行カード・実績=状態色レール不変・イベント藤色帯） |
| `3aa3b71`〜`f46bce7` | **M-4c**: 時間軸216px/h固定+横スクロール（35分枠でフルネーム）・スタッフ列sticky・「今」自動スクロール・スクロールバー最終行直下・時刻バードラッグパン |
| `8749c6a` | **RBAC UI統一（BE込み）**: PC版「全ロール同一表示・操作は権限どおり」+申請履歴middleware矛盾修正 |

**PO決定事項（本セッション確定・覆さないこと）**:
1. **T-5 (A-4) スキップ**: 「モバイル版は現状のままにて好評」→ 現場側UI（モバイル/現場ボード）の意匠統一は行わない
2. **モニターは横レイアウト維持**: 縦時間軸案は却下（列=スタッフ数で「全員×今」の一覧性が壊れる）。横スクロール+固定スケールで解決
3. **RBAC UI**: 「全ロール同一表示・操作は権限どおり(disabled)」+ admin限定センシティブ（監査ログ/ユーザー管理/カイポケ連携/削除/全件置換）は非表示維持。**BEの書込みRBACは不変**（閲覧GETのみ staff 開放: /monitor・/monitor/nearby・/checkin-settings）

---

## 1. ⚠️ PO実機確認待ち（次エージェントの初手・回答が来たら即対応）

本セッションは高速反復のため、以下が**まとめて実機確認待ち**:

1. **staffアカウントでのRBAC統一確認**（最重要・8749c6a）: スケジュール/モニターが admin と同一構成か・disabledボタンの見え方・申請履歴が manager で開けるか
2. モニター M-4 一式: 予定カードの性別色/📍住所・実績レールの読みやすさ・イベント藤色帯・横スクロール/時刻バードラッグ/「今」自動スクロール・スクロールバー位置
3. スケジュール: T-6パリティ3点（プール直接ドロップ/イベント帯DnD/ペア⠿個別ドラッグ）・複数人打合せ登録・イベント帯の3段表示とバッジ位置
4. CornerPushPin: はみ出しピンの意匠合格・**リスト行の赤ピン2重表示（名前隣トグル vs 右上状態ピン）の是非** ← PO判断保留中
5. 前々回分: 日TL 18:00まで届くか・週TL下端・ペア住所重複表示 (R-2)
6. **運用**: 性別が砂色の患者 = 患者マスタ性別未登録 → PO側で入力すると色が出る

## 1.5 追記（2026-07-08 同日・引き継ぎ書作成後の追加作業）

- **統一パスワード完了**: 全9アカウントを PO 指定の共通パスワードに統一（DB直更新・コード変更なし・
  must_change 全OFF・3ロールでログインAPI 200確認・バックアップ `pre-password-unify-20260708-133616.sql`）。
  詳細=メモリ `careflow-unified-password.md`
- **ログイン手順書改訂完了** (`4e2ccf4`): 利用マニュアル・クイックガイドを共通パスワード運用+RBAC統一に
  全面改訂。**配布カード staff-login-cards.html は gitignore=ローカルのみ**（共通PW版に刷新済み）

## 2. 残タスク（★徹底列挙・忘れず引き継ぐ）

### 【PW. パスワード変更/リセット経路の締め付け（PO「別途対応したい」・方式選択待ち）】
背景: 共通パスワード運用が始まったが、それを崩す経路が残っている。
- **① admin ユーザー管理のパスワードリセットボタン: 確認ダイアログなし1クリック即実行** ← PO懸念の本丸
- ② Header「パスワード変更」メニュー: 全ロールが自己変更可能（現在PW必須なので誤操作リスクは低い）
- 提案済み選択肢: (a)リセットに確認ダイアログ (b)変更メニューをadmin限定 (c)両方=推奨。PO指示待ち
- 実装すればマニュアル6.1/6.2の運用注意書きも簡素化できる

### 【A. タイムライン刷新の続き】
- **A-5 (T-6) 撤去+既定整理** — テーブル(CourseDayTable)・旧週ビュー(CourseWeekOverview)削除・Panel減量。
  条件 = パリティ全達成 + 現場数週間運用 + PO承認。
  - 機能パリティ①②③は**本セッションで完遂** ✅
  - **残④: 残項目の正式棚卸し**（担当割当レビュー・全ダイアログ起動等の最終チェックリスト作成 — ドキュメント作業）
  - **残⑤: 週の「一覧 vs 週TL」2枚持ちの畳み方**（PO相談事項）
  - 日/週タブの既定タイムライン化の最終判断（現状: 日=TL既定/週=一覧既定）

### 【RB. RBAC UI統一の第2弾】
- **スタッフ詳細 (`staff/[id]`) のセクション内 canEdit ボタン群（約10箇所: シフト/イベント等）の disabled 化**（本体は完了・ここだけ hidden 方式が残存）
- 患者/スタッフ new・edit ページの直アクセスガードは現状維持で良い（ボタン disabled で到達しない設計）
- ScheduleReviewBanner は canEdit 限定のまま（通知的・恒常構造でないと判断）— PO から指摘があれば再検討

### 【B. カイポケ逆反映の運用開始（PO監督必須・エージェント単独では進められない）】
- **B-1. 初回 実apply（dry_run=false）** — 未実施。**済むまで逆反映取り込みは全週無効**。
  PO/現場監督・noVNC監視・10月サンドボックス。詳細 `docs/plans/kaipoke-rpa-revival-HANDOFF.md` §7
- **B-2. 要手当データ**: 髙梨桂子(staff未登録)・槇恵(patient未登録)の登録or名寄せ・全看護師職種backfill
- **B-3. 適用後検証（apply後再export→1件ずつ照合）の実装**

### 【C. その他・低優先（前回から継続）】
- C-1. GEMINI APIキーのGoogle側失効（ユーザー操作待ち）
- C-2. カイポケ側パスワードローテーション（PO検討中）
- C-3. **untracked の `docs/HANDOFF.md`・`CareLink-handoff.zip` のコミット可否確認（今も未処理・git statusに残存）**
- C-4. 申請・承認基盤（pending_requests・本番0行）の要否棚卸し（今回 manager アクセス矛盾は修正済み）
- C-5. RPA側 .env の KAIPOKE_* 3キー削除（安定運用後）

### 【D. 技術負債・バックログ（小粒）】
- D-1. ~~イベントの複数スタッフ一括登録~~ → **本セッションで完了** ✅ (70ac7b6)
- D-2. patient/staff 型へ `sex` を正式追加（`as`キャスト散在。今回一部改善したが型負債は残る）
- D-3. ペア移動のBEバッチ化（現状 visit-move-week-only×2連続・undo回復可）
- D-4. Panelテストの Provider 未ラップ既存fail 20件（**今回モック大幅増強で解消に近い**: QueryClientProvider+不足モック7種のパターンは T-2S/T-6P1/T-6P2 で確立済み。既存20件は古い期待値の見直しも必要）
- D-5. 30分カードの住所行の高さ余裕（PO指摘があれば py 調整）
- D-6.（新）mini_schedule に patient_id 加算案 — 現状プール候補ミニ行は sex はBE加算済みだが住所ウォッシュ不可。必要になったら sex と同じ前例パターンで
- D-7.（新）患者500名超で提案系/モニターの性別色が中立化（PATIENT_LIST_HARD_CAP 500・現状実害なし）
- D-8.（新）日TLレーン分割時にはみ出しピン頭が隣カードに僅かに隠れる稀ケース（実害あればピン留めカードを z-[3] へ）
- D-9.（新）モニターに常時可視のカスタム横スクロールバー案（PO希望が出たら）

---

## 3. 新設計資産（次エージェントが再利用すべき部品・パターン）

- **カード視覚言語の適用面が完成**: スケジュール（日/週TL・日リスト・週一覧・プール）+ 提案系4画面 + 一括投入 + **モニター**。実装源: `lib/scheduling/timeline.ts` (genderPalette)・`components/ui/push-pin.tsx` (**CornerPushPin**)・inline style var(--sched-*)
- **仮想セル合成パターン**（T-6パリティ①②で確立）: tl-col ドロップ→snapYOffsetToMinutes→仮想セル{weekday,courseTemplateId,time}→`const cell = poolCell` シャドーイングで既存テーブルフローへ無変更合流
- **TimelineEventAddDialog** + `useCreateEventForStaff`（staffId=variables・全体invalidate）: 複数スタッフ一括イベント
- **MonitorTimeline 新レイアウト**: PX_PER_HOUR=216・LANE 66px=予定カード40px+実績レール14px・sticky スタッフ列・時刻バーpan・「今」自動スクロール
- **Panelテスト新パターン**: QueryClientProvider ラップ + lucide Proxy モック（'then'ガード必須）+ 不足フックモック群 — T-2S/T-6P1/T-6P2 参照

## 4. 教訓（今回追加・厳守）

1. overflow-hidden→h-full 委譲の高さチェーンは**全中間ラッパーに flex 連鎖が必要**（1つ素の div が挟まると h-full が auto 化 = 下端切れの真因）
2. vi.mock の Proxy フォールバックは **'then' に関数を返すと thenable 誤認で await import が永久ハング**。コンポーネントはキャッシュで識別性安定
3. 意匠を入れてもデータ流路に sex が無いと全行中立色 — **BE レスポンス毎に sex 有無を確認**（FE join か BE 加算かを画面毎に判断）
4. angular装飾のはみ出しは「overflow-hidden除去+各要素truncate」。**行あたり固定幅要素は1つ・残りは truncate**（バッジ+tnum時刻を同行に並べない）
5. ハンドルの伝播止めは「**activator を呼んでから stopPropagation**」。Capture 側は React 合成イベントで同一要素の bubble ハンドラまで止める
6. useWeekStaffEvents 等に渡す Date は **Date.UTC で構築**（ローカルTZ構築だと toISOString で前日にズレる）
7. 再レンダー毎に呼ばれるモックに mockReturnValueOnce は不適（恒常設定+テスト末尾で既定復元）
8. グループ無効化は pointer-events-none でなく **inert**（キーボード/支援技術も遮断）
9. staff へ閲覧開放する画面は **BE の GET require_role を先に確認**。書込みRBACは絶対に緩めない
10. Radix Select は jsdom で ResizeObserver 必須（最小ポリフィル）

## 5. デプロイ状態

- 本番 = リポジトリ HEAD 一致（`8749c6a`）。backend+frontend とも再ビルド済み。DB migration 0058（本セッション DB 変更なし）
- デプロイ手順・規約は従来どおり（pg_dump→pull→build→recreate→healthz内外。--no-verify 禁止・日本語ファイルは Edit ツールのみ・prettier と vitest は別コマンド）
- Panelスイートの基準値: **20 fail（QueryClient 既存・D-4）/ 6 pass** — 変更時は base±0 を維持すること
