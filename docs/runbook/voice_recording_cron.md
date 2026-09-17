# 訪問の音声記録 — 保持期間パージ cron（2026-09-18）

- エンドポイント: `POST /api/v1/admin/visit-recordings/purge-audio`（admin token・advisory lock・冪等・`VISIT_AUDIO_RETENTION_DAYS`（既定 90・下限 30）超の音声ファイルだけ削除。文字起こし・要約は残す）。
- 本番 crontab（`carelink-cron` ユーザー・`checkin_jobs_cron.md` と同じ方式）:
  ```
  30 3 * * *  set -a; . /home/carelink-cron/.secrets/carelink.env; set +a; curl -sS -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json" https://carelink.kaipoke-api.net/api/v1/admin/visit-recordings/purge-audio >> /home/carelink-cron/voice_purge_audio.log 2>&1
  ```
- 応答 `{"locked": bool, "purged": n}`。`locked=true` は同時実行の後着。保持日数が下限未満だと 422（設定ミスの可視化）。
- 手動実行: 上記 curl をそのまま。ログ `/home/carelink-cron/voice_purge_audio.log`。
- 音声の保存先 `/opt/carelink/data/visit_audio/{yyyy}/{mm}/{id}.{ext}`（backend bind-mount・`chown 999:999`）。鍵 `/opt/carelink/secrets/rakusuke-voice-sa.json`（0400・`:ro` mount）。
- バックアップ: 音声は日次 DB バックアップに含めない（サイズ）。必要なら `rsync` で別途。
