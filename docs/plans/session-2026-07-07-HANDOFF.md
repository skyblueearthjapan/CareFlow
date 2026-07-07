# 引き継ぎ書：2026-07-07 セッション総合（QR修正／カイポケ双方向化・汎用化／スケジュール縦タイムライン刷新）

作成 2026-07-07 / **本番 HEAD = `0da7abf`** / DB = **migration 0058** / healthz 内外とも正常。
**次のエージェントはまずこのファイルを読む。**

関連正典（このセッションで作成・更新。順に読む）:
- `docs/plans/schedule-timeline-redesign-design.md` — スケジュール縦タイムライン刷新の全体計画（T-0〜T-6）
- `docs/plans/schedule-timeline-production-fit.md` — モックを本番へ落とす適合設計（意匠翻訳・ギャップ）
- `docs/mockups/timeline-comparison-mock.html` — **ピクセル仕様の正**（日/週タイムライン＋リスト＋詳細ホバー）
- `docs/plans/kaipoke-reverse-sync-design.md` — カイポケ→CareFlow 逆反映（R-0〜R-3・§8がR-3）
- `docs/plans/kaipoke-credentials-config-design.md` — ログイン情報のアプリ内設定化（C-1〜C-4）
- 自動メモリ索引 `MEMORY.md` 先頭に本セッションの詳細メモ

---

## 0. TL;DR — このセッションで何をしたか（すべて本番デプロイ済み）

大きく4系統。**すべて独立レビュー→指摘反映→デプロイ→healthz確認**を踏襲。

1. **QR読取の位置ズレ根治**（`8c42b0f`）
2. **カイポケ ⇄ CareFlow 双方向化（逆反映 R-0〜R-3）**（`9190a55`〜`20ad5da`）
3. **カイポケ ログイン情報のアプリ内設定化（汎用化 C-1〜C-4）**＋モニターのCF期限切れ自動復帰（`f8f521d`〜`2049747`）
4. **スケジュール画面の縦タイムライン刷新（T-0〜T-3＋改善多数）**（`dfc903d`〜`0da7abf`）★現在進行中の主テーマ

| commit | 内容 |
|---|---|
| `8c42b0f` | QRスキャナの正方形切り抜き撤去（見える中央≠読取領域のズレ解消） |
| `9190a55` | 逆反映 R-0〜R-2（diff-inbound/apply-inbound・キャンセル表示・apply実績ゲート・migration 0056） |
| `20ad5da` | 逆反映 R-3（スタッフ変更=コース変更・add取り込み・臨時コース「臨」migration 0057） |
| `f8f521d` | ライブモニターのCloudflare Access期限切れ自動検知→ログイン誘導→自動再接続 |
| `480b647` | ログイン情報のアプリ内設定化 C-1/C-2（暗号化保存・接続設定UI・migration 0058） |
| `dfc903d` | タイムライン T-0（レガシー未マウント9ファイル削除＋設計/モック追加） |
| `e92ed0f` | タイムライン T-1（PC日タイムライン・読み取り専用・切替式） |
| `0d3812a` | T-1意匠を本番トークンに適合＋実バグ修正（bg-bg-surface等） |
| `0fae854` | タイムライン T-3（週タイムライン・曜日列・1コース深掘り） |
| `d589266` `07cac28` | 行高拡大（日52px・週52px）＋週の曜日別担当者名表示 |
| `4d43ace` | レーン分割をクラスタ単位に（重なりで単体まで半分化するバグ修正） |
| `0da7abf` | 日リストをモック意匠へ刷新＋**同住所90分ペアの占有表示** |

---

## 1. スケジュール縦タイムライン刷新（★次セッションの主戦場）

### 1-1. 背景と思想（必読）
- スケジュール画面（アプリの顔）を「見えて・触れて・使いたくなる」へ。参考=ZEST型縦タイムライン。
- **貫く原則（PO合意）**: 「**時間はいつも下へ流れる。列は"比べたいもの"を置く**」。
  - 日ビュー: 列=コース（人ごとに比べる）
  - 週ビュー: 列=曜日（一週間を比べる・コースは選ぶ）
  - リスト=精読の相棒（配置が違って当然・残す）
