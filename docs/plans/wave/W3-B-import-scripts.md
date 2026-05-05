# W3-B. 初期データ import スクリプト群

**実装 commit**: `8edeff2` (2026-05-05)
**ドメイン**: D1 (Backend) / Phase 2

## 概要

既存 GAS / Excel (Sample 2) で管理されていたマスタを CareFlow PostgreSQL
へ移植するための python スクリプト 8 本 + orchestrator を整備する。
Wave 3-A で確定した schema を前提に、idempotent な upsert 戦略で何度でも
再実行できる構造とする。本番初回投入用と運用後の差分追加用の双方を兼ねる。

## 実装範囲

- **共通基盤 `scripts/_import_utils.py`**: `iter_rows`, `cell`, `clean_str`,
  `parse_bool`, `build_parser`, `print_summary` 等 + dispose_engine wrapper
- **エンティティ別 7 スクリプト**:
  - `import_patients.py` (患者マスタ)
  - `import_staff.py` (スタッフマスタ)
  - `import_users.py` (管理者ユーザ + 自動生成 temp password CSV 出力)
  - `import_weekly_pattern.py` (患者の通常週パターン)
  - `import_special_weeks.py` (特別週ヘッダ + 明細)
  - `import_staff_events.py` (スタッフイベント / 会議等)
  - `import_staff_overrides.py` (その週だけの休み)
- **orchestrator `run_initial_import.py`**: 上記を依存順に一括実行 + 各
  スクリプトの summary 集計

## 関連 commit

- `8edeff2` feat(W3-B): 本体 (8 ファイル + 1757 行)
- `07fb2dd` fix(scripts): import 全 8 本の asyncpg event-loop cleanup を
  同一 loop 化 (再実行時のハング解消)
- `0a6db00` feat(W4-G): import 正規化強化 (weekday_priority 空文字 → 低、
  frequency_per_week=0 → null) + `import_users.py` に `--out` 必須化 +
  chmod 0600

## テスト被覆

- `test_import_patients_normalisation.py`
- `test_import_weekly_pattern_normalisation.py`
- 実 Sample 2.xlsx に対しては手動 dry-run + 本番投入で検証

## 残課題 / 次 Wave 移譲

- 旧 GAS UI からの差分連携 (運用中の patient master 変更追従) は Phase 5
  (Wave 5 以降) で kaipoke-api 連携と統合検討
- import_users が出力する `initial_users.csv` は git 管理外。受け渡し方法
  (1Password 等) は運用 runbook に追記要 (W5-F の本作業で対応済)
