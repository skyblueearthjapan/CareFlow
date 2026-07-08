# 引き継ぎ書：2026-07-08 セッション総合（T-2触れる化 完了／タイムラインUI大量改善／スクロール構造刷新）

作成 2026-07-08 / **本番 HEAD = `e484cb5`** / DB = **migration 0058（変更なし）** / healthz 内外とも正常。
**次のエージェントはまずこのファイルを読む。** 前セッション正典 `docs/plans/session-2026-07-07-HANDOFF.md` の後継。

関連正典（順に読む）:
- `docs/plans/schedule-timeline-redesign-design.md` — タイムライン刷新の全体計画（T-0〜T-6）
- `docs/mockups/timeline-comparison-mock.html` — ピクセル仕様の正
- 自動メモリ `careflow-timeline-redesign.md` — 本セッションの詳細メモ・教訓集

---

## 0. TL;DR — このセッションで何をしたか（すべて本番デプロイ済み・21コミット）

**A-2（T-2 触れる化）を完遂**し、その後は PO 実機フィードバック（A-1）を高速反復。
体制は全コミットで「実装 → 独立 code-reviewer → 全指摘反映 → デプロイ → healthz」を踏襲
（緊急のPOブロック時のみレビューをデプロイと並行し、指摘は追いデプロイ）。

| commit | 内容 |
|---|---|
| `5564364` | 日リスト縦積みバグ根治（行gridに列テンプレ未適用が縦長の正体）＋高さ圧縮 |
| `418f8aa` | 週一覧に性別ドット追加 |
| `f70c821` | **T-2②-a** 空き枠クリック→登録モーダル（訪問=place-and-fix／イベント=EventAddDialog流用） |
| `20bae1c` `1b55172` | イベント帯を担当者の列内表示化＋クリックで編集/削除（全幅帯の「全員に入った」誤読を根治。誤登録1件はDB削除済み） |
| `04a8531` | **T-2②-b** カードDnD移動（15分スナップ・別コース可）＋ChangeScopeChoice二択 |
| `6e4a6d6` | **T-2②-c** ピンshake拒否＋トースト「元に戻す」 |
| `64acdef` | **重要バグ修正**: onPointerDownがdndリスナーを後勝ち上書きしドラッグ死亡→単一spread合成＋実ドラッグ回帰テスト |
| `0089520` | ドラッグ=カード実寸ゴースト／🔒→PushPin統一／プールカード性別ウォッシュ化 |
| `c452aed` | 📍住所行／プールゴースト全情報（PatientCard ghost）／**週タイムライン全コース縦積み**／週ヘッダ性別アバター／週ペア90分ボックス／**同住所ペア2名セットDnD** |
| `49422b1` `2da389c` | 住所30分カードから／日週カード情報統一／sticky固定（→後に撤去） |
| `c662660` | **スクロール構造刷新**: sticky撤去→lg以上はページ非スクロール（h-full flexチェーン）＋左ペイン/プールの内部スクロール |
| `a3eb824` `e70b084` | ペア情報充実＋**日リスト/週一覧の行をカードUIへ統一**（性別ウォッシュ・左帯） |
| `360e319` | 日タイムラインの列ヘッダ固定（盤面内部スクロールへ委譲） |
| `4ea08cb` | **日タブ既定=タイムライン**（週タブ既定=一覧のまま） |
| `382d51e` | 週一覧の曜日ヘッダ行を固定＋本体内部スクロール |
| `40b6c7a` `5b86ca6` | 下端切れ対策: 100dvh→**JS実測innerHeight（--app-vh）方式**＋grid-rows-[minmax(0,1fr)]＋週TL pb-6 |
| `e484cb5` | 同住所ペアの各カード行に📍住所（重複記載・PO明示指示） |

---

## 1. ⚠️ 未確認・進行中（次エージェントの初手）

