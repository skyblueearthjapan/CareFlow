# W1-E Diff Engine 移植 — Critic Review

**Reviewer**: oh-my-claudecode:critic (Opus, THOROUGH)
**Commit**: `565259e feat(backend): W1-E port diff engine + /api/v1/diff API + tests`
**Date**: 2026-05-05

## VERDICT: ACCEPT-WITH-RESERVATIONS

PT1→CareFlow 移植は機能的に正確、Codex 指摘 4件 (C-9/C-10/Bug C/Bug D) すべて保持。テスト件数 16 (PT1 と同等、parametrize 12 + 関数 4)。一方 API 層に運用リスクあり。

## Critical Findings

該当なし。

## Major Findings

### M1. ペイロード上限なし (DoS リスク)
- **Evidence**: `backend/app/api/v1/diff.py:29-46` で `current_csv`/`optimized_csv` に `max_length` 無し。`main.py` にもグローバル body size limit 無し
- **Why this matters**: 認証済 admin/manager が 100MB 文字列 2 本で OOM 可能
- **Fix**: `DiffRequest` の各 field に `Field(..., max_length=10_000_000)` + `@limiter.limit("5/minute")` を `compute_diff` に追加

### M2. tempfile 経由のラウンドトリップが不適切
- **Evidence**: `engine.py:830-845, 868-895` で content (string) を tempfile に書き、再オープンして読込。`io.StringIO` 直行で十分
- **Fix**: `parse_csv_from_content` を `io.StringIO` ベースに、`parse_kaipoke_csv` を「rows を受ける関数」「path を受ける wrapper」に分離

### M3. PT1 と CareLink で BOM 除去文字が異なる可能性
- **Evidence**: PT1 は `'﻿'`、CareLink は raw BOM 文字をリテラル埋め込み
- **Fix**: `engine.py:830,868,870` を `'﻿'` に置換 (diff レビュー耐性のため)

## Minor Findings

- `is_event` 判定が business_type 型不在で誤分類 (PT1 踏襲)
- `import os, tempfile` は M2 解消後 不要
- `parse_time` の `int(parts[0])` ValueError リスク
- `read_csv_auto_encoding` は OK (utf-8-sig → utf-8 → cp932 → shift_jis)

## What's Missing

- engine 単体テスト (Pass1〜Pass5 マッチングロジック) 未移植
- 空 CSV / ヘッダーのみ CSV テスト
- `validate_correction_data` のテスト + 利用箇所
- エンコーディング失敗の test
- `target_users` フィルタ動作の単体検証

## Verdict Justification

機能正確性は高く、Codex 4 件保持完璧。M1 が最大の懸念 (Hostinger VPS 単一構成で実害可能性)。Realist: cloudflared body size 制限の存在で軽減余地あり、admin 限定で外部攻撃直接刺さらないため CRITICAL ではなく MAJOR。

**ACCEPT 昇格条件**: M1 (size + rate limit) + M3 (BOM literal) 修正

## Open Questions

- Cloudflared/Nginx の body size 制限有無
- 将来 Playwright で `validate_correction_data` を呼ぶ計画
