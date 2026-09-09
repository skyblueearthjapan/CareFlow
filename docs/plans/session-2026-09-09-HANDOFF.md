# セッション引き継ぎ 2026-09-09（患者ステータス×予定の連動: 調査 → Phase 0 一掃 → 設計確定 → Phase 1 実装準備）

**次のエージェントへ: まずこのファイル → `patient-status-schedule-design-2026-09-09.md` §6-C（確定事項）と §7（実装計画）を読むこと。** 前セッション総括 = `session-2026-09-08-HANDOFF.md`（本番 203a5c1・実機確認 (a)〜(h)・PO 判断 5 件は未消化のまま）。

## ★ 最初の 5 分
1. **状態（2026-09-09 21:00 JST）**: 本番 HEAD `203a5c1`（コードは前セッションのまま・今日はコード変更なし）。ローカル develop = origin = `7e9d80a`（docs のみ先行）。**未コミット**: `docs/plans/patient-status-schedule-design-2026-09-09.md`（新規）・`docs/plans/session-2026-09-09-HANDOFF.md`（本書）・`docs/reports/2026-09-09-patient-status-cleanup-report.html`（顧客報告・患者名あり・未追跡のまま）。
2. **本番データ変更あり（Phase 0）**: 19:45 に非稼働 8 名の未来 planned(auto) **21 件**を `POST /schedule/v2/visit-cancel-week`（source=manual_cancel・reason 付き・週ごと op_group 4 組）で取消。藤原様の特別訪問週間（期間・○ 25・配置済み 5・うち未来 2 件）は **PO 指示で不変**。バックアップ `/opt/carelink/backups/pre-status-cleanup-20260909-1044.sql.gz`。**カイポケ送信は PO 指示で保留**（次の突合で delete 差分 21 件が出る）。事務がカイポケ週間パターンを止める（9 名）。
3. **PO 決定は設計書 §6-C に全部ある**（取消して非表示／当日から・明日から／効果日なし／復帰は確認→型から再生成／⭐ は必ず確認・既定「残す」／入口は止めるが「稼働中にして続ける」で復帰フローを丸ごと実行／残骸はバッジ／pending も非稼働／型は残す／申請は自動却下）。
4. **次の一手 = Phase 1 実装**（設計書 §7-8 の 5 手）。共有ファイル（定数・列・mig 0082・pydantic・FE 型）をコーディネータが先に編集 → BE-A/BE-B/FE-A を並行投入。
5. PO が「見えない」と言ったら PWA キャッシュ（Ctrl+Shift+R）。

## 1. 今日わかったこと（要点・詳細は設計書 §1）
- ステータスは **週生成の瞬間だけ** 見る。PATCH に連動なし・日付列なし・遷移検証なし。生成済み未来週の残骸は週再生成でも消えない（冪等削除が active 患者に絞られている）。`apply_week_only` の掃除は退行（`tests/test_inactive_patient_visit_cleanup.py::test_apply_week_only_soft_deletes_inactive_patient_visit` が HEAD で失敗）。
- 唯一掃除するのは `reset_visits_to_fixed` の削除側（status 不問・2026-05-31 bacb442）。
- ⭐ プール/place・手動追加/配置の全 API・表示全画面・カイポケ往復（送信/突合/取込）は status を見ない。QR 打刻だけ 404。
- 監査ログ `audit_logs` は before/after 未使用。ステータス変更は request_body の status からしか追えない（今回 20/21 件しか残らなかった＝ミドルウェアはベストエフォート）。
- スタッフには前例あり（`app/api/v1/staff.py:200-210` 非 active 化で同行 purge）。

## 2. 教訓
1. 顧客報告書は「時間がない人が読む」前提で、1 頁目に要約＋お願い、挙動は 2 コマ＋「消える/残る/その後」表、質問は推奨付き表。A4 は `.sheet` 固定高＋絶対配置フッタで 1 頁 1 sheet に割る（Playwright で `scrollHeight-clientHeight==0` を測ると崩れが検出できる）。`⇧` はフォントに無いので使わない。
2. 本番データ変更は **既存 API 経由**（op-log・監査・ガードが全部効く）。生 SQL は最後の手段。
3. PO の「特別訪問週間はクライアントが理解して登録するもの」= 連動処理で黙って触らない。必ず確認し既定は残す。

## 3. 残タスク
- **Phase 1 実装**（設計書 §7）→ レビュー → テスト → PO 確認 → デプロイ（mig 0082 あり＝build --no-cache）→ 実機確認 §7-7。
- Phase 2（入口ガード＋上書き導線＋取込除外）・Phase 3（DTO patient_status バッジ・突合「非稼働患者の行」）。
- カイポケ: 週間パターン停止（事務）・削除差分 21 件の送信タイミング（PO）。
- 前セッション残（`session-2026-09-08-HANDOFF.md` §6）: 実機確認 (a)〜(h)・PO 判断 5 件・BE 開講日バリデーション・入替 1 TX。
- docs のコミット（本書・設計書）。報告書は未追跡のまま。