- **最終形 = 「タイムライン系＋リスト」の2つに集約**。テーブル・旧週ビューは移行期の足場→T-6で撤去。
- **絶対条件**: 現行CareFlowの全機能を1つも失わない（各Waveでパリティ確認・共有コアは温存）。
- 思想の正典 `docs/plans/schedule-advisor-design.md` §6（余白の原則）。タイムラインは余白が面積で見える＝思想の可視化。

### 1-2. 本番ツールバー構造（モックと違う・重要）
本番は「**曜日タブ（月 火 水 木 金 土 週）**」＋モード切替。モックの2軸（期間×見え方）とは別構造。
- 日タブ選択時: `[タイムライン｜リスト｜テーブル]`（既定=テーブル据置）
- 週タブ選択時: `[タイムライン｜一覧]`（既定=一覧＝既存 CourseWeekOverview・全機能温存）

### 1-3. 完了済み（本番稼働）
- **T-0**（`dfc903d`）: 本番未マウントの旧UI9ファイル削除（WeekGrid/VisitChip/VisitEditDialog/
  UnassignedList/AllocateRunDialog＋廃止DiffAdd3種＋test）。回帰±0。
  **性別は本番既存の `patient.sex`/`staff.sex`（male/female/unknown）を使用**（新カラム不要と判明）。
- **T-1**（`e92ed0f`＋`0d3812a`）: PC日タイムライン（読み取り専用）。列=コース＋担当アバター・時間比例カード・
  性別色・空き枠・会議イベント帯（カイポケ反映外）・現在時刻ライン・2名重なりレーン分割。
  意匠を本番トークンへ適合（薄塗り・白地・実バグ3件修正）。
- **T-3**（`0fae854`）: 週タイムライン（列=曜日・縦=時間・コースセレクタで1コース深掘り・受入可能数n/N件・
  曜日別担当者名）。**2枚持ち**（俯瞰は一覧＝既存週ビュー・深掘りはタイムライン）。
- **行高拡大**（`d589266`/`07cac28`）: 日30分=52px・週30分=52px。カードは名前1行専有＋時刻/サービス2行目。
- **レーン修正**（`4d43ace`）: 重なりは「クラスタ（連続して重なる塊）ごと」にlaneCountを数える。
  1組ペアがいても重ならない単体は全幅（旧: 列全体一律で半分化していたバグ）。
- **日リスト刷新＋90分ペア**（`0da7abf`）:
  - 日リストを新 `TimelineDayList`（モック意匠の整列グリッド・列見出し・性別ドット＋左色帯・
    コースグループ見出し）へ。**共有コア WeekdayScheduleCard は提案ダイアログ専用に温存**（提案系に無影響）。
    本番の全情報（実動時間・住所・条件・性別制限・同住所・移動警告・ピン・距離・空き枠interleave）保持。
  - **同住所・同時刻2名の90分占有表示**: タイムライン上で1つの琥珀ボックス（大枠）＋上下2段。
    高さ=実占有90分ぶん（SAME_ADDRESS_PAIR_MIN_OCCUPANCY）。「時間が面積で見える」を徹底。

### 1-4. コード地図（タイムライン関連）
- `frontend/lib/scheduling/timeline.ts` — 純関数（時間↔px・性別パレット genderPalette・assignLanes
  クラスタ単位・週用 minutesToYScaled/durationToHeightScaled）。行高定数 TL_ROW_PX=52 / TL_WEEK_ROW_PX=52。
- `frontend/components/schedule/timeline/`:
  - `TimelineDayBoard.tsx`（日タイムライン・PairBox で90分ペア・buildRenderItems）
  - `WeekTimelineBoard.tsx`（週タイムライン・曜日列・担当者名・受入可能数）
  - `TimelineDayList.tsx`（日リスト・整列グリッド・同住所囲み groupSameAddress・警告行強調）
  - `__tests__/` 各テスト（timeline系 計85本 pass）
