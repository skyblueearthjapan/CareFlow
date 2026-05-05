# W4-E. UUID 生入力 → Combobox 化 + zodResolver 有効化

**実装 commit**: `a4257c5` (2026-05-05)
**ドメイン**: D3 (Screens) / Phase 3

## 概要

Wave 2 で TODO のまま残っていた「UUID を手で貼ってください」系
プレースホルダを廃止し、検索可能 Combobox + zodResolver による
クライアント検証に統一する。あわせて、特別週フォーム等で react-hook-form の
入力値 (string) と API wire schema (coerce 済 number / date) の型差異で
zodResolver が validate を全失敗させていた問題を、form 用 schema を分離
することで解消する。

## 実装範囲

- **共通 Combobox 3 種** (`frontend/components/master/`):
  - `OfficeCombobox` (`useOffices()`)
  - `StaffCombobox` (`useStaffList()`、mentor / 担当 共用)
  - `PatientCombobox` (`usePatients()`、active 患者のみ)
  - shadcn/ui の Command + Popover ベース、検索文字列で名前 / 拠点コード /
    カナ をインクリメンタル絞込
  - value は UUID 文字列を return / accept、表示ラベルは内部 resolve
  - 呼び出し側は React Hook Form `Controller` 1 行で済む
- **zodResolver 修正**:
  - `frontend/lib/schemas/special-week.ts` に form 用 schema を追加
  - 入力 string を z.coerce で適切型に変換するレイヤを介在
  - 全フォームで `zodResolver(formSchema)` を有効化

## 関連 commit

- `a4257c5` feat(W4-E): 本体

## テスト被覆

- frontend Playwright E2E (W5-C) で各 Combobox の検索 → 選択 → 保存を
  通り抜け確認
- backend は元々 zod 不在のため変更なし

## 残課題 / 次 Wave 移譲

- Combobox の virtualization (1000 件超のリスト) は Wave 6 で react-virtuoso
  導入を検討 (現状は patient/staff いずれも数百件規模で問題なし)
- a11y (Combo の ARIA combobox role) は shadcn/ui 既定値で OK と判断
