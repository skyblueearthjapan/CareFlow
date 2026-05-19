# lat/lng 整合性 audit cron 設定手順 (Task #144 / Phase G-5)

## 目的

User が患者の住所だけを手で書き換えて lat/lng が古いまま残るケースを 1 日 1 回
自動で catch して再 geocoding する.

## エンドポイント

`POST /api/v1/admin/geocoding/audit`

- 認証: admin role の Bearer token
- body: `{"dry_run": false}` (= 本番実行) または `{"dry_run": true}` (差分プレビュー)
- レスポンス: `{locked, checked, corrected, skipped_unchanged, errors, details: [...]}`
- 動作: 全 alive 患者の `address` を Google Geocoding で再評価. 既存 `(lat, lng)`
  と **100m (0.1km) 以上ずれ** ていれば自動 UPDATE + `audit_logs` に
  `auto_geocode_correction` 記録. 並列実行は `pg_try_advisory_lock` で排他.

## VPS 側 cron 設定 (初回のみ)

### 1. cron 専用 user を作成 (任意、 root 直は非推奨)

```bash
ssh root@72.60.211.213
useradd -m -s /bin/bash carelink-cron
mkdir -p /home/carelink-cron/.secrets
chmod 700 /home/carelink-cron/.secrets
```

### 2. admin token を取得 + 環境変数に保存

```bash
# admin user で /api/v1/auth/login して JWT を取得 (= refresh token + access token)
# 長期実行用に refresh token を保存し、 cron 内で都度 refresh する設計が望ましいが、
# MVP として access token (有効期限 24h 推奨) を直接保存する.
# (本番では JWT TTL を audit 用に長めにする / または service account 化を検討)

echo "ADMIN_TOKEN=<貼り付け>" > /home/carelink-cron/.secrets/carelink.env
chown carelink-cron:carelink-cron /home/carelink-cron/.secrets/carelink.env
chmod 600 /home/carelink-cron/.secrets/carelink.env
```

### 3. cron 登録 (毎日 03:00 JST)

```bash
sudo -u carelink-cron crontab -e
```

以下 1 行を追加:

```cron
0 3 * * *  set -a; . /home/carelink-cron/.secrets/carelink.env; set +a; curl -sS -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json" -d '{"dry_run": false}' https://carelink.kaipoke-api.net/api/v1/admin/geocoding/audit >> /home/carelink-cron/audit.log 2>&1
```

### 4. 初回手動テスト (dry-run)

```bash
sudo -u carelink-cron bash -c 'set -a; . /home/carelink-cron/.secrets/carelink.env; set +a; curl -sS -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json" -d "{\"dry_run\": true}" https://carelink.kaipoke-api.net/api/v1/admin/geocoding/audit'
```

期待される response 例:

```json
{
  "locked": false,
  "checked": 86,
  "corrected": 0,
  "skipped_unchanged": 86,
  "errors": 0,
  "details": []
}
```

(= 全件整合済の状態. 100m 以上ずれた患者があれば `corrected > 0` で `details` に
詳細が入る.)

## モニタリング

- `/home/carelink-cron/audit.log` に response が追記される. 増えすぎないよう
  `logrotate` 設定推奨 (= weekly).
- `audit_logs` table で `action = 'auto_geocode_correction'` を grep すると
  どの患者の lat/lng が補正されたか追える.

## トラブルシュート

| 症状 | 原因 | 対処 |
|------|------|------|
| `locked: true` | 別ジョブが advisory lock 保持中 | 前回 job 完了を待つ. timeout (300秒) で必ず解放. |
| `errors > 0` | Geocoding API quota / network | 翌日 retry. quota の場合は GOOGLE_MAPS_API_KEY の請求枠を確認. |
| 全件 corrected (= 100%) | 住所と lat/lng が大幅乖離 | データ移行直後の正常動作. 2 回目以降は corrected≈0 が期待値. |
| 401 Unauthorized | ADMIN_TOKEN 期限切れ | 新しい token を再発行して `.secrets/carelink.env` を更新. |

## 廃止条件

- frontend `AddressGeocodeField` が住所変更時に lat/lng を必ず同時更新するため、
  人手で住所だけ書き換える経路が無くなった場合は本 cron は不要.