- `frontend/components/schedule/v2/CourseDayTablePanel.tsx`（中枢・2900行超）:
  - `weekdayViewMode`（table/list/timeline）・`weekViewMode`（overview/timeline）
  - `timelineColumns` / `weekTimelineOptions` / `weekTimelineCapacityByWeekday` /
    `weekTimelineStaffNameByWeekday` / `weekdayListCourses`（TimelineDayList入力）memo
  - `visitsByCourse` builder に patient_sex/start_time/end_time 追加、`overviewVisits` builder にも
- `frontend/styles/tokens.css` — `--sched-*`（性別/イベント/勤務外/now）トークン。**Tailwind未マップ＝inline styleで var() 使用**
- 既存資産の流用元: `MonitorTimeline`（比例算法）・`lib/scheduling/freeGaps`（空き枠・全系統共有）

### 1-5. デザイン適合の要点（`schedule-timeline-production-fit.md`）
モックの意匠は本番デザイン言語（Warm&Human・同ティール#0d9488・クリーム・セリフ）とほぼ同思想。差は使い方:
- カードは**白地**（クリームはカードの外側の地）。ティールは**薄塗り**（/5〜/10）。セリフは**見出しだけ**。
- 性別カード地色は**明度を上げた淡いウォッシュ**（周囲テーブル/リストと馴染ませ、識別は左バー＋文字色）。
- **未定義クラス禁止**: `bg-bg-surface`（→`bg-bg-muted`）・`text-status-mismatch`（→`text-warning`）は
  T-1で実バグ化していた。`--now`は`--sched-now`にトークン化済み。

---

## 2. カイポケ ⇄ CareFlow 双方向化（逆反映・完了）

正典 `docs/plans/kaipoke-reverse-sync-design.md`。自動メモリ `careflow-kaipoke-reverse-sync`。
- **概念「週のバトンリレー」**: 未来週=CareFlowが正（送る）→ **週apply がバトンタッチ** →
  提供中の週=カイポケが正 → 取り込みで CareFlow が追いかける。
- **apply実績ゲート**: 実apply(dry_run=false)した週だけ取り込み可。**実applyは本番未実施のため現状は
  全週で取り込み無効＝初回実apply（下記§4-C）が取り込み運用の開始条件**。
- R-0〜R-3 全完了・本番稼働（HEAD時点 DB 0057）。スタッフ変更=コースの変更（丸ごと交代/移動/臨時コース「臨」新設）。
- 実データ発見: 本番CareFlowに2名体制visitは0件（CSV1行⇔visit1行で成立）。

---

## 3. カイポケ ログイン情報のアプリ内設定化（汎用化・完了）

正典 `docs/plans/kaipoke-credentials-config-design.md`。自動メモリ `careflow-kaipoke-credentials`。
- 法人ID/ユーザーID/パスワードを `kaipoke_credentials`（Fernet暗号化・migration 0058）へ。
  鍵=本番.env `KAIPOKE_CRED_SECRET`。ジョブ起動時にHTTP bodyのみで RPA へ渡す（KaipokeJob.params には入れない）。
- 接続設定UI（admin）・接続テスト。未設定時は連携ボタン無効化。現行情報は移行登録済み・接続テスト成功。
- RPA側(PlaywrightTest1)は payload credentials 対応＋ハードコード除去済み。
- **他事業所展開＝デプロイ＋接続設定入力だけ**。
- ライブモニターはCF Access期限切れを自動検知→別窓ログイン誘導→4秒毎に待受け自動再接続（`f8f521d`）。

---

## 4. 残タスク（タスク振り分け＋順番）★次エージェント最重要

### 【最優先グループ A】スケジュール縦タイムライン刷新の続き（現在の主テーマ）

**タスク順（この順で進める）:**

- **A-1. PO実機フィードバック反映（着手前に必ず確認）**
  直近デプロイ（日/週タイムライン行高52px・90分ペアボックス・日リスト刷新）の見え方をPOが実機確認中。
  「色・囲み・上下2段のサイズ・90分の見せ方・情報量」等の微調整要望が来たら**まず対応**。
  → 担当: `executor`（FE微調整）＋ `designer`（意匠判断が要るとき）。デプロイは frontend のみ。