1. **下端切れ（週タイムラインの縦スクロールが最後まで届かない）の実機確認待ち**。
   dvh対応（40b6c7a）では直らず → `window.innerHeight` をJS実測して `--app-vh` に流す方式へ変更（5b86ca6・AppShell.tsx）。
   **PO確認がまだ**。まだ欠ける場合の切り分け質問を提示済み:
   (a) 欠けるのは画面最下帯全体か週TLの中身だけか (b) ウィンドウをリサイズすると直るか。
   直らなければ候補: WebView固有のUAバグ／lg未満幅でのモバイルレイアウト混入／
   PWAの旧バンドル残留（SWキャッシュ）。
2. **ペアカード住所の重複表示（e484cb5）と直近UI変更全般のPO実機確認**。

---

## 2. T-2「触れる化」完了内容（操作仕様）

- **空き枠クリック**（②-a）: canEditで空き枠が「＋ここに追加」ボタン化→ SlotRegisterDialog。
  プール不足患者を選択→開始15分刻み→**place-and-fix（fix_pattern=false=この週だけ）**。
  トーストに「毎週の型にも登録」昇格＋「元に戻す」。会議イベントは既存 EventAddDialog を
  担当スタッフ宛・時刻プレフィルで流用（**カイポケ反映外**明示）。2名体制患者は候補から除外
  （従来のプールDnD相方コース経路に委譲・注記あり）。
- **カードDnD**（②-b）: tl-visit:/tl-col: 名前空間（既存DnDと完全分離）。列droppable＋
  `snapYOffsetToMinutes`（連続時間軸15分スナップ）。ドロップ後 TimelineMoveDialog
  （**共通部品 ChangeScopeChoice** 必須・既定=この週だけ）。
  week=`useVisitMoveWeekOnly` / pattern=移動＋`promoteWeekToFixed`（=bulkSync・コースまたぎ可）。
  範囲外(9:00-18:00)ドロップは警告拒否・同位置noop。掴めない=ピン/キャンセル/2名体制ペア
  （理由別title・BE 422が二重防壁）。
- **ピン/undo**（②-c）: ピンカードpointerdownで tl-shake 拒否演出。配置/移動トーストに
  「元に戻す」（op-log undo=Ctrl+Zと同一スタック仕様）。
- **同住所ペアの2名セットDnD**: tl-pair:{id1}:{id2}。90分占有で範囲判定・同一op_group_idで
  2件移動（**undo1回で両方戻る**）・昇格も patient_ids 配列で2名一括。
  `promoteWeekToFixed/promoteToastAction` は `(patientIds: string[], label)` に一般化済み。

## 3. UI現況（本番）

- **既定**: 日タブ=タイムライン／週タブ=一覧。テーブル・リスト・週TLはタブ切替で共存（T-6まで）。
- **カード視覚言語の統一**: タイムライン（日/週）・日リスト・週一覧・プールの全面が
  性別ウォッシュ地＋左色帯＋角丸。ピン=PushPin（赤丸頭）。📍住所は30分カードから表示
  （極小はtitle）。週カードは日と同一情報構成（時刻・分/住所/条件ピル）。
  ペアボックス=見出し「同住所 n分占有」＋各行に住所・時刻分・条件。
- **ドラッグゴースト**: カード/ペアボックス/プールカードとも**実寸・全情報**のまま動く
  （DragOverlayは掴んだノード寸法にwrapperを合わせる仕様を利用、h-full/w-fullで充填）。
- **イベント帯**: 担当者の列内のみ表示・クリックで EventEditDialog（編集/削除）。
  カードz-[2] > 帯z-[1]（帯がカードクリックを塞がない）。
- **スクロール構造（lg以上）**: ページ非スクロール。見出し/週セレクタ/ツールバー=固定行、
  左ペインとプール=各自内部スクロール。日TL=盤面内部スクロール（列ヘッダsticky固定）、
  週一覧=カード内部スクロール（曜日ヘッダsticky固定）、
  **週TL=縦積みのままペインスクロール（ヘッダ渋滞回避のためPO指定で固定しない）**。
  モバイル(lg未満)は従来のページスクロール。

