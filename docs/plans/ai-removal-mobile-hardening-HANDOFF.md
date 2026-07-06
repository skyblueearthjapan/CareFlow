# 引き継ぎ書：UI棚卸し・AI全撤去・モバイル現場対応（2026-07-05 セッション）

作成 2026-07-06 / **本番 HEAD = `783a867`** / DB = **migration 0055** / healthz 正常。
**次のエージェントはまずこのファイルを読む。**

関連正典:
- 前セッション: `docs/plans/kaipoke-rpa-revival-HANDOFF.md`（カイポケRPA — 残作業は §6 参照）
- 自動メモリ索引（`MEMORY.md`）の先頭5件が本セッションの詳細メモ

---

## 0. TL;DR — このセッションで何をしたか

PO と対話しながら **8 コミットすべて本番デプロイ済み**。大きく3系統:

1. **UI棚卸し**: 死んだUI（受入目安トグル）削除・**AI機能（Gemini）全撤去**
2. **モバイル開放**: 現場ボード/受け入れ枠を staff 含む全ロール閲覧可に
3. **モバイル現場対応（PO実機テスト起点の障害修正）**: QR読取クラッシュ根治・
   写真アップロード/表示の修復・PWA更新事故の自己回復・下タブ重なり・文言/挨拶/上部集約

