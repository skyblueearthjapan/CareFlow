# 引き継ぎ書：スケジュール・アドバイザー一式（UI統一〜欠勤対応コース単位化）

作成 2026-07-03 / 本番HEAD = `63d6d61` / DB = migration **0050** / 前セッションの引き継ぎは
`docs/HANDOFF.md`（プロジェクト全体）と `docs/plans/staff-account-linking-HANDOFF.md`（スタッフログイン）。

このドキュメントは 2026-07-02〜03 の大規模セッション（訪問モニターUI統一 → スケジューリング正規化調査 →
スケジュール・アドバイザー Phase 0〜3 → P4/P5）の**全体像と現在地**。次のエージェントはまずこれを読む。

---

## 1. TL;DR — いま何がどうなっているか

- **製品の中核方針が転換済み**: 「毎週の全面最適化」ではなく **「固定スケジュール＋例外処理」を支える
  アドバイザー**（診る/囁く/弁える/節目に見直す）。全面最適化は「シミュレーション（比較専用）」に降格済み。
- **提案可否の権威は3層**（P4で確定・実装済み）:
  1. **📌ピン留め**（`PatientFixedVisit.is_pinned`。旧UI名「鍵」— P4-Bで全UI「ピン留め」＋赤丸頭ピンアイコンに統一）→ 一切提案しない
  2. **希望訪問スケジュール**（`Patient.weekly_pattern` = 患者から聞き取り済みの受け入れ可能範囲）→
     範囲内の候補は**確認不要**で提案（within_preference・曜日跨ぎも可）
  3. 範囲外 → 可動域フラグ（movability）のルールで要確認/制限。可動域セレクタはUI上「詳細設定」に格下げ済み
- **欠勤対応はコース単位**（P5）: 提案単位は「引き継ぎプラン」（丸ごと→AM/PM分担→Mgr→分散[最終手段]）。
  患者単位の個別選択は最終手段トグル内に温存。
- 全機能 本番稼働中・エラーなし。フロント変更後は現場で **Ctrl+Shift+R** 必須（Service Worker）。

## 2. 本番状態とデプロイ

- VPS `root@72.60.211.213` / `/opt/carelink` / https://carelink.kaipoke-api.net / ブランチ=develop
- migrations: `0046`(username) → `0047`(movability+suggestion_dismissals) → `0048`(visits.manual_staff_override)
  → `0049`(ck_sd_kind に swap) → `0050`(ピン由来 locked 残骸解放)
- **デプロイ手順の教訓（必読・実事故由来）**:
  1. `set -eo pipefail` を使う（`set -e` はパイプ左側の失敗を検知せず、migration失敗のままコンテナが起動した事故あり）
  2. **build → migrate → recreate の順**（`docker compose run` は旧イメージを使うため、build 前に migrate すると修正版 migration が反映されない）
  3. migration で既存制約を DROP する際は**命名規約適用後の実名**を確認 or 両名 `IF EXISTS`
     （0049 が `ck_sd_kind` 素の名前で DROP して失敗。実名は `ck_suggestion_dismissals_ck_sd_kind`。SQLiteテストでは再現しない）
  4. デプロイ前 pg_dump 必須・本番で pytest 禁止・`--no-verify` 禁止（従来通り）

## 3. このセッションの実装一覧（時系列）

