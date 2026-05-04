# INV-4: VPS 実稼働状態 検証レポート

**対象**: srv1300618.hstgr.cloud (72.60.211.213) / Ubuntu 22.04 LTS / Linux 5.15.0-171
**検証日時**: 2026-05-04 15:33 UTC / **Uptime**: 60日 5時間 / **Load**: 0.07/0.09/0.04

---

## 1. システムスペック（実測）

| 項目 | 実測値 | 備考 |
|---|---|---|
| CPU | **AMD EPYC 9354P 2-core** (`nproc=2`) | 32コア物理の2vCPU割当 |
| メモリ | **7.8 GiB total / 4.8 GiB available / 2.7 GiB used** | **Swap 0B**（未設定） |
| ディスク | `/dev/sda1` 100GB / **使用 66GB / 残 32GB / 68%** | 単一ボリューム、別ディスクなし |
| Load平均 | 0.07 (15分) | 極めて低負荷 |

`lsblk`: sda(100G) 一本のみ、RAID/別ディスクなし。

---

## 2. Docker コンテナ稼働実態（6台）

| Name | Image | Status | MEM | ポート |
|---|---|---|---|---|
| **kaipoke-api** | playwrighttest1-kaipoke-api | Up 2 weeks (healthy) | 138 MiB | 5000, 6080, 8443 |
| hermes-agent | hermes-agent-hermes | Up 5 days | 298 MiB | 8080 |
| openclaw-gateway-1 | openclaw:local | Up 5 days (healthy) | 612 MiB | 18789-18790 |
| obsidian-couchdb | couchdb:3.3 | Up 2 months | 53 MiB | 5984 |
| n8n-n8n-1 | n8nio/n8n | Up 2 months | 326 MiB | 5678 |
| line-webhook | node:20-alpine | **Exited (255) 2ヶ月前** | - | 3001 |

**合計使用メモリ ~1.43 GiB / CPU合計 3.3%**

### kaipoke-api 詳細
- ヘルスチェック: healthy（5エントリ）
- ネットワーク: `playwrighttest1_default` (172.21.0.0/16) のみ
- **`kaipoke-net` は実在しない**（D5計画書の前提誤り）
- マウント10件 (bind): `/root/PlaywrightTest1/{credentials,lib,artifacts,state.json,commands,logs,.env,config,data,api_server.py}` を `/app/` 配下に
- Volume: `hermes-agent_hermes-data`, `n8n_n8n_data` のみ（kaipoke は名前付きVol なし）

---

## 3. cloudflared（実行形態の真実）

- **形態**: **systemd ネイティブ**（Docker でも Dashboard でもない）
  - `/etc/systemd/system/cloudflared.service`、PID 1245914、Up 1ヶ月12日、Memory 23.5M
  - 実行: `/usr/bin/cloudflared --no-autoupdate --config /etc/cloudflared/config.yml tunnel run`
- Tunnel ID: `ff143e2a-65a9-468d-a8dd-96e56e690ae7`
- 公開ホスト3つ (`/etc/cloudflared/config.yml`):
  - `kaipoke-api.net` → `127.0.0.1:5000` (Flask)
  - `linebot.kaipoke-api.net` → `127.0.0.1:18789` (openclaw)
  - `novnc.kaipoke-api.net` → `127.0.0.1:6080` (noVNC)
- 直近警告: QUIC `198.41.200.23` 経由で datagram failure → 自動 reconnect で `sin15` 復旧、実害なし

---

## 4. 稼働実績（最終 RPA 動作）

| 指標 | 最終時刻 | 評価 |
|---|---|---|
| `artifacts/` 最新PNG | **2026-03-04 00:15 (61日前)** | 2ヶ月以上 RPA 未実行 |
| `state.json` 更新 | **2026-03-08 02:02 (57日前)** | 同上 |
| `state.json` Access | 2026-04-29 04:57 | 何かが読みには来ている |
| `logs/` | **空**（.gitkeep のみ Feb 8 から変化なし） | ファイルログなし |
| `kaipoke-api` 内部ログ | 2026-04-18 01:19 起動以降サイレント | コンテナ生きているが業務ゼロ |
| ヘルスチェック | **全 OK**（毎日09:00 連続15日） | 監視は機能 |

