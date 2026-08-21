# セッション引き継ぎ 2026-08-21〜22（週空間構想 全Phase完成・カイポケ双方向同期・実機検証）

**次のエージェントへ: まずこのファイルを読むこと。**
本番 HEAD = `936504c`（全コミットデプロイ済み・healthz 健全確認済み・**migration 無し** = DB は 0074 のまま）。
RPA リポ（VPS `/root/PlaywrightTest1`・コンテナ `kaipoke-api`）HEAD = `a12c54e`（本セッションで 3 コミット: e9ce312 / fe968cf / a12c54e）。
バックアップ = `/opt/carelink/backups/pre-deploy-20260821-*.sql.gz` 多数（サーバは **UTC 表示**に注意）。
デプロイは通常 build で可（migration 無し）。RPA はファイル編集→`docker restart kaipoke-api`（ソースはボリュームマウント）。

---

## 0. このセッションで何が起きたか（30秒版）

**「週空間構想」を朝の設計相談から1日で全Phase本番稼働+実機検証まで完走した。**

- 思想: 「最適化された固定コース（マスタ=PFV/テンプレ）」と「今週の柔軟なスケジュール
  （週空間=courses+visits+staff_events）」を完全分離し、カイポケ同期は週空間だけと行う。
  **正典 = `docs/plans/weekly-space-design.md`**（憲法5条・Phase表・実機テスト教訓まで全部入り）
- 成果: 職員スケジュールタブが「今週の組み立て盤面」に進化
  （コース/患者1件のDnD貼り替え・曜日跨ぎ移動・×解除・休み表示）+
  **🔄突合パネル**（カイポケとの差分表示→1件ずつ⇩取込/⇧送信・👥マスタ突合）
- 実機検証: PO立会いで 突合→⇩取込→⇧送信→復元 の全サイクルを実施。
  **発見した不具合8件+αを全て是正**（過去日ガード等・§3）
- おまけ: 患者/スタッフマスタに「あいうえお順」トグル（PO要望・kana列は全件登録済みだった）

## 1. 実装した機能（コミット順）

| コミット | 内容 |
|---|---|
| `e63988e` | 週空間設計書（調査+憲法+突合ビュー設計） |
| `7823f2b`+`799e6d3` | **A1**: コース貼り付けDnD+休み表示（新API GET /staff/overrides-week）+レビュー対応 |
| `1fe4f4f`+`1ffb611` | ×解除ボタン・ドラッグゴースト（applyCourseDragImage）・戻し先ゾーン |
| `2728b10` | **A2前段**: 患者1件の担当貼り替え（新API POST /schedule/v2/visit-assign-staff-week = primary+manual_staff_override+VSA置換3点セット・undo新op set_visit_staff） |
| `195519b` | ×無反応の根治（取込由来帯=コース行担当が空→訪問の個別解除フォールバック） |
| `61fd9a5` | **「コースの表」パレット撤去（PO判断）** → （担当なし）行が置き場+戻し先。DnD共有部品は `courseDnd.ts` |
| `c221d0c` | **A2後段**: 曜日跨ぎ移動（新API POST /schedule/v2/course-move-weekday-week-only・undo新op move_course_weekday）+ **Phase B**: cascade_fixed_visit を422封鎖 |
| `f04c516` | **C1**: KaipokeReconcilePanel（突合・イベント差分の盤面ゴースト🟣🟡🔵・1件ずつ⇩） |
| `99f8625` | A2〜C1レビュー対応（undo衝突ガード等） |
| `69dac3c` | **C2**: 突合パネルから訪問の⇧送信（diff-local+/integrations/apply結線・2段クリック確認）。**C0プローブ不要と判明**（訪問書込RPA=auto_apply.pyが既に本番稼働） |
| `17e6d97` | 突合UX（パネルを盤面の上へ+自動開始 — 「押しても何も起きない」PO指摘対応） |
| `f2b5d0b` | **409障害根治**: _reconcile_latest_job が自己完結型 events-preview ジョブを先取りクローズ→statusが409。予防（30分以内の自己完結opはスキップ）+自己回復（RPA resultから再構築） |
| `f4b4b3d` | 戻る/進むボタンを Row 2（青ピン行の空き）へ移設（PO指示・タブ行が2行に） |
| `63fe578` | **あいうえお順**トグル（患者/スタッフマスタ・lib/kana-sort.ts・URL ?sort=kana 同期） |
| `ba6253a`+`936504c` | **実機テスト是正8件+Phase M**（§3参照）+レビュー対応 |

