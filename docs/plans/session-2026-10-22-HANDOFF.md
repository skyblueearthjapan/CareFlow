# 2026-10-22 セッション引き継ぎ（患者様の住所を 2 つ持つ・本番へ）

## 1. 本番に出したもの（76c7253a・alembic 0100）
- 患者マスターに **保険証の住所**（`insurance_address`・座標なし）と **「訪問先は保険証の住所と同じ」**（`visit_same_as_insurance`・既定 true）を足した。
- 既存の `address`/`lat`/`lng` は **実際の訪問先** の意味になった（列名は同じ）。QR・移動・ならし・地図は訪問先で計算する（読む側は変えていない）。
- 画面: 患者の登録・編集の「連絡先」→「住所」カード（保険証の住所＋［保険証の住所と同じ｜別の場所］）。詳細は 2 行、別なら「訪問先は別」の札、保険証が空なら「保険証の住所 未記入」の札（管理者だけ）。QR まわりの「登録住所」→「訪問先の住所」。
- 保存のきまりは `backend/app/services/patient_address.py`。訪問先が変わったのに新しい座標が来ないときは、サーバーが控え優先で位置を求め、見つからなければ 422。
- Excel: 末尾に「保険証の住所」「訪問先は保険証と同じ（○）」。「住所」は「訪問先の住所」に。○ の行で訪問先が保険証の住所と違うと行エラー。
- 職員（staff）には `insurance_address` を null で返す。
- 正典: `docs/plans/patient-two-addresses-design-2026-10-22.md`。モック: `docs/mockups/patient-two-addresses-mock-2026-10-22.html`。

## 2. 本番の作業の記録（UTC）
- pg_dump: `/opt/carelink/backups/pre-deploy-two-addresses-20261022-1330.sql.gz`。ログ `/opt/carelink/backups/deploy-two-addresses.log`。
- develop を 76c7253a まで fast-forward → backend・frontend を build・`--no-deps --force-recreate` → `alembic upgrade head`（0099→0100）→ agent-relay・agent-sandbox を build・up（rk の masters が変わったため）。
- 確認: 患者 133 人すべて `visit_same_as_insurance = true`、`insurance_address` と今の住所の食い違い 0 件。healthz（内側・公開）ok、`/agent-relay/healthz` ok、全コンテナ healthy。
- 注意: 作り直しのとき `carelink-postgres` も同時刻に起動し直していた（RestartCount 0・データは無事）。前からそうなのかは未確認。

## 3. 試験
- backend: `test_patient_two_addresses.py`（32 件）・`test_migration_0100.py` ほか関係ファイル。落ちるのは前からの `test_replace_all_manager_forbidden` だけ（ほかに前からの 5 件: `test_patients_v2.py` 4・`test_db_row_persisted`）。
- agent: 330 passed・3 skipped。frontend: 関係 61 ファイル 787 件すべて通過・`tsc` 通過。
- レビュー（1 回）で「住所の文字は新しいのに座標が古いまま」になる経路が 6 つ見つかり、すべて直した（サーバーの網・画面で位置が決まるまで保存を止める・Excel の行エラー）。

## 4. 次にやること
- **PO に画面で見てもらう**: 患者の編集で「同じ／別の場所」の切り替え、保険証の住所を打ち直したときの位置の表示。現場はハードリロード（Ctrl+Shift+R）。
- **川名さんの確認書**（PO の PC の Downloads `住所の確認のお願い_2026-10-22.html`・個人情報なのでリポジトリ外）が戻ったら:
  - B（実際の訪問先）の方 → 画面で「別の場所」にし、訪問先の住所を入れ、保険証の住所はカイポケの住所にする。
  - A（らく助の書き間違い）の方 → 保険証の住所をカイポケの住所に合わせる。
  - P021 様は保険証の住所は一致。訪問先（約 3.5km 先）を入れると QR の「場所違い」が止まる。
- カイポケの利用者情報との見比べは **別に設計**（PO 10-22・カイポケを正として自動で反映するとは限らない）。材料は設計書 §5。
- Downloads のカイポケ CSV（`利用者情報 (1).csv`）と Google の `client_secret_…json` は、要らなくなったら消す（PO 了承済みで残置中）。
