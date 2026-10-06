# サインで記録 — 画像の保持期間パージ cron と日次バックアップ（2026-10-07）

- 設計: `docs/plans/signature-checkin-design-2026-10-06.md` §5-1（Q5: 画像は 5 年保存・その後は画像だけ消して記録の行は残す／バックアップ: 画像のフォルダを日ごとのバックアップに含める）。
- 画像の保存先: `/opt/carelink/data/visit_signatures/{yyyy}/{mm}/{id}.png`（backend bind-mount・`chown 999:999`。年月は受け取った日）。
- エンドポイント: `POST /api/v1/admin/visit-signatures/purge-images`（admin token・advisory lock・冪等。`VISIT_SIGNATURE_RETENTION_DAYS`（既定 1825・下限 365）を過ぎた画像ファイルだけ削除。打刻と記録の行は残す）。
- 本番 crontab（`carelink-cron` ユーザー・`voice_recording_cron.md` と同じ方式）に 1 行足す:
  ```
  45 3 * * *  set -a; . /home/carelink-cron/.secrets/carelink.env; set +a; curl -sS -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json" https://carelink.kaipoke-api.net/api/v1/admin/visit-signatures/purge-images >> /home/carelink-cron/signature_purge.log 2>&1
  ```
  初回から 5 年は 0 件で終わる（`{"locked": false, "purged": 0}`）。
- 応答 `{"locked": bool, "purged": n}`。`locked=true` は同時実行の後着。保持日数が下限未満だと 422（設定ミスの可視化）。
- バックアップ: `docs/deployment/scripts/backup-carelink-db.sh` の「3b) Signature images sync」が `/opt/carelink/backups/visit_signatures/` へ写す（`--delete` なし・1830 日を過ぎた写しだけ消す）。復元は `docs/deployment/backup-restore-runbook.md` Step 9。
- 取り出し: `GET /api/v1/visit-signatures/{id}/image`（管理者・職員・期間の制限なし）。取り出すたびに `audit_logs` に `action='signature_read'` が 1 行残る。