| 塊 | コミット | 内容 |
|---|---|---|
| モニターUI統一 M1-M3 | `ada7dd9` `8f012a6` `e0b71f3` `f7661d7` `5a008f1` | 訪問モニターを Warm & Human トークンに統一（status token層/警報2段化/点滅廃止/lucide化/FilterChip共通化/タイポ5段/カード額縁）。**Tailwind は var() 色に /alpha 修飾子を生成しない**（--border-error等の実値トークンで解決） |
| 正規化調査 | `1cb6f5f` | 5自動算出ロジック（layer3/auto_allocator_v2×2/propose-slots×2）の横断調査 → `scheduling-logic-normalization.md`（ルールN-1〜N-8・不整合I-01〜I-20） |
| アドバイザー Phase 0 | `f8e60c4` `731884d` `1bdc862` `a5996f3` | P0-1: propose-slots にスタッフ実態警告（staff_unassigned/absent/sex_mismatch、除外せず降格）。P0-2: pfv_validator カーネル（V2 pinned同一性/V3衝突/V4昼休み/V5容量）＋PUT fixed-visits エンベロープ化＋apply の force_lunch 統一＋FE警告表示。**PUT INSERT の is_pinned 欠落（保護自壊）をレビューで検出・修正** |
| Phase 1 診る | `7dada87` `e4fb9e6` | GET /v2/schedule-health（週次コース別 移動/距離/バッファ/隙間。提案と同一物差し）＋健康診断ダイアログ（前週比・1.5倍警告バー）。lib/format/isoWeek.ts 新設 |
| Phase 2 囁く | `5e3e140` `04dd150` `bb14bad` `87ec252` | migration 0047（movability 4値＋suggestion_dismissals[指紋=patient×kind×weekday, uq]）。改善提案API=**限界コスト方式**（delta=現在枠の限界コスト−候補挿入コスト、閾値10分/週）＋filtered_summary（N-6）＋dismiss/promote。患者詳細に「改善提案」セクション・可動域セレクタ・見送り→昇格フロー |
| Phase 3 全7テーマ | `93f0d51` `ad042cf` `0b79e00` `eb6391a` `c558999` `31a9042` `d73aca3` `61accaa` `0df8971` | ⑤シミュレーション再定義 / ①欠勤代替（migration 0048・_find_conflict再利用・manual_staff_override保護・欠勤対応ダイアログ）/ ②スワップ（migration 0049・双方向feasibility・apply-swap 1TX・FE寛容パース）/ ⑥週次ガイド＋マニュアル / ⑦badge透明バグ修正＋isoWeek統一 / ③見直し通知（trend API・+20%バナー）/ ④効率優先の代替枠（既定False完全不変） |
| P4 希望＝余白＋ピン統一 | `effd012` `6d6f397` `acf1206` | 権威3層化（§1参照）。「鍵」→「ピン留め」全UI統一＋赤丸頭ピン（ui/push-pin.tsx カスタムSVG）。**P4-C: ピン解除時に movability=locked を解放**（migration 0050 で残骸一括クリーンアップ。解除しても提案が出ないユーザー報告の修正） |
| P5 欠勤コース単位 | `7e83d3f` `63d6d61` | 引き継ぎプラン4層（§1参照）。plans[]はレスポンス拡張で後方互換・POST apply 不変（FEが substitutions[] に展開） |

## 4. 概念モデル・用語辞書（現場と合意済み）

| 用語 | 実体 | 意味 |
|---|---|---|
| 固定訪問スケジュール | `PatientFixedVisit`(PFV) | 患者との確定した約束。週次visit生成の源 |
| 希望訪問スケジュール | `Patient.weekly_pattern` | 患者から聞き取った受け入れ可能範囲＝提案の「余白」の権威 |
| ピン留め（旧: 鍵） | `PFV.is_pinned` | 自動化から完全保護。apply系は422。UI=赤丸頭ピン |
| 可動域 | `PFV.movability` (unknown/time_flexible/day_flexible/locked) | 希望で表現できない例外の上書き。UIは詳細設定内。**ピンON⇒locked自動/ピンOFF⇒locked解放** |
| コース | `Course` (A-E, M=overflow) | スタッフ配置の単位（1コース1スタッフ/日）。欠勤対応はコース単位 |
| 却下記憶 | `suggestion_dismissals` | 指紋=patient×kind(time_change/day_change/swap)×weekday。同一提案を蒸し返さない。理由→可動域昇格は人間確認後のみ |
| 限界コスト | improvement_engine | 「患者を抜くと前後が直結して浮く分」−「候補位置への挿入コスト」 |

## 5. コード地図（今回追加・改修の主要ファイル）

