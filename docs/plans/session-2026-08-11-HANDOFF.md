# セッション引き継ぎ 2026-08-11（NGスタッフ機能 全実装 → 実機不備の全面根治）

**次のエージェントへ: まずこのファイルを読むこと。**
本番 HEAD = `1c26076`（全コミットデプロイ済み・healthz 健全確認済み）。
DB migration head = `0070_patient_ng_staff`（本セッションで新設・適用済み。0069 から 1 本）。
バックアップ = `/opt/carelink/backups/pre-deploy-20260811-{0043,0858,1250}.sql.gz`（3 回デプロイ各直前）。

デプロイ手順は `docs/deployment/runbook.md`。migration を含むときは build `--no-cache` +
`compose run --rm backend alembic upgrade head` 手動実行 + `alembic heads` 1 行確認
（本セッション 1 回目のデプロイで実施済み。2・3 回目は migration 無しの通常ビルド）。

---

## 0. このセッションで作ったもの（3 行サマリ）

1. **NGスタッフ機能**（患者ごとの割当禁止スタッフ・複数可・恒久のみ）を DB〜エンジン〜UI〜Excel まで新規実装し本番稼働。
2. PO 実機テスト（朝倉様×高岡様）で「提案が NG 先を出す・手動移動が素通し」が発覚 →
   **提案系は NG ハード除外へ格上げ・手動/採用系 約15 経路に 422 確認フローを面展開**して根治。
3. 副次バグ 4 件を発見・修正（編集ポップアップの patientId 未伝搬 / 提案キャッシュ失効漏れ /
   コース跨ぎ移動の担当未更新 / Excel 削除経路の中間テーブル掃除漏れ）。

**正典設計書 = `docs/plans/patient-ng-staff-design.md`**（PO 4 論点の決定・実装コミット一覧・
経路網羅表・実装時確定事項をすべて反映済み。仕様の疑問はまずここ）。

## 1. NGスタッフ機能の最終仕様（覚えるべき要点）

- **データ**: `patient_ng_staff`（複合PK patient_id×staff_id + note + decided_by_user_id・mig 0070）。
  行が存在する=NG。「NG のみ行を作る」方式。旧 v1 の ng_staff_ids は 2026-05 に廃止済みで
  **旧データは復旧不可**（今回の実装は復活だが入力はゼロから）。
- **制約の強度（PO 確定）**:
  - Layer3 自動割当 = **ハード**（INF 除外・Stage3 でも緩和しない・性別と同格）。
    候補ゼロは ⛔ 赤レビュー（段階式 reason: 性別緩和→NG緩和→両方=`also_violates`）→
    管理者 override 可・一斉承認対象外。残留は `unresolved_ng_warnings`。
  - **提案系 = ハード除外（候補を生成しない）** ← 2026-08-11 夕に「警告+降格」から格上げ
    （PO 明言「NG 先に提案を進めるのは機能していないのと同じ」）。スワップは**両方向**検査。
    **性別制限は提案系では従来どおり警告+降格のまま**（非対称は意図的・PO 未指示のため）。
  - 手動・採用系 = **422 確認フロー**（`code=constraint_confirmation_required` →
    確認ダイアログ → `acknowledge_constraint_warnings:true` 再送で通過 →
    **管理者へお知らせ通知**（ベル・操作者本人含む=監査兼用・op_group_id 冪等））。
  - 2名体制 secondary = 性別+NG を検証するが**割当は維持**（ペア構造を壊さない・警告のみ）。
- **BE 共通部品** = `backend/app/services/constraint_override_notify.py`
  （検査の芯 `_match_patients_against_staff` 単一ソース / コース→患者方向と患者→コース方向の
  兄弟関数 / 422 detail ビルダ / 通知 producer。**checkin 版の `_create_idempotent` は
  reference_id=None が IS NULL 展開で永久沈黙する罠があるため流用せず再実装した**）。
- **FE 共通部品** = `useConstraintConfirmRetry`（422 捕捉→確認→ack 再送・経路別文言）+
  `ConstraintOverrideConfirmDialog` + `parseConstraintConfirmationDetail`。
- **UI 配置**: NGスタッフ/同住所紐付けは患者マスタの**「訪問条件」Card 内**（embedded）。
  PatientForm 1 箇所で マスタ編集/新規/スケジュールの編集ポップアップ/新規登録 の 4 画面に反映。
  モバイルは「訪問条件」セクションで閲覧のみ（編集は PC 限定）。