**結論**: API コンテナは生きているが、**実 RPA 業務は2ヶ月停止状態**。/api/status だけが叩かれている。

---

## 5. cron / バックアップ / 監視

- crontab: `*/5 * * * * /usr/local/bin/healthcheck-kaipoke.sh` 1行のみ
- healthcheck-kaipoke.sh: Flask ping NG → docker restart、cloudflared inactive → restart、外部HTTPS失敗 → restart
- **バックアップ実態**: `/mnt` 空、`/var/backup` `/backup` 不在
- 単発手動: `/root/openclaw-backup-20260204-1131.tgz` (106MB) 3ヶ月前のみ
- **定期バックアップなし、別ディスクなし**

---

## 6. 公開ポート / TLS

```
22(ssh) 80(nginx) 5000(kaipoke) 5678(n8n) 5984(couchdb)
6080(noVNC) 8080(hermes) 8443(kaipoke) 18789-18790(openclaw)
3000(node 127.0.0.1) 20241-20242(cloudflared metrics)
```

- **TLS終端は cloudflared のみ**（443 直接公開なし、Let's Encrypt ゼロ、caddy 不在）
- nginx は `linebot.kaipoke-api.net` 80番のみ→127.0.0.1:3001（停止中）にプロキシ → 事実上死に設定
- .env は5箇所分散、`/root/PlaywrightTest1/.env` には3キーのみ

---

## 7. CareLink 配備時の余力評価

| リソース | 残量 | CareLink要件 | 同居可否 |
|---|---|---|---|
| メモリ | 4.8 GiB available | 1.9 GiB | 同居可（残 2.9GB） |
| CPU | 2 vCPU、idle | **2.2コア要求 > 2 vCPU** | **同居不可** |
| ディスク | 32 GB free / **Build Cache 40.7GB 回収可** | 数GB | 余裕あり |
| Swap | **0B** | - | リスク高、swapfile 追加要 |

### D5 計画書の前提と実態の差

| 計画書前提 | 実態 | 影響 |
|---|---|---|
| `kaipoke-net` 想定 | **不在**（playwrighttest1_default のみ） | docker-compose `external: true` 参照は失敗 |
| dev 環境並走 | **2 vCPU しかなく不可能** | 別 VPS or 本番のみ |
| RPA 稼働中 | **2ヶ月停止状態** | 検証データ取得困難 |
| TLS（Let's Encrypt 等） | **証明書ゼロ、cloudflared一本依存** | CareLink も Cloudflare Tunnel 経由が現実的 |
| 定期バックアップ | **存在しない** | CareLink 投入前にバックアップ戦略必須 |
| Swap | **0B** | OOM リスク、swapfile 推奨 |
| Docker disk | **48.83GB image + 40.72GB cache** | system prune で大幅回収可 |

### CareLink 配備推奨アクション
1. **CPU**: 2 vCPU で 2.2 コア要求は無理。Hostinger プランアップグレード（4 vCPU 以上）または別 VPS
2. **Swap**: 4GB swapfile 追加（OOM 保険）
3. **Docker GC**: `docker builder prune` で 24GB、未使用イメージ整理で 40GB 回収
4. **kaipoke-net**: external network 作成 or CareLink を `playwrighttest1_default` に attach
5. **バックアップ**: `/root/PlaywrightTest1` + n8n/couchdb volume の日次 tar + 別ストレージ転送
6. **cloudflared config.yml** に CareLink ingress 行追加（最小コスト）
7. **既存 RPA 復活**: state.json/artifacts が2ヶ月停止のため、CareLink リリース前に動作再確認

---

## 主要ファイルパス

- `/etc/cloudflared/config.yml` — Tunnel ingress
- `/etc/systemd/system/cloudflared.service`
- `/usr/local/bin/healthcheck-kaipoke.sh` — 5分毎監視
- `/var/log/kaipoke-healthcheck.log`
- `/root/PlaywrightTest1/` — Kaipoke RPA (438MB)
- `/etc/nginx/sites-enabled/linebot` — 死に設定