**BE（backend/app/）**
- `services/scheduling/pfv_validator.py` — 再検証カーネル（V2-V6）。apply系とPUTの安全網
- `services/scheduling/improvement_engine.py` — 改善提案＋スワップ（限界コスト・movability/希望3層・却下）
- `services/scheduling/schedule_health.py` — 健康診断＋trend（travel物差しは auto_allocator_v2 から import）
- `services/scheduling/staff_substitute.py` — 欠勤代替（訪問単位カーネル＋P5プランエンジン4層）
- `api/v1/schedule_v2.py` — health/trend/improvement/apply-swap/propose-slots(効率代替) 等
- `api/v1/staff_substitute.py` / `api/v1/patient_fixed_visits.py`（PUTエンベロープ・pin PATCH＝解除時locked解放）
- 定数の単一ソース: `constants.py`＋`auto_allocator_v2.py`（速度20/バッファ8/90分占有/営業枠/昼窓）— propose-slots系は import
- **不変の正典**: 時間実行可能性=proposal_solver（前方/後方・90分占有）。全新機能はこれを import 再利用（コピー禁止）

**FE（frontend/）**
- `components/ui/push-pin.tsx`（赤丸頭ピン）/ `filter-chip.tsx` / `badge.tsx`（透明バグ修正済）
- `components/schedule/v2/`: ImprovementSuggestions{Section,Card} / DismissReasonDialog / ScheduleHealthDialog /
  ScheduleReviewBanner / StaffSubstituteDialog（プランUI）/ WeeklyRitualGuideDialog / FullOptimizeDialog（シミュレーション）
- `lib/format/isoWeek.ts`（ISO週の単一ソース・UTC純粋）/ `lib/schemas/v2/*`（zodはBE契約と1:1・警告系は寛容）
- **FE規約**: 未知enum値でセクション全滅しない寛容パース（improvementSuggestion の kind 方式を踏襲）/
  fixed-visits 採用は `_proposeSlotUtils.mergeAdoptedIntoNormalFixedVisits`（is_pinned/movability を必ず運搬）

## 6. 設計文書インデックス（docs/plans/）
- `scheduling-logic-normalization.md` — ルールN-1〜N-8・不整合I-01〜I-20（レビューの判定基準）
- `schedule-advisor-design.md` — アドバイザー構想（原則P1-P6・Phase 0-3）
- `p0-2-apply-safety-net-design.md` / `p2-improvement-mvp-design.md` / `p3-1-staff-substitute-design.md` /
  `p5-course-substitute-design.md`
- 運用マニュアル: `docs/manual/週次スケジュール準備ガイド.html`（P3-⑥）

## 7. 既知の課題・backlog
- 改善提案の閾値（10分/週）・欠勤プランの例外閾値（2件）・プラン数上限（層2/計5）は現場フィードバックで調整前提
- 層3(Mgr)は層1-2ゼロ時のみ生成 — 「層1が例外付きのみでもMgr案が見えない」不満が出たら再検討（レビュー指摘済）
- apply-individual の V5 統合テスト未追加 / 新規曜日の course None バケットは V5 偽陰性（warning-only）
- H5(受入カレンダー)×apply-week-only の仕様整理（I-07）/ FE/BE二重持ちの残り（freeGaps定数等）
- 既存fail（無関係）: BE `test_reset_to_fixed_*` 2件・auth tzフレーク / FE CourseDayTablePanel系（QueryClientProvider）と SessionProvider系
- クライアントデモ未実施（デモ台本と事前チェック手順はセッション履歴にあり。健康診断→改善提案→見送り学習の3幕構成）

## 8. 開発プロセス規約（このセッションで確立・厳守）
- 体制: **実装(executor, 複雑=opus)→独立レビュー(code-reviewer)→修正→ディレクターがコミット→Waveごとにデプロイ**。自己approve禁止
- BEテスト: `cd backend && python -m pytest <files> -q`（**uv run 不可**）。FE: `pnpm tsc --noEmit` / `pnpm vitest run <files>` / `pnpm lint`
- migration: 単一head維持・PG/SQLite両対応・既存制約DROPは実名確認
- **日本語ファイルの一括置換に PowerShell Get-Content/Set-Content 禁止**（二重エンコードで文字化け。必ずEditツール。2回事故）
- コミットメッセージは日本語 conventional。レビュー判定と反映済み指摘を明記

## 9. 次の候補
1. クライアントデモ（§7）→ 閾値・文言の現場調整
2. 当日欠勤プランの実運用フィードバック（例外閾値・Mgr表示条件）
3. Phase 3 後続: 見直し通知の月次指標拡張 / office横断の改善提案フィード
4. 保守: §7 の残債
