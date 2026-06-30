# QR チェックイン 定期ジョブ cron 設定手順 (Phase 5-2 / 5-6)

`lat_lng_audit_cron.md` と同じ「VPS 側 cron + 管理 API (admin Bearer token)」方式で、
QR 訪問チェックインの 2 つの定期ジョブを登録する。

- **未訪問通知 (5-2)**: `POST /api/v1/admin/checkin/check-missing` (5 分毎)
- **GPS パージ (5-6)**: `POST /api/v1/admin/checkin/purge-gps` (日次 03:00)

両ジョブとも **PostgreSQL advisory lock** で多重実行を排他し冪等 (重複起動しても
二重生成 / 二重更新しない)。

## エンドポイント

### 1. 未訪問通知 — `POST /api/v1/admin/checkin/check-missing`

- 認証: admin role の Bearer token
- body: なし
- レスポンス: `{"locked": false, "scanned": N, "missing": M, "created": K}`
  - `scanned` = 走査した当日 visit 数 / `missing` = 未訪問判定数 / `created` = 新規通知行数。
- 動作: 当日 (JST) の visit を走査し、**到着記録なし** かつ
  (**未訪問記録あり** または **予定開始 + grace 超過**) の visit を「未訪問」と判定。
  取消 / 削除 visit、`visit_reviews` で確認済みの visit、既通知は除外し、active な
  admin/manager 全員へ `notifications` (`type='checkin_missing'`) を **冪等** 生成する
  (`reference_type='checkin_missing'` + `reference_id=visit_id`)。
- 想定頻度: **5 分毎**。
- 備考: 場所違い (mismatch) 通知は checkin 記録時にイベント駆動で即時生成されるため
  cron 不要。

### 2. GPS パージ — `POST /api/v1/admin/checkin/purge-gps`

- 認証: admin role の Bearer token
- body: なし
- レスポンス: `{"locked": false, "purged": N}`  (`purged` = NULL 化した checkin 行数)
- 動作: `visit_checkins` で `scanned_at < now - 2年` の行の `lat` / `lng` / `accuracy_m`
  を NULL 化する (APPI 保持上限)。`distance_m` / `match_status` は監査のため **残す**。
- 想定頻度: **日次 03:00** (geocoding audit と同枠)。

## VPS 側 cron 設定 (初回のみ)

`lat_lng_audit_cron.md` の `carelink-cron` user と `.secrets/carelink.env`
(`ADMIN_TOKEN=...`) をそのまま流用する (既に作成済みなら新規作成不要)。

### cron 登録

```bash
sudo -u carelink-cron crontab -e
```

以下 2 行を追加 (既存の geocoding audit 行はそのまま残す):

```cron
# QR チェックイン 未訪問通知 (5 分毎)
*/5 * * * *  set -a; . /home/carelink-cron/.secrets/carelink.env; set +a; curl -sS -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json" https://carelink.kaipoke-api.net/api/v1/admin/checkin/check-missing >> /home/carelink-cron/checkin_missing.log 2>&1

# QR チェックイン GPS パージ (日次 03:00 JST)
0 3 * * *  set -a; . /home/carelink-cron/.secrets/carelink.env; set +a; curl -sS -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json" https://carelink.kaipoke-api.net/api/v1/admin/checkin/purge-gps >> /home/carelink-cron/checkin_purge_gps.log 2>&1
```

### 初回手動テスト

```bash
# 未訪問通知 (生成件数を確認)
sudo -u carelink-cron bash -c 'set -a; . /home/carelink-cron/.secrets/carelink.env; set +a; curl -sS -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json" https://carelink.kaipoke-api.net/api/v1/admin/checkin/check-missing'

# GPS パージ (NULL 化件数を確認。初回は 2 年前のデータが無ければ purged=0)
sudo -u carelink-cron bash -c 'set -a; . /home/carelink-cron/.secrets/carelink.env; set +a; curl -sS -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json" https://carelink.kaipoke-api.net/api/v1/admin/checkin/purge-gps'
```

期待 response 例:

```json
{"locked": false, "scanned": 42, "missing": 1, "created": 3}
{"locked": false, "purged": 0}
```

## モニタリング

- `/home/carelink-cron/checkin_missing.log` / `checkin_purge_gps.log` に response が追記
  される。`logrotate` (weekly) 推奨。
- 通知は `notifications` table の `reference_type IN ('checkin_missing','checkin_mismatch')`
  で追える。
- `created` が常に 0 → 未訪問が無い (正常) か、admin/manager ユーザーが居ない / 既通知。

## トラブルシュート

| 症状 | 原因 | 対処 |
|------|------|------|
| `locked: true` | 別ジョブが advisory lock 保持中 | 前回 job 完了を待つ。transaction 終了で必ず解放。 |
| `created: 0` が継続 | 未訪問が無い / 既通知済み / admin・manager 不在 | `notifications` を確認。重複は冪等キーで抑止される (正常)。 |
| 401 Unauthorized | ADMIN_TOKEN 期限切れ | 新 token を再発行し `.secrets/carelink.env` を更新。 |

## 補足 (backlog)

- 未訪問通知の **自動解決** (missing → 後から到着で通知をクリア) は本フェーズ非対象。
  確認済み (`visit_reviews`) で要対応トレイから外す運用で代替する。
- 通知ターゲットは当面 **全 admin/manager** (拠点別の絞り込みは将来検討)。