- **A-2. T-2「触れる化」（操作面の本命・PO期待大）**
  モック（timeline-comparison-mock.html）で体験済みの操作を本番タイムラインに実装:
  - 空き枠クリック→登録モーダル（訪問／会議イベント。イベントは**カイポケ反映外**明示）
  - カードのドラッグ移動（15分スナップ・置けない場所=赤拒否・**この週だけ/固定パターンの二択**）
  - ピン=不可侵（掴めない・shake拒否）／undo（トーストに「元に戻す」）
  - **既存APIに接続**: place-and-fix（プール/空き枠配置）・fix-or-pattern／visit-move-week-only（移動の二択）・
    delete-visit・op-log undo/redo。**新規ソルバ・API不要**（既存の契約を叩くだけ）
  - 参考: 既存 CourseDayTable の DnD 実装（dnd-kit・droppable id=`course-day-cell:...`）と
    CourseDayTablePanel の DragEnd集約が契約の見本。タイムラインは連続時間軸の15分スナップに座標系を変える。
  → 担当: `executor`（model=opus 推奨・DnDと状態管理が重い）→ `code-reviewer` 独立レビュー必須。
  → 規模大。段階分割推奨（②-a 空き枠クリック登録 → ②-b カードDnD移動＋二択 → ②-c ピン/undo）。

- **A-3. T-4 提案系の意匠統一**
  `CourseMoveTimeline`（提案タイムライン・4画面共有）と `WeekdayScheduleCard`（提案ダイアログ側）を
  タイムライン兄弟の視覚言語へ寄せる。**共有コアなので signature 変更は4〜5画面波及**（慎重に）。
  → 担当: `executor` ＋ `code-reviewer`。A-2 の後。

- **A-4. T-5 現場ボード/モバイルの意匠統一**（データ系統は不変・見た目のみ）
  `FieldBoard`（現場ボード）・モバイル自己予定を同じ視覚言語へ。
  → 担当: `executor`。A-3 の後。

- **A-5. T-6 撤去（最終・パリティ達成後）**
  テーブル（CourseDayTable）・旧週ビュー（CourseWeekOverview）を削除。中枢 Panel をタイムライン中心に減量。
  **条件**: パリティチェックリスト（プール配置DnD・担当割当レビュー・ピン・undo/redo・定員/警告・全ダイアログ起動・
  イベント移動）が全てタイムラインで可能＋現場が数週間タイムライン既定で運用できたこと。
  → 担当: `executor` ＋ `code-reviewer` ＋ PO承認。既定のタイムライン化（table/overview→timeline）もここで判断。

### 【グループ B】カイポケ逆反映の運用開始（PO監督が要る）

- **B-1. 初回 実apply（dry_run=false）**（最優先の運用タスク・逆反映の開始条件）
  PO/現場監督下・noVNC監視で。10月サンドボックスで通し→再エクスポートで反映検証。
  **これが済むまで逆反映の取り込みは全週で無効**（apply実績ゲート）。
  → 担当: 人間（PO監督）＋エージェントは監視補助。詳細 `docs/plans/kaipoke-rpa-revival-HANDOFF.md` §7。
- **B-2. 要手当データ**: 髙梨 桂子（staff未登録）・槇 恵（patient未登録）を登録 or 名寄せ。全看護師の職種backfill。
- **B-3. 適用後検証（post-apply verification）実装**: apply後に再exportし1件ずつ照合。

### 【グループ C】その他・低優先

- **C-1. GEMINI APIキーの Google 側失効**（前々セッションからの宿題・ユーザー操作待ち。AI機能は撤去済み）
- **C-2. カイポケ側パスワードローテーション**（旧コードに直書きされていた経緯・PO検討中。変更後は接続設定カードで更新するだけ）
- **C-3. `docs/HANDOFF.md`（2026-06-14 の全般引き継ぎ）が untracked のまま** — コミットするか意図確認
- **C-4. 申請・承認基盤（pending_requests・本番0行）の要否棚卸し**（現場ヒアリング後）
- **C-5. RPA側 .env の KAIPOKE_* 3キー**は当面フォールバックで残置（安定運用後に削除可）