- **Excel**: 患者マスタシート**末尾**に `ng_staff_codes` 列（カンマ区切り・`<CLEAR>` で全解除・
  replace_all は**空セル=維持の merge 例外**=バックアップ復元で全消しさせない）。
  **列は位置ベース読取のため途中挿入は silent 破壊 — 新列は必ず末尾**。
  同住所紐付けは対称ペアの矛盾リスクのため Excel 対象外（UI 専用・意図的見送り）。

## 2. 経路網羅（どこで何が効くか）

設計書 §7-1 の表が正典。要約:
- **検査あり**: Layer3(ハード) / コース担当変更 PATCH / apply-staff-review / visit-move-week-only
  （「訪問を移動」2 択・DnD・ペア・改善提案の週反映が全部ここ） / place-and-fix（プール直接
  ドロップ・空き枠登録・採用この週だけ） / 特別週 place / PUT fixed-visits(pattern_and_week) /
  apply-individual / apply-swap（両患者） / scope・unblock・pool-bulk の各 apply
  （**書き込み前の一括事前検査で部分適用ゼロ**） / visits 直 API 3 本（単純 422・FE 導線なし）。
- **提案生成側**: propose-slots / ペア枠 / 改善提案 move+swap / scope / unblock（退避先含む）で
  NG 候補を生成しない。pool_bulk_inserter は propose 委譲で自動適用。
- **検査なし（意図的）**: カイポケ取込（外部データが正・dry-run に ngConflicts 警告のみ） /
  reset-to-fixed・sync-fixed-to-week・from-week-bulk（下記残タスク参照） / 現場ボード（配置系
  mutation 自体なし） / レガシー `/allocate`・`/schedule/fix`（FE 導線なし・下記棚卸し参照）。

## 3. 本セッションのコミット（時系列・全て本番反映済み）

第1弾（機能実装）: 9f3d9ef 基盤(mig0070) / 31097a2 Layer3 / f71b7b6 提案系警告(→後に除外へ) /
6e9bbbf 手動コース担当422+通知 / 01364a6 FEコア / cb0812e 面展開+逆引き / 9f8d0bc 賢いマスタV9+
取込dry-run / 3e9e4e7 連携画面表示 / 95b92bc docs
第2弾（訪問条件統一+Excel）: 022279d 訪問条件枠統一+**ポップアップpatientId未伝搬バグ根治** /
9f95680 Excel NG列+**Excel削除経路の掃除バグ修正**
第3弾（実機不備の根治）: ba7c1da 提案系ハード除外 / 8e50065 手動5経路422 / 67f6363 FE配線+
**キャッシュ失効根治** / 04a4020 適用系4経路+visits直API / 7a54860 apply-swap FE+設計書 /
1c26076 テストteardown

## 4. 残タスク・PO 判断待ち

1. **提案系の性別制限も除外に格上げするか** — 現状 NG だけ除外・性別は警告のみの非対称。
   PO に選択肢を提示済み（最終報告 2026-08-11）。やるなら ba7c1da と同じ箇所に 1 条件ずつ。
2. **候補ゼロ時の理由表示** — NG 除外で候補が消えても `ExcludedReasonCode`（
   `schemas/v2/propose_slots.py:228` の Literal）に NG コードが無く「なぜ出ないか」が FE に
   出ない。schema+FE 辞書への追加が必要（小・独立タスク）。
3. **reset-to-fixed / sync-fixed-to-week / from-week-bulk の通知** — PFV に眠る NG 組み合わせが
   週生成のたび黙って復活し得る。経路調査で「422 不適・通知向き」と分類したが未実装（P3）。
4. **レガシー棚卸し** — `services/allocation/engine.py` の NG 15 箇所+文字列不一致で死んでいる
   性別チェック 9 箇所（`"女性のみ"` vs `"female_only"`）、`/allocate/run`・`/schedule/fix`・
   `/fix-or-pattern`（FE 導線なし・API 直叩きの穴）。削除するなら一括で。
5. **apply-swap B 側の移動先仕様** — 現実装は `_apply_pfv_move` の後方互換どおり
   「course_template_id 省略=B は自コース維持（曜日だけ変わる）」。「真にコースを交換」へ
   変えるなら `_apply_pfv_move` の fallback 側を直す（検査は自動追随）。設計判断メモは
   04a4020 のコミット・`test_sw2` が現挙動を固定。
