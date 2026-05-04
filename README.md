# CareLink

訪問看護スケジューリングアプリ。既存 GAS UI（careflow-scheduler）+ VPS Python/Playwright（PlaywrightTest1）の後継として新規構築。

## アーキテクチャ

- **Frontend**: Next.js 15 (App Router) + TypeScript + Tailwind CSS + shadcn/ui
- **Backend**: FastAPI + SQLAlchemy 2.0 + asyncpg
- **DB**: PostgreSQL 16
- **デプロイ**: Hostinger Malaysia VPS（既存 kaipoke-api と同居）
- **公開URL**: `https://carelink.kaipoke-api.net`（Cloudflare Tunnel）
- **認証**: NextAuth.js + メール/パスワード
- **AI**: Gemini API（自然言語入力 → 構造化）
- **デザイン**: Warm & Human（Teal #0D9488 + Terracotta #D97706 + Cream #FAF7F2）

## ディレクトリ構造

```
CareLink/
├── backend/           # FastAPI バックエンド
├── frontend/          # Next.js フロントエンド
├── docs/              # 設計仕様書・実装計画・監査レポート
│   ├── design/        # UI 設計仕様（11ファイル）
│   ├── plans/         # 実装計画（D1〜D5 + クロスレビュー + Codex）
│   └── audit/         # 既存システム再調査（INV-1〜5 + レビュー）
├── Sampledata/        # 参考用サンプルデータ（.gitignore で除外）
└── README.md
```

## 開発ステータス

**Phase 0（着手前準備）進行中**：
- 既存 PlaywrightTest1 のクリティカルバグ修正
- CareLink Backend / Frontend 基盤実装
- VPS 実態調査済み

詳細: `docs/audit/MASTER-AUDIT-REPORT.md` 参照。

## ライセンス

Private（社内利用）
