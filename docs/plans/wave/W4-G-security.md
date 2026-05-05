# W4-G. Security headers + CORS guard + 防御強化

**実装 commit**: `0a6db00` (2026-05-05)
**ドメイン**: D1 (Backend) + D5 (DevOps) / Phase 3

## 概要

Phase 4 投入直前の防御層強化。Security headers middleware で OWASP 標準
ヘッダを返却し、CORS production guard で wildcard 設定での起動を阻止する。
本日 (2026-05-05) の本番障害学習として、alembic 未適用時に audit
middleware が login を巻き込んで 500 を返す問題が発生したため、専用
ハンドリングを追加。あわせて Claude review M2 指摘の `_coerce_positive_int`
の bool/float 防御、import スクリプトの正規化強化、
`initial_users.csv` の漏洩リスク低減を一括投入する。

## 実装範囲

- **Security headers middleware** (`middleware/security_headers.py`):
  - `X-Frame-Options=DENY`
  - `X-Content-Type-Options=nosniff`
  - `Referrer-Policy=strict-origin-when-cross-origin`
  - `HSTS` (Cloudflare 経由の TLS 終端を考慮した max-age 設定)
  - `Content-Security-Policy` (default-src 'self' + 必要 CDN allowlist)
- **CORS production guard** (`main.py`):
  - APP_ENV=production で wildcard `*` を含む CORS_ORIGINS を起動時拒否
  - 空も拒否 (frontend ドメイン明示必須)
- **audit middleware 防御**:
  - `sqlalchemy.exc.ProgrammingError` (table 不在等) 専用 try/except
  - 監査書込失敗が業務 API を巻き込まないよう warn-and-continue
- **その他防御**:
  - `allocate.py` `_coerce_positive_int`: bool 型 (Python では int の
    sub-class) と float NaN を弾く
  - `import_*.py` 正規化: weekday_priority 空文字 → 低優先、
    frequency_per_week=0 → null
  - `scripts/normalize_legacy_weekly_pattern.py`: 本番データ修正用
  - `import_users.py`: `--out` 必須化 + chmod 0600 + 既存 user 行除外で
    `initial_users.csv` の漏洩リスク低減

## 関連 commit

- `0a6db00` feat(W4-G): 本体
- `35dd0ac` feat(W5-C): 関連 backend tests 50 ケース追加 (本コミットの
  防御も含めて regress)

## テスト被覆

- `test_security_headers.py`: 各 header の設定確認
- `test_cors_production_guard.py`: production で wildcard / 空 CORS が
  startup 失敗
- `test_audit_middleware_resilience.py`: ProgrammingError でも 500 に
  ならない
- 11 新規テスト + 既存 228 全 PASS

## 残課題 / 次 Wave 移譲

- CSP の nonce 化 (現状 unsafe-inline を許容) は Wave 6 で Next.js 側と
  合わせて検討
- HSTS preload 申請は本番運用 6 ヶ月後に判断
- gitleaks pre-commit + CI workflow は W5-A で実装済 (P-21 完了)