RPA側: `e9ce312`（氏名照合スペース除去+削除検証）/ `fe968cf`（削除確認網拡大+失敗時スクショ/HTML保存）/ `a12c54e`（apply結果にログ末尾添付）。

## 2. 実機検証の記録（2026-08-21夜・PO立会い）

1. 🔄突合: イベント42件差分検出 → **⇩1件取込 → 再突合で41件（取込分が「一致」化）= 同期ループ実証**
2. ⇧送信テスト1回目（シング様8/18・過去日）: RPAは動いたが「元行残存+新行追加・担当が-」
   → 真因: **カイポケは当日以前に実績入力済みで、実績付き行は予定画面から動かせない** +
   氏名スペース差（髙梨桂子≠髙梨　桂子）で職員選択失敗。誤追加行は遠隔で削除・復元済み
3. RPA修正後、**未来日（8/22 久須見様）1件の⇧送信成功**（時刻変更+職員選択OK）→ 遠隔で元に復元
4. 全操作はサーバ側から監視（kaipoke_jobsポーリング+BEログ）。遠隔オペの手口は §5 参照

## 3. 実機テストから生まれた是正一式（`ba6253a`/`936504c`・全て本番）

正典 = weekly-space-design.md「C2実機テストの教訓と是正」節。要点:

1. **⇧送信の過去日/実績保護**: 週スコープapplyで当日以前(JST)を除外・全過去日は422。FEも同基準（Intl Asia/Tokyo）で一覧から除外
2. **部分適用 itemIds**（/integrations/apply）: 1件送信でシートがロックされる409を解消。送信済みitemは include=False 化+再指定422（サーバ側再送ガード）
3. **include_unassigned**（csv_builder）: 差分計算で未割当訪問を'-'行として比較。「週全未割当→カイポケ全削除127件」の偽提案の根治。送信行に「担当なし(-)」バッジ
4. **氏名正規化の単一ソース** = `master_reconcile.normalize_person_name`（NFKC+空白全除去+異体字マップ）。diff engine の _normalize_user_name も委譲・outboundも normalize_names=True
5. **date_change 最近接日ペアリング**（engine Pass3スコアリング）
6. **Phase M マスタ相互突合**: POST /integrations/master-reconcile + 突合パネル👥ボタン。患者/スタッフ × 一致/表記ズレ/カイポケのみ/らく助のみ（カイポケ側は当月CSVに現れた氏名で判定）
7. **⇩取込差分（全曜日・1件ずつ）**: diff-inbound結線 — 実績のない日も項目単位で取込可（当初PO要望）
8. 突合UX: 再突合でリスト全リセット+計算時刻表示

## 4. 残タスク（優先度順）

1. **⇧送信の再実機確認（5分・次回最初に推奨）**: 修正版RPAで未来日の「変更」1件を送信→
   カイポケで「元行が消え新行に担当が入る」目視確認。通れば全件送信を運用解禁
2. **Phase D（未着手・最後の1ピース）**: 突合の定期自動実行+「差分N件あり」ベル通知=完全な常時同期。
   ⇧運用が数週安定してから。実体=突合パネルのfetch群をcron/スケジューラ化+通知基盤へ1件
3. **見送り済みの小粒改善**: 訪問差分の盤面ゴースト（現状イベントのみ）/ smart-preview(訪問・45-90s同期)の202化
   （Cloudflare 100sの安全マージン・イベントで確立した型の横展開）/ ダークモード配色(amber等raw色・全体課題) /
   date_change の after 側日付も過去日ガード対象に（理論上のみ）