| commit | 内容 |
|---|---|
| `4d43338` | 受入目安トグル削除（W16以降実体なしの凡例のみ。/acceptance が完全代替） |
| `847ed18` | **AI全撤去**（FAB/モーダル/ヘルプ/AIログ/BE ai.py/gemini_client/**migration 0055**） |
| `c28fdba` | 現場ボード(/m)・受け入れ枠(/m/acceptance) 全ロール閲覧化（staffは閲覧専用UI） |
| `fe74995` | PWA: デプロイ跨ぎ旧チャンク404→自己回復（SW controllerchange リロード＋error.tsx） |
| `8c9837c` | モバイル下タブに最下行が隠れる→100dvh 化＋余白 |
| `d5f052b` | **QR読取成功で必ずクラッシュ→根治**・写真UP500（volume未マウント）・Layer1 note非表示 |
| `211733e` | 写真表示の認証エラー（AuthedPhoto）・QR読取性向上・GPS失敗理由ヒント |
| `783a867` | 内部note非表示を一覧カードへ横展開・挨拶をスタッフ名に・現場ボード上部集約 |

---

## 1. AI機能 全撤去（最大の変更・破壊的）

**PO決定**: AI入力（自然言語→Gemini→構造化→申請）をPC/モバイルから完全削除。

- 削除: AiFab/MobileAiFab（両layoutのFAB）・AiInputModal・/help/ai・/integrations/ai(AIログ)・
  BE `/api/v1/ai/*`・gemini_client.py・AiInterpretLog・AiContextType・CSPのgenerativelanguage・
  GEMINI設定/依存/デプロイ文書・useDraggableFab
- **DB migration 0055**: `ai_interpret_logs` テーブルと `pending_requests.ai_interpret_log_id` 列を
  DROP（両方とも本番0行を確認してから実施。downgrade で再作成可）
- 本番 `.env` から GEMINI 2行除去（バックアップ `.env.bak.remove-gemini-20260705`）
- **残すと決めたもの（混同注意）**: 申請・承認基盤 `pending_requests` 一式は**AI専用ではなく共用**
  （現場ボード手動申請=FieldSheets が producer・PC「申請履歴」/現場ApprovePanel が consumer）。
  PO選択で存続。ただし本番0行=実運用実績なし → 将来の棚卸し候補
- 独立レビュー APPROVE（CRIT/HIGH/MED 0・LOW 7件全反映）
- **要ユーザー操作（未完）**: Google AI Studio 側の GEMINI API キー自体の失効（伝達済み）

## 2. モバイル閲覧開放（c28fdba）

- `(field)/m` と `/m/acceptance` の admin/manager ガード撤去。MobileShell の導線を全員に表示
- 編集系は `canEditKarte` で一元制御（提案/承認/患者管理/直接配置/カルテ編集 → staff 非表示）
- BE: `GET /schedule/v2/board` と `GET /scheduling-settings` に staff 追加（read-only。書込系は不変）
- 副産物: `(field)/m/__tests__/page.test.tsx` の既存26失敗（useSchedulingSettings 未モック）を
  修正しスイート全緑化

## 3. モバイル障害修正（PO実機テスト起点・最重要の技術知見）

### 3-1. QR読取成功で必ずクラッシュ（d5f052b で根治）
- **真因**: html5-qrcode の `stop()` は停止済みのとき **生文字列を同期 throw** する。
  読取成功→stop→unmount 時の cleanup 二重 stop で throw → React が文字列に
  `_componentStack` を付与しようとして TypeError → アプリ全体エラー境界落ち
- 「QRなしで記録」は無事（スキャナ稼働中で stop 成功）＝リリース以来未発覚だった理由
- 修正: `safeStop()`（同期throw＋非同期reject両吸収）＋二重stop回避
- **再現手法（資産・再利用可）**: `/login/` 配下に一時ページ（middleware素通し）＋
  Playwright chromium `--use-fake-device-for-media-stream --use-file-for-fake-video-capture=qr.y4m`。
  y4m は segno（pip）のQR行列から純Pythonで生成（ffmpeg不要）。dev サーバーで pageerror を
  捕ると真のスタックが取れる。デコード→token抽出まで自動検証できる
- PO実機で読取成功を確認済み（が「熱心にやったら」= 読み取りにくい → 211733e で
  videoConstraints 1280x720・fps15・qrbox75%・表示 min(80vw,320px) に改善。**実機再確認待ち**）

### 3-2. 写真アップロード/表示（W4-D以来 本番未動作だった）
- UP 500: 本番 compose に visit_photos volume が無く container 内に保存先ディレクトリ不存在
  → compose に bind mount 追加＋ホスト `chown 999:999`（container app uid）。WRITE_OK 検証済み
- 表示 `{"detail":"Authentication required"}`: 素の `<img src>`/`<a href>` が Bearer 必須の
  download API を直叩き → `AuthedPhoto`（認証fetch→blob→objectURL＋アプリ内ライトボックス）新設。
  **教訓: 認証必須APIを `<img>`/`<a>` に直接渡さない**

### 3-3. PWA デプロイ跨ぎ事故（fe74995）
- デプロイ毎に旧ハッシュチャンクが消える＋既存SWが旧キャッシュを即削除 → 開きっぱなし端末が
  ChunkLoadError で「Application error」固まり
- 3層対策: SW controllerchange→1回自動リロード / `app/error.tsx`（チャンク系1回自動リロード＋
  再読み込みボタン）/ `app/global-error.tsx`（最終防波堤・インラインスタイル）
- **以後のデプロイは開いているタブも自動で新ビルドに乗り換わる**（Ctrl+Shift+R 案内ほぼ不要に）。
  副作用: デプロイ瞬間に未保存入力が消え得る

### 3-4. その他モバイル
- 下タブ重なり: MobileShell を `supports-[height:100dvh]:h-dvh` 化＋下余白+16px（8c9837c）
- 内部note（`Layer1: fixed pattern`/`reset_to_fixed_v2…`）非表示: `lib/visit-note.ts` に共通化し
  訪問詳細＋一覧カード（MobileVisitCard）両方に適用。人間向け日本語note は表示継続
- ホーム挨拶: ログインコード(s002)→スタッフ氏名（useMyShifts 経由 GET /staff/{id}・フォールバックあり）
- 現場ボード上部集約: ブランドヘッダー撤去、患者管理/提案/承認（バッジ付きピル）をトップバー右に
  移設・「← 戻る」短縮。縦 約60px 拡大。承認モード=アンバーピル
- GPS失敗: 理由（権限拒否/測位不能/タイムアウト）を捕捉しプレビューに対処ヒント表示。
  測位なしでも記録可の仕様は不変

---

## 4. コード地図（本セッションの主要ファイル）

**FE**: `components/mobile/QrScanner.tsx`（safeStop）・`components/mobile/AuthedPhoto.tsx`（新規）・
`lib/visit-note.ts`（新規）・`app/(mobile)/m/today/[visitId]/page.tsx`（プレビュー/GPS/写真/ライトボックス）・
`components/mobile/MobileVisitCard.tsx`・`components/MobileShell.tsx`（dvh/導線）・
`components/field/FieldBoard.tsx`（BackToAppBar集約・canEdit ガード）・`app/error.tsx`/`app/global-error.tsx`（新規）・
`app/layout.tsx`（SW controllerchange）・`app/(field)/m/page.tsx`・`app/(field)/m/acceptance/page.tsx`（ガード撤去）

**BE**: `api/v1/schedule_v2.py`（board に staff）・`api/v1/scheduling_settings.py`（GET に staff）・
`alembic/versions/0055_drop_ai_interpret.py`・AI関連は全削除済み

**インフラ**: `docs/deployment/docker-compose.production.yml`（backend に visit_photos volume 追加）・
VPS `/opt/carelink/data/visit_photos`（chown 999:999 済）

---

## 5. 検証・プロセス（本セッションで踏襲した規約）

- 全コミット: tsc/lint/prettier/ruff 緑・関連 vitest/pytest pass を確認してからデプロイ
- 既存failとの切り分けは `git stash` で base HEAD 比較（Python3.14 UUID系 env fail・
  CourseDayTablePanel系の「No QueryClient」fail は既存問題として残存）
- 大型変更（AI撤去）は独立 code-reviewer レビュー→全指摘反映→コミット
- デプロイ: pg_dump→pull→build→(migrate)→recreate→healthz（毎回バックアップ取得済み）
- PowerShell here-string でコミットメッセージが化ける → `git commit -F <file>` を使う
- ローカル Windows の `next build` は最終段 standalone symlink EPERM で落ちるが
  「Compiled successfully」まで出れば実質OK（Docker/Linux では起きない）

---

## 6. 残作業・次の候補

### A. 実機確認待ち（PO）
1. QR読取性改善（211733e）後の実機テスト — まだなら追加改善（エンジン差し替え=jsQR 等も選択肢）
2. 写真のサムネイル表示・タップ拡大（AuthedPhoto）
3. GPS ヒント表示（位置情報許可オフの端末で黄色ヒントが出るか）
4. 現場ボード新トップバー（承認バッジ・アンバー表示含む）の使用感

### B. 明示的な未完タスク
5. **GEMINI API キーの Google 側失効**（ユーザー操作待ち）
6. `docs/HANDOFF.md`（2026-06-14 の全般引き継ぎ）が **untracked のまま** — コミットするか意図確認
7. 申請・承認基盤（pending_requests・本番0行）の要否棚卸し（現場ヒアリング後）

### C. 前セッションからの継続（カイポケRPA — 詳細は kaipoke-rpa-revival-HANDOFF.md §7）
8. 要手当データ（髙梨/槇 登録・全職種 backfill）
9. **実 apply（dry_run=false）初回**（PO監督下・noVNC監視・適用後検証つき）
10. 適用後検証（post-apply verification）の実装

---

## 7. 気になる点（リスク）

1. **QR読取性は端末依存**: 改善は入れたが実機未確認。ダメなら html5-qrcode 自体の差し替え
   （jsQR + getUserMedia 自前実装）を検討。ただし VPS ビルドの npm 取得可否を先に確認すること
2. SW 自動リロードはデプロイ瞬間に未保存フォーム入力を失わせ得る（クラッシュよりマシの判断）
3. 現場ボードの提案/承認がアイコン中心のピルになった — 現場が見つけられないようなら文言復活
4. staff への閲覧開放でスケジュール全体・患者名が staff にも見える（PC /schedule と同等の情報で
  設計意図どおりだが、現場から要望があればマスキング検討）