## 4. コード地図（本セッションの追加・変更）

- `frontend/components/schedule/timeline/`
  - `TimelineDayBoard.tsx` — VisitCard(drag/shake/住所行)・PairBox(drag)・DraggableVisitCard/
    DraggablePairBox・ColumnDropLayer・TlVisitDragGhost/TlPairDragGhost・
    id規約 tlVisitDraggableId/tlPairDraggableId/tlColDroppableId(+parser)
  - `SlotRegisterDialog.tsx`（新規・②-a）／`TimelineMoveDialog.tsx`（新規・②-b二択）
  - `WeekTimelineBoard.tsx` — 全コース縦積み(CourseWeekSection×N)・staffByWeekday={name,sex}・
    buildWeekRenderItems/WeekPairBox・WTL_DEFAULT_SERVICE_MIN=35
  - `TimelineDayList.tsx` — カード行化・PushPin
- `frontend/lib/scheduling/timeline.ts` — snapYOffsetToMinutes・TL_SHOW_ADDR_PX=46
- `frontend/components/schedule/v2/`
  - `CourseDayTablePanel.tsx` — slotReg/tlMove/tlEventEdit/activeTlVisit/activeTlPairVisits/
    activePoolCard(+poolCardDataRef) の各state・handleDragEnd の tl-visit/tl-pair 分岐・
    スクロール委譲の条件分岐・visitMoveWeekOnlyMut
  - `PatientCard.tsx` — sex(性別ウォッシュ・後方互換)・ghost モード
  - `CourseWeekOverview.tsx` — WeekOverviewVisit に same_address_key/patient_address 追加・
    性別ドット・カード行化・曜日ヘッダsticky・内部スクロール
- `frontend/components/AppShell.tsx` — **--app-vh（innerHeight実測）**
- `frontend/app/(app)/staff/[id]/_components/EventAddDialog.tsx` — defaultStart/End props（後方互換）
- `frontend/app/(app)/schedule/page.tsx` — lg:h-full flexチェーン

## 5. 教訓（規約追加・厳守）

1. **dnd-kit の listeners spread の後に同名プロップ（onPointerDown等）を書くと undefined でも
   後勝ち上書きでドラッグが死ぬ**。合成は単一spreadで。回帰テストは「DndContext の onDragStart
   発火まで」検証（jsdomは MouseEvent を pointerdown 偽装＋isPrimary defineProperty）。
2. `display:grid` に grid-template が無いと1列積み（日リスト縦長の正体）。jsdomでは検出不能→
   className に `grid-cols-[` を要求する回帰テストで防ぐ。
3. **sticky は「張り付くまで一緒に動く」**→固定UIはページ非スクロールの固定行で作る（この画面の正解）。
4. flex/grid の内部スクロールは `min-h-0` 必須＋**gridは `grid-rows-[minmax(0,1fr)]` が無いと
   行トラックが内容高で伸びてはみ出す**。
5. WebViewは 100vh/100dvh が実寸と乖離しうる→ `window.innerHeight` 実測の --app-vh が正。
6. inline style は hover: 含む class に勝つ→性別ウォッシュと警告赤/ピン琥珀の共存は
   inline background を条件分岐で外す。
7. ResizeObserver を早期returnのあるコンポーネントで使うなら callback-ref（useRef+effect[]は取り逃す）。
8. 二択UIは必ず共通部品 ChangeScopeChoice（設計書§2.4）。昇格の意味論は bulkSyncWeekToFixed。
9. 変更スコープ系の慣例: prettier --write と vitest は別コマンド／日本語ファイルはEditツールのみ／
   schedule系vitestはbase比較±0（Panelスイートは既存fail20/pass3）。

---

## 6. 残タスク（★次エージェント最重要・忘れず引き継ぐ）

### 【最優先】直近の確認・詰め
- **R-1. 下端切れの実機確認**（§1。直らなければ切り分け質問の回答から追加調査）
- **R-2. 直近UI変更のPOフィードバック反映**（ペア住所重複表示・カード行の色味/行間など微調整）

