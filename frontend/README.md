# CareLink Frontend (D2 Foundation)

Next.js 15 (App Router) + TypeScript strict + Tailwind CSS 3.4 + shadcn/ui スタイル の雛形。
本ディレクトリは D2 フェーズの **Frontend Foundation** スケルトンであり、画面の作り込みは含まない。

## セットアップ

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

ブラウザで http://localhost:3000 を開くと `/dashboard` にリダイレクトされる。

## 主要スクリプト

| コマンド | 用途 |
|---|---|
| `npm run dev` | 開発サーバ起動 |
| `npm run build` | プロダクションビルド |
| `npm run start` | プロダクション起動 |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run format` | Prettier 全体整形 |

## ディレクトリ構成

```
app/
  (auth)/login/        ログイン
  (app)/               AppShell でラップされる本体
    dashboard/
    patients/
    staff/
    schedule/
  api/auth/[...nextauth]/   NextAuth Credentials Provider
components/
  AppShell.tsx         サイドバー232/72px + ヘッダー60px + main
  Sidebar.tsx
  Header.tsx
  MobileShell.tsx      768px未満のボトムタブ用シェル
  AiFab.tsx            右下固定FAB（Gemini入力）
  ui/                  Button / Input / Card 雛形
lib/
  auth.ts              NextAuth options
  api-client.ts        Bearer Token 添付 fetch wrapper
  utils.ts             cn() ヘルパー
styles/
  tokens.css           Warm & Human パレット
middleware.ts          認証ガード + ロール別ルーティング
types/
  next-auth.d.ts       session.user.role 拡張
```

## 設計参照

- `docs/plans/D2-frontend-foundation-plan.md`
- `docs/design/00-overview.md` 〜 `10-mobile.md`

## 注意事項（D2 Foundation スコープ）

- 実データ取得は `// TODO: fetch from BACKEND_API_BASE_URL` レベル
- shadcn/ui は CLI で取り込まず、Button/Input/Card 3点を直接コードで配置
- 2名体制以降のフローや週ビュー DnD 等は D3 担当
- AI 入力 FAB はガワだけ（モーダル本体は D4）
