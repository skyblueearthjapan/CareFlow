# W4-D. モバイル機能補完 (写真 / 通知 / シフト希望)

**実装 commit**: `69163ce` (2026-05-05)
**ドメイン**: D3 (Mobile) + D1 (3 リソース schema) / Phase 3

## 概要

Wave 2-C で導入したモバイル 4 画面の "準備中" UI を解消するため、独立した
3 機能を 1 alembic リビジョンで追加する。各機能は他に依存せず、
router / model / schema / UI をセット投入する。スタッフが現場から写真
アップロード / 通知確認 / シフト希望提出を完結できる状態にする。

## 実装範囲

- **写真アップロード `visit_photos`**:
  - `POST /api/v1/visits/{visit_id}/photos` (multipart/form-data)
  - `GET /api/v1/visits/{visit_id}/photos` (visit メンバ + admin)
  - `GET /api/v1/visits/{visit_id}/photos/{id}/download`
  - `DELETE /api/v1/visits/{visit_id}/photos/{id}` (uploader 本人 + admin)
- **通知 `notifications`**:
  - `GET /api/v1/notifications?unread_only=&limit=` (自分宛のみ)
  - `POST /api/v1/notifications/{id}/read` (未読 → 既読)
  - `POST /api/v1/notifications` (admin が任意ユーザ宛作成、W6 producer
    実装待ちの暫定エンドポイント)
- **シフト希望 `shift_requests`**:
  - `POST /api/v1/staff/{staff_id}/shift-requests` (本人/admin)
  - `GET /api/v1/staff/{staff_id}/shift-requests` (本人/admin/manager)
  - `POST /api/v1/shift-requests/{id}/status` (approve/reject、admin/manager)
- **alembic 0006_w4d_mobile_features**: 上記 3 テーブル追加
- **Frontend**: `/m/today` に写真アップロードボタン、`/m/me` に通知一覧 +
  シフト希望フォーム

## 関連 commit

- `69163ce` feat(W4-D): 本体
- `d526b59` fix(alembic): W4-D / W4-F の並列 head を 0008 merge revision
  で統合

## テスト被覆

- backend: 各リソース pytest (`test_visits.py` 拡張 + 新規テスト)
- 写真 storage は当面 backend container 内 `/var/lib/carelink/photos/` に
  保存 (object store 化は別 sprint)

## 残課題 / 次 Wave 移譲

- 通知の producer (visit 割当変更時 / シフト承認時に自動生成) は W6 で実装
- 写真の object store (S3 互換) 移行はリソース消費を見て判断
- iOS Safari の multipart upload で稀に CORS preflight 失敗するケース
  (現状回避策: backend 側 `Accept-Encoding` 緩和) を W5-B で監視追加