6. **`manual_staff_override` visit の扱い** — コース跨ぎ移動時の担当付け替えで override を
   **上書きする**方針を採用（理由コメントあり・`schedule_v2.py` の `_apply_visit_move_week_only`）。
   PO が「override は移動後も維持」を望むなら 1 条件で戻せる。
7. **同住所紐付けの Excel 対応**（専用シート方式なら可能）と **A4 カルテへの NG 欄**
   （テンプレ再生成が必要）は意図的見送り — 要望が出たら着手。
8. **scope_optimizer._copy_bucket の event_windows / course_template_id 欠落** — NG とは無関係の
   既存バグ（範囲最適化/一括投入の模擬状態でイベント考慮が効いていない可能性）。要調査。
9. **テスト基盤の順序依存フレーク** — conftest の共有インメモリ SQLite 1 コネクションが根因。
   対症は各ファイルの teardown rollback（1c26076 の型）。恒久策 = conftest への autouse
   rollback（影響範囲が広く意図的に見送り・要判断）。

## 5. ハマり所・教訓（今セッション発生分）

1. **「4 画面に自動反映」は props の伝搬まで確認する** — PatientEditDialog が PatientForm に
   patientId を渡しておらず、ポップアップだけセクションが丸ごと非表示だった（レビューでも
   見逃し・PO 実機で発覚）。ラッパー経由の「相乗り」実装は結線テストを必ず書く。
2. **機能を足したら「その結果を読むキャッシュ」を全部失効させる** — NG 登録後も改善提案が
   staleTime 5 分で古いまま表示され「警告が出ない」ように見えた。`invalidateNgStaffDependents`
   / `invalidateSameAddressDependents` に依存キーを集約済み。新しい提案系クエリを作ったら
   ここに追加すること。
3. **保護は「生成側」と「適用側」の両方に置く** — 提案から除外しても、生成後に NG 登録された
   古い提案の適用が素通りする（plan 指紋は NG 行を含まない）。適用系は書き込み前の一括事前
   検査（部分適用ゼロ）が正解。
4. **複製 dataclass はフィールド追加に追随しない** — `_CourseBucket` の浅コピー
   （unblock の `_bucket_without` 等）が `ng_patient_ids` を落とし、模擬状態だけ除外が無効に
   なっていた。バケットにフィールドを足したら全複製箇所を grep すること。
5. **`onClick={fn}` は event が第 1 引数に入る** — `fn(acknowledge=false)` 型のハンドラを
   直接渡すと acknowledge が truthy になる。必ず `onClick={() => fn()}`。
6. **テストでリクエスト間に test session から INSERT しない** — 共有インメモリ SQLite では
   1/3 程度の確率で黙って落ちる。API 経由で登録するか、リクエスト前に seed する。
   テスト終端で `await db.rollback()`（開いた読み取り TX を残さない）。
7. **通知の冪等キーに `reference_id == None` を使うと SQLAlchemy が IS NULL 展開して
   「過去の NULL 通知全部」にヒットし永久沈黙する** — None のときは重複検査ごとスキップ。
8. **Excel 列は位置ベース** — 途中挿入は旧ファイルを silent 破壊。新列は必ず末尾。
   replace_all に載せる列は「空=維持」の merge 例外を検討（weekly_pattern の前例）。

## 6. 既知のベースライン失敗（触らない・変更前から存在を stash 比較で実証済み）

manager-403 期待系 約21 件（ロール二軸 mig0069 由来） / `special_week_active=None` の
patients_v2 系 / audit middleware / auth lockout / kaipoke integration RBAC /
`test_schedule_v2_api` reset_to_fixed×2 / UUID hex 系（test_visit_v2 / test_visits /
test_pending_requests・order 依存フレーク含む） / test_inactive_patient_visit_cleanup /
FE: e2e spec の vitest 収集 9 件 + middleware manager 1 件 + BulkPoolInsertDialog 並列フレーク。

## 7. 動作確認の手引き（PO 向け・再テスト観点）

ハードリロード（Ctrl+Shift+R）必須。朝倉様（P084）× 高岡様の組み合わせで:
① 配置改善の提案に高岡様のコースが**出ない** ② 「訪問を移動」で高岡様のコースへ →
「それでも移動しますか？」確認 → OK で管理者ベルに通知 ③ 空き枠登録セレクトで NG 患者に ⛔
④ 自動スタッフ割当で候補が高岡様しか居ないコースは ⛔ 赤レビュー（一斉承認不可）
⑤ カイポケ取込 dry-run に NG 衝突警告（取込自体は通る）。
