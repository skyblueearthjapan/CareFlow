# kaipoke-ops — らく助×カイポケ連携の調査・復旧スクリプト

2026-09-03 の 9/7 週送信事故（`docs/plans/session-2026-09-03-HANDOFF.md`）で使った道具。すべて読み取り専用（`build_payload.py` の出力を RPA に投げるときだけ書込になる）。本番 VPS（`root@72.60.211.213`）で使う前提。

| ファイル | 使う場所 | 何をするか |
|---|---|---|
| `admin_call.py` | backend コンテナ | 今泉アカウントの JWT を内部発行して API を 1 回叩く。`docker cp admin_call.py carelink-backend:/tmp/ && docker exec -w /app -e PYTHONPATH=/app carelink-backend python /tmp/admin_call.py POST /api/v1/integrations/diff-local '{"month":"2026-09","weekStart":"2026-09-07","weekEnd":"2026-09-13"}'` |
| `classify.py` | kaipoke-api コンテナ (`docker exec -i kaipoke-api python3 - < classify.py`) | `/tmp/run.log`（`api.log` の 1 実行分）から失敗項目を「削除OK→追加失敗 / 削除検証NG / 職員select未表示 / 予定未検出」で分類。`failed_keys` は対象に合わせて書き換える |
| `blocks.py` | 同上 | 指定 (利用者, 日) の RPA ログブロックを丸ごと表示 |
| `csvrows.py` | VPS ホスト (`python3 - < csvrows.py`) | `/root/PlaywrightTest1/data/current_202609.csv` の (日,利用者) 行を一覧。`want` を書き換える。**export が新鮮か（`CSV出力完了` ログ・md5 変化）を先に確認** |
| `dropdown.py` | VPS ホスト | RPA が保存した `artifacts/debug_click_entry_*.html` からカイポケの利用者ドロップダウン全件を取り出して検索（氏名の漢字違い調査） |
| `build_payload.py` | backend コンテナ | 修正シートの items から RPA `/api/apply` payload を生成（担当なし `-` は除外・`dry_run:true`）。`SHEET` を書き換える |
| `xref.sql` | postgres (`docker exec -i carelink-postgres psql -U carelink -d carelink -q < xref.sql`) | 残差シート × 前回送信シートの突合 |
| `live.sql` | 同上 | 週内で主担当が NULL の訪問（担当なしで送られる候補） |
| `audit2.sql` | 同上 | 週生成/割付の実行履歴と週ごとの重複グループ数（`deleted_at IS NULL` 付き） |
| `dups.sql` | 同上 | 特定患者の重複訪問・VSA・コース担当の内訳 |

RPA 直叩き（dry-run は登録しない）:
```
TOK=$(docker exec carelink-backend printenv KAIPOKE_API_TOKEN)
curl -s -X POST http://127.0.0.1:5000/api/apply -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" --data-binary @payload.json
curl -s http://127.0.0.1:5000/api/apply/result -H "Authorization: Bearer $TOK"   # 完了までポーリング
```