4. **週空間以外の持ち越し**（前ハンドオフ session-2026-08-21-HANDOFF.md から不変）:
   休み申請系（am_off/pm_off エンジン無視・request_type CHECK制約不一致疑い・管理者ベル通知）/
   都賀A誤紐付け24行・加藤様/並木様5枠（お客様待ち）/ QR実機3点・同行実機15項目 /
   新規スタッフ登録時のアカウント自動作成の仕組み化 / two-way設計書§5のPO確認
5. **テスト既知ベースライン**: `test_integration_kaipoke.py` の9件fail（*_requires_admin系=ロール正規化以降の既知・
   stash比較で差分ゼロ確認済み）+ 従来分（8/17一覧・test_pending_requests str vs UUID）。触らない

## 5. 次のエージェントが知るべき「手口」（本セッションで確立）

- **遠隔カイポケオペ**: correction_sheets+items をSQLで直挿入（direction='outbound', include=true, before/after JSONB）→
  admin トークン鋳造（`docker exec carelink-backend python -c "from app.core.security import create_access_token; ..."`・
  admin id は users から）→ `curl localhost:18001/api/v1/integrations/apply {sheetId, dryRun:false}`。復元も同手口
- **RPAログ**: `docker exec kaipoke-api tail /var/log/supervisor/api.log`（apply結果にも log_tail 添付済み）。
  RPA API直叩きは `.env` の KAIPOKE_API_TOKEN を Bearer で
- **デプロイのSSH切断対策**: リモートで `nohup sh -c "build && up -d" > /tmp/deploy-X.log &` →
  別sshで `until grep Started /tmp/deploy-X.log; do sleep 12; done` をrun_in_background
- **ジョブ監視**: kaipoke_jobs を psql でポーリング（op名は `params->>'op'`。events-preview/smart-preview/diff-local=fetch、apply=push）

## 6. 教訓（今セッション新規分）

1. **カイポケは実績が正**: 当日以前は実績が入力済みのことがあり、実績付き行は予定画面から動かせない。
   外部書込は「未来の予定のみ」が原則（イベントの実績02除外と同じ整理）
2. **1件テストの価値**: 全件送信前の1件テストが「全削除127件」提案・二重化・担当'-'を捕まえた。
   外部書込機能は必ず 1件→目視→全件 の順で
3. **RPA操作は「実行後の検証」が必須**: クリック成功≠操作成功。削除後の残存チェックで silent no-op を failed に
4. **汎用reconcileと自己完結ジョブの競合**: 遅延クローズ(idle観測でjobを閉じる)は、自前statusで決着する
   非同期opを先取りクローズし得る。op種別でスキップ+status側に自己回復を
5. **SQLAlchemyのVSA置換罠**: bulk delete+同一PK再INSERTは flush順序(INSERT→DELETE)で相殺される。
   ORM delete+先flush で行うこと（op_log_service/schedule_v2 に同コメントあり）
6. **氏名正規化は単一ソースに**: スペース(半角/全角/無)+異体字(髙/高等)。らく助・engine・RPAの3箇所に
   別実装があると必ずズレる → master_reconcile.normalize_person_name に集約済み
7. **FEテストの日付は動的に**: 過去日ガード等「実時計」依存ロジックのテストは固定日付だと時間経過で腐る
   （KaipokeReconcilePanel.test.tsx の「来週」計算方式を踏襲）

## 7. 動作確認の手引き（PO向け）

ハードリロード必須。スケジュール →「職員スケジュール」タブ:
- **盤面**: コース帯(⠿)/訪問の行をドラッグでスタッフ間・曜日間移動（今週のみ・型不変）。
  ×か（担当なし）行へドラッグで解除。「戻る」でundo
- **🔄突合**: 押すと自動で取得(2〜3分)→イベント差分(盤面ゴースト+1件ずつ⇩)・訪問差分・
  「⇩取込差分(全曜日)」・「⇧送信差分」(未来日のみ・2段クリック)・「👥マスタ突合」
- **患者/スタッフマスタ**: 検索欄の隣「コード順|あいうえお順」トグル
