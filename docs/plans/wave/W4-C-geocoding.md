# W4-C. Google Maps Geocoding 中継 + 自動補完

**実装 commit**: `b8edd5e` (2026-05-05)
**ドメイン**: D4 (Integrations) + D3 (3 フォーム) / Phase 3

## 概要

design 11 で「Phase 5 で住所→緯度経度の自動変換を予定。現状は手動入力。」
と書かれていた hint テキストを実装で解消する。Patient / Staff / Office の
3 フォームすべてで、住所欄を変更すると 800ms debounce で
`POST /api/v1/geocode` を呼び、lat/lng を自動補完する。Google Maps API
結果は `geocoding_cache` テーブルにキャッシュし、quota 消費を抑える。

## 実装範囲

- **Backend**:
  - `services/geocoding/client.py`: httpx async + Google Maps Geocoding
    REST + `GeocodingServiceError` / `GeocodingQuotaExceeded` 例外階層
  - `api/v1/geocoding.py`: `POST /api/v1/geocode` (cache HIT/MISS/
    force_refresh) + `GET /cache` (admin/manager only)
  - `schemas/geocoding.py`: GeocodeRequest/Response + GeocodingCacheRead
  - 入力 address を NFKC 正規化 + SHA-256 hash で cache key 化
  - 並行 INSERT IntegrityError → rollback + re-SELECT で 500 回避
  - 503 OVER_QUERY_LIMIT 時 `Retry-After: 60` ヘッダ
- **Frontend**:
  - `components/AddressGeocodeField.tsx`: 共通ラッパ (controlled +
    React Hook Form 両対応)
  - 住所変更時 `useGeocodeOnAddressChange(debounce 800ms)` で API 呼出
  - lat/lng フィールドの onFocus/onBlur で focus 検知 → 編集中は API
    結果で上書きしない (ユーザの手入力を尊重)
  - Patient / Staff / Office の 3 フォーム既存 lat/lng 入力を本コンポに
    置き換え

## 関連 commit

- `b8edd5e` feat(W4-C): 本体
- `198ced5` 住所変更時に緯度経度を強制更新するよう修正 (post-W4-C 微修正、
  別リポジトリ side mirror)

## テスト被覆

- 11 ケース pytest smoke: cache HIT/MISS/quota/upstream/RBAC/hash 安定性
- 本番では Patient 新規作成時に 1 回手動 geocode 成功を確認

## 残課題 / 次 Wave 移譲

- Google Maps API key の rotation runbook は W5-F (本タスク) で追記
- jurisdiction (拠点 - 市区町村 mapping) との照合は別 sprint
- API quota 監視 (日次上限通知) は Wave 5-B 監視 cron に追加検討