---

## 5. タスク振り分け・順番まとめ（一望）

| 順 | タスク | グループ | 担当 | 状態 |
|---|---|---|---|---|
| 1 | A-1 PO実機フィードバック反映 | A | executor/designer | 待ち（要望次第） |
| 2 | A-2 T-2 触れる化（DnD/登録/undo） | A | executor(opus)+reviewer | **次の本命** |
| 3 | B-1 初回実apply | B | 人間PO＋監視 | 運用・別軸で並行可 |
| 4 | A-3 T-4 提案系の意匠統一 | A | executor+reviewer | A-2後 |
| 5 | A-4 T-5 現場/モバイル統一 | A | executor | A-3後 |
| 6 | A-5 T-6 撤去＋既定切替 | A | executor+reviewer+PO | 最終 |
| — | B-2/B-3・C-* | B/C | 随時 | 低優先/宿題 |

**次エージェントの初手**: このファイル→`schedule-timeline-redesign-design.md`→`timeline-comparison-mock.html` を読み、
POの実機フィードバック（A-1）を確認してから、無ければ **A-2（T-2 触れる化）** に着手。

---

## 6. プロセス規約（本セッションで踏襲・厳守）

- 体制: **実装 → 独立 code-reviewer レビュー（自己approve禁止）→ 全指摘反映 → コミット → デプロイ → healthz確認**。
- デプロイ（frontendのみの変更が多い）: `pg_dump バックアップ → git pull --ff-only → build frontend →
  up -d --force-recreate frontend → git rev-parse → healthz 内外`。**pg_dump行を set -e と繋ぐと稀に exit1
  になるが実害なし**（pull/build/recreateは成功する。段階実行で確認）。
- 回帰確認: schedule系 vitest は既存の SessionProvider/QueryClient 未ラップ fail が多数あり **base HEAD比較で
  ±0 を必ず確認**（`git stash --include-untracked -- frontend` で base 比較）。
- テスト実行: FE=`pnpm vitest run <path>` / `pnpm tsc --noEmit` / `pnpm lint` / `pnpm exec prettier`。
  **prettier --write と vitest を同一コマンドで繋ぐとテストがフレークする**（別々に実行）。
- 日本語ファイル編集は Edit/Write のみ（PowerShell Get-Content/Set-Content 禁止）。
- **CSSコメントに `*/` を含めない**（`bg-*/5` 等はコメントを閉じてしまう。tokens.cssで一度事故）。
- デザイントークンは実値クラス（`bg-*/50` の var()+alpha は CSS不生成）。timelineの色はinline style + var(--sched-*)。
- **未定義Tailwindクラス注意**: `bg-bg-surface`/`text-status-mismatch` は存在しない（透過して無効）。
- 表示専用コンポーネントの原則: データ変換・API・mutation を持たず、Panel が組んだ typed 入力を読むだけ。
- 本番デプロイ後は現場でハードリロード不要（PWA自己回復で自動更新。fe74995で導入済み）。

---

## 7. 気になる点（リスク）

1. **T-2 のDnDは中枢 CourseDayTablePanel（2900行超・全DnDハブ）に触れる** — 最もデリケート。
   既存テーブルのDnDと共存させる（タイムライン用の droppable/draggable を別系統で足し、既存を壊さない）。
2. **共有コア WeekdayScheduleCard** は提案ダイアログ4〜5画面が使用。signature変更は波及。T-4で触るなら慎重に。
3. 週タイムラインは1コース深掘り。全コース俯瞰は「一覧」が担う（撤去しない）。T-6でこの2枚持ちをどう畳むか要設計。
4. 逆反映の初回実apply（B-1）は不可逆。必ずPO監督・noVNC監視・10月サンドボックス。
5. schedule系 vitest の既存fail（Provider未ラップ）は「新規failゼロ」の判定を base比較でしか出来ない。毎回比較する。