### 【A. タイムライン刷新の続き（引き継ぎ書順）】
- **A-3. T-4 提案系の意匠統一** ← 機能開発の次の本命。
  `CourseMoveTimeline`（提案タイムライン・4画面共有）と `WeekdayScheduleCard`（提案ダイアログ側）を
  タイムラインの視覚言語へ。**共有コアのため4〜5画面波及・signature変更は慎重に**。
  executor＋独立レビュー必須。
- **A-4. T-5 現場ボード/モバイルの意匠統一**（データ系統不変・見た目のみ）。A-3の後。
- **A-5. T-6 撤去＋既定整理**（テーブル・旧週ビュー削除／Panel減量）。
  条件=パリティ全達成＋現場数週間運用＋PO承認。
  **T-6パリティの未達（今日時点）**:
  - プールカード→タイムラインへの**直接ドロップ配置**（現状は空き枠クリック→モーダルのみ。
    handleDragEnd の pool 分岐は course-day-cell 前提で tl-col を受けない）
  - タイムライン上の**イベント帯DnD移動**（編集ダイアログでの時刻変更は可・テーブル帯DnDは既存）
  - **同住所ペアの1名ずつ個別ドラッグ**（セットは可。UI区分け案=ペア行に⠿ハンドル、
    行はハンドルから・箱は余白から掴む）
  - 担当割当レビュー・全ダイアログ起動等の残項目の正式棚卸し
  - 週の「一覧 vs 週TL」2枚持ちの畳み方／週TLの既定化判断

### 【B. カイポケ逆反映の運用開始（人間PO監督・別軸）】
- **B-1. 初回 実apply（dry_run=false）** — 未実施。**済むまで逆反映取り込みは全週無効**。
  PO/現場監督・noVNC監視・10月サンドボックス。詳細 `docs/plans/kaipoke-rpa-revival-HANDOFF.md` §7。
- **B-2. 要手当データ**: 髙梨桂子(staff未登録)・槇恵(patient未登録)の登録or名寄せ・全看護師職種backfill。
- **B-3. 適用後検証（apply後再export→1件ずつ照合）の実装**。

### 【C. その他・低優先（前回から継続）】
- C-1. GEMINI APIキーのGoogle側失効（ユーザー操作待ち）
- C-2. カイポケ側パスワードローテーション（PO検討中）
- C-3. untracked の `docs/HANDOFF.md`・`CareLink-handoff.zip` のコミット可否確認（まだ未処理）
- C-4. 申請・承認基盤（pending_requests・本番0行）の要否棚卸し
- C-5. RPA側 .env の KAIPOKE_* 3キー削除（安定運用後）

### 【D. 本セッションで積んだバックログ（技術負債・小粒）】
- D-1. **イベントの複数スタッフ一括登録**（現状1人ずつ。「3人だけの会議」用の複数選択UI）
- D-2. patient/staff 型へ `sex` を正式追加（`as`キャスト散在の型負債・レビューMED記録）
- D-3. ペア移動のBEバッチ化（現状 visit-move-week-only×2連続。部分失敗はundoで回復可能だが
  単一トランザクションが理想。失敗時のop-log即時invalidateは対応済み）
- D-4. Panelテストの Provider 未ラップ既存fail 20件の解消（解消後にDnD統合テスト追加）
- D-5. 30分カードの住所行は高さぎりぎり（9px/leading-tightで対処済み。PO指摘があれば py 調整）

---

## 7. デプロイ状態

- 本番 = リポジトリ HEAD 一致（`e484cb5`）＋本引き継ぎ書コミット。DB migration 0058（本日DB変更なし）。
- デプロイ手順・規約は前回引き継ぎ書 §6 のとおり（pg_dump→pull→build frontend→recreate→healthz内外）。
- PWA自己回復により現場ハードリロード不要（ただし下端切れが「直らない」場合はSW旧バンドル残留も疑う）。
