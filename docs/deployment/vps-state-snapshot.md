# VPS 実態スナップショット

**取得日時**: 2026-05-05
**対象**: srv1300618 (Hostinger Malaysia, 72.60.211.213)
**目的**: CareLink デプロイ前の前提確定 (Codex Open Questions の回答)

## Tunnel & Cloudflared

> **重要 (2026-05-05 実測)**: cloudflared tunnel は **Cloudflare ダッシュボードのリモート管理モード**で動作中。ローカル `/etc/cloudflared/config.yml` 編集はランタイムに反映されない。

### ローカルファイル (informational only)
`/etc/cloudflared/config.yml`:
```yaml
tunnel: ff143e2a-65a9-468d-a8dd-96e56e690ae7
credentials-file: /root/.cloudflared/ff143e2a-65a9-468d-a8dd-96e56e690ae7.json

ingress:
  - hostname: kaipoke-api.net
    service: http://127.0.0.1:5000
  - hostname: linebot.kaipoke-api.net
    service: http://127.0.0.1:18789
  - hostname: novnc.kaipoke-api.net
    service: http://127.0.0.1:6080
  - service: http_status:404
```

### 実際にロードされた設定 (`journalctl -u cloudflared | grep "Updated to new configuration"` 由来、version=4)
```json
{
  "ingress": [
    {"hostname":"kaipoke-api.net","service":"http://localhost:5000"},
    {"hostname":"novnc.kaipoke-api.net","service":"http://localhost:6080"},
    {"hostname":"linebot.kaipoke-api.net","service":"http://127.0.0.1:18789"},
    {"service":"http_status:404"}
  ]
}
```

### 検出方法
- ローカルファイル: `127.0.0.1:5000` / `127.0.0.1:6080`
- ダッシュボード版: `localhost:5000` / `localhost:6080` ← micro-difference!
- → ローカルとダッシュボードに drift があり、ダッシュボード版が優先される

**確定事項:**
- ワイルドカード `*.kaipoke-api.net` 不在 → `carelink.kaipoke-api.net` を新規追加で安全
- catch-all (`http_status:404`) は末尾に存在 → 必ず維持
- **carelink hostname の追加は Cloudflare Zero Trust ダッシュボード経由でのみ可能**
  - URL: https://one.dash.cloudflare.com → Networks → Tunnels → tunnel ID → Public Hostname tab → Add
  - Service URL は `localhost:18000` を指定 (frontend 単一 hostname、API は Next.js rewrites 経由)

## Open ports (host)

| Port | Bind | Process | 用途 |
|---|---|---|---|
| 80 | 0.0.0.0 | nginx (PID 941922,941921,941917) | HTTP 受付 (おそらく Cloudflared と独立した何か) |
| 8080 | 0.0.0.0 | docker-proxy → hermes-agent | hermes |
| 3000 | 127.0.0.1 | node (PID 721) | **既存ノードプロセス**(用途不明、要確認) |

**フリーポート (CareLink で使用予定):**
- 5432 (PostgreSQL container 内部、host 公開せず) ✅
- 18000 (host:frontend) ✅ 衝突なし
- 18001 (host:backend) ✅ 衝突なし
- 443 (Cloudflared 経由のため host 不要) ✅

⚠️ **注意**: `127.0.0.1:3000` で listening している node プロセス (PID 721) は CareLink frontend container 内部の port 3000 とは別物。我々は host port 18000 を使うため衝突しない。

## Docker containers

| Name | Image | Ports |
|---|---|---|
| hermes-agent | hermes-agent-hermes | 0.0.0.0:8080 |
| openclaw-src-openclaw-gateway-1 | openclaw:local | 0.0.0.0:18789-18790 |
| kaipoke-api | playwrighttest1-kaipoke-api | 0.0.0.0:5000, 6080, 8443 |
| obsidian-couchdb | couchdb:3.3 | 5984 |
| n8n-n8n-1 | n8nio/n8n | 5678 |

**確定事項:**
- 既存 PostgreSQL コンテナ無し → 新規 carelink-postgres は安全
- kaipoke-api は port 5000 と 6080 を host 公開 (既存 cloudflared 経由 + 直接アクセス両用)
- カイポケ Sample データ取得用の openclaw が 18789-18790 占有
- 我々の 18000/18001 は競合無し

## Docker networks

```
bridge                      (default)
hermes-agent_default
host
line-webhook_default
n8n_default
none
obsidian-livesync_default
openclaw-src_default
playwrighttest1_default
```

**確定事項:**
- INV-4 監査通り `kaipoke-net` は **存在しない**
- 我々は default `bridge` を使う方針 (INV-4 結論と一致)
- 各サービスは独立 network で隔離されている

## Disk & Memory

```
/dev/sda1: 97G total, 66G used, 32G avail (32GB free, 33%)
Mem: 7.8GB total, 2.7GB used, 4.5GB buff/cache, 4.8GB available
Swap: 0B (無効)
```

**確定事項:**
- 32GB 空き → CareLink (postgres + backend + frontend image + DB 永続) で十分
- 4.8GB 空きメモリ → backend (256MB) + frontend (512MB) + postgres (512MB) で 1.3GB、余裕あり
- swap 0 → メモリ枯渇時は OOM killer。compose の `mem_limit` 設定を要検討

## API 公開方針 (Codex Open Question 3 への回答)

**採択: 同一ホスト `/api/v1/*` 方式**

理由:
1. NextAuth が同一オリジン経由で session cookie を扱う設計
2. 別ホスト `api.carelink...` だと CORS 設定が必要、`fetch` のクロスオリジン制限も発生
3. Cloudflared は `path:` ルールで host 内 path 振り分けが可能 (G7 fix で実装済)
4. DNS レコード追加 1 個で済む (`carelink` の CNAME のみ)

実装:
- `cloudflared-config-fragment.yml` に path-based ingress 2 件 (api → 18001, それ以外 → 18000)
- `frontend/next.config.js` に `rewrites()` で server-side 呼び出しの fallback 経路 (G7 で実装済)

## Cloudflare DNS 追加（VPS 上ではなく Cloudflare 管理画面で）

`carelink` の CNAME を kaipoke-api.net Zone に追加:
- Type: CNAME
- Name: `carelink`
- Target: `ff143e2a-65a9-468d-a8dd-96e56e690ae7.cfargotunnel.com`
- Proxy status: Proxied (orange cloud)
- TTL: Auto

確認:
```bash
dig +short carelink.kaipoke-api.net
# → Cloudflare の anycast IP が複数返る
```

## 既知の懸念事項

1. **port 80 の nginx**: 用途不明。kaipoke-api コンテナの一部か、別途立てた reverse-proxy か要確認。CareLink には影響しないが、もし将来 frontend を 80 で公開する選択肢を取る場合は要調査
2. **port 3000 の node**: PID 721 のプロセスが何か特定できていない。CareLink container は 18000:3000 マッピングなので衝突しないが、`docker run -p 3000:3000` のような直接コマンドはエラーになる
3. **既存 kaipoke-api の Postgres 永続化方法**: 不明。kaipoke-api 内部で SQLite かもしれないし、別 DB かもしれない。CareLink は独立 Postgres コンテナで運用するため依存無し

## 次アクション

このスナップショットを基に、deployment artifacts は以下を確認済み:
- ✅ cloudflared fragment は安全 (ワイルドカード無し、catch-all 末尾)
- ✅ ポート 18000/18001/5432 (内部) 衝突無し
- ✅ Docker network default bridge で OK
- ✅ ディスク・メモリ余力あり

実 VPS デプロイは `runbook.md` Phase A〜J を順に実行すれば良い。
