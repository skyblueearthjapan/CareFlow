# 引き継ぎ書：自動割当UX改革セッション（名称統一〜反映先統一U-0..U-3〜チェックインペア補正）

作成 2026-07-04 / **本番HEAD = `3de99a2`** / DB = migration **0052** / healthz 正常。
前セッションの引き継ぎ: `docs/plans/scope-optimization-HANDOFF.md`（範囲最適化〜処方箋）→
`docs/plans/schedule-advisor-HANDOFF.md`（アドバイザー全体）→ `docs/HANDOFF.md`（プロジェクト基本）。
**次のエージェントはまずこのファイルを読む。**

このセッション（2026-07-03〜04）は PO と対話しながら「自動割当まわりの UX 改革」を
一気通貫（実装→独立レビュー→コミット→デプロイ）で 10 回以上デプロイした大規模セッション。

---

## 1. TL;DR — いま何がどうなっているか

1. **変更反映先の統一が完成（U-0〜U-3）**: スケジュールを変更する全操作の出口が
   「A. 固定訪問週間に登録（今週にも即反映）/ B. この週だけ反映」の共通2択
   （`ChangeScopeChoice`）に統一された。「型に書いたのに表に出ない」「知らないうちに
   型が変わった」は構造的に解消。**ドラッグの既定は B（この週だけ）に変更済み**
   （現場周知は PO 了承済み・トーストの「毎週の型にも登録」で昇格・「今週のみ」チップ表示）。
2. **戻る/進む（↶↷・Ctrl+Z/Y）が稼働**: 操作ジャーナル（migration 0052 `schedule_op_log`）。
   v1 対象 = DnD 移動・担当変更・週だけ移動（グループ undo・自分の操作のみ・409 衝突検知）。
   PFV 系・一括系の undo は **v2 バックログ**。
3. **C案（プール個別化）3段階完了**: P-1 候補への厳密限界コスト表示＋除外理由 /
   P-2 俯瞰（「効果を表示」緑ボタン・自動効果順）/ P-3 一括プール投入の廃止
   （ボタン自体も PO 指示で削除済み。BE /v2/diff-add は残置）。
4. **定員超過の相談プロセス（方式b）**: 候補0件かつ定員起因のとき「+1名なら N 件」呼びかけ→
   超過候補表示→理由必須で採用。
5. **自動スタッフ割当の刷新**: 「自動スタッフ割当」に改称し「一斉スタッフ未割当」の左隣へ。
   レビューの一斉承認ボタン。**体制上不可避な連続は自動確定＋理由つきお知らせ**
   （G-91 オーナー決定Bを PO 指示で上書き）。「都賀」拠点名ハードコードは
   OfficeFeatureFlag `l3_fix_primary_staff` に置換（migration 0051 でシード済み）。
6. **訪問モニター大幅改善**: 未割当行のコース別分割・未割当行のマップ表示・
   一斉未割当の反映漏れ修正・同住所ペアのレーン分割（1レーン=66px 全高）＋
   合体ピルマーカー・**チェックイン警告のペア補正**（後攻の誤警告解消・「ペア待ち」表示）。

## 2. 本番状態とデプロイ

- VPS `root@72.60.211.213` / `/opt/carelink` / https://carelink.kaipoke-api.net / develop
- migrations: `0051`(l3_fix_primary_staff シード・都賀1拠点確認済み) → `0052`(schedule_op_log)
- 手順は従来どおり（pg_dump → pull → build → **migrate** → recreate → healthz・
  `set -eo pipefail`・フロント変更後は現場 Ctrl+Shift+R）
- **現場周知が必要な仕様変更**: ドラッグの既定が「この週だけ」（暗黙の型書換→B）に変更済み

## 3. 主要コミット（時系列・全て本番反映済み）

| 塊 | コミット | 内容 |
|---|---|---|
| 改称+一斉承認 | `818098d` `a3e0893` `9239722` | 「自動スタッフ割当」改称・γ移動・一斉承認・「一斉スタッフ未割当」 |
| 不可避連続+脱ハードコード | `7872d25` (mig 0051) | 代替候補0の連続を自動確定+notices・都賀→フラグ化・週次ガイド更新 |
| C案 P-1 | `d893dae` | propose-slots に marginal_cost_minutes（DELTA_EVAL_LIMIT=20・delta昇順）＋excluded_summary |
| C案 P-2 | `b0aadb0` | POST /v2/pool-overview（クエリ約7回一定・propose-slotsと厳密一致）＋PoolOverviewPane |
| C案 P-3+UI | `db64406` `71b657d` | 一括プール投入廃止→ボタン削除・「効果を表示」緑・効果順自動化・ヘッダ整理 |
| 方式b+モニター2件 | `994421d` `1ef6ded` `23760a3` | 定員超過相談+pending_applier config注入修正 / 未割当行分割 / 未割当マップ+一斉未割当反映(visits.primary/secondary/override解除) |
| U-1 | `dfa3d02` | PUT fixed-visits change_scope+week_sync / place-and-fix false=manual_week / scope-opt apply 3値 / ChangeScopeChoice+プール採用+範囲最適化 |
| U-2+ペア表示 | `354a155` `4c7d365` | 全操作展開（DnD既定B・cascade=false・今週のみチップ・改善提案/新規提案/シミュ/固定枠編集）/ visit-move-week-only 新設 / モニターレーン分割+ピルマーカー |
| U-3 | `b33198e` (mig 0052) | op_log+undo/redo+↶↷/Ctrl+Z/Y+M-2恒久対策(manual_week日の再生成スキップ)+update_course UUIDバグ+migrationヘッドテスト緩和 |
| レーン全高 | `d745b07` | 圧縮廃止・1レーン=66px（PO指摘） |
| ペア補正 | `3de99a2` | チェックイン警告のペア補正（起点置換・pair_waiting・notify同補正・(0,0)ガード） |

## 4. 概念モデル（このセッションで確立）

| 概念 | 実体 | 意味 |
|---|---|---|
| 反映先 A/B | `change_scope` (pattern_and_week / week_only) | A=型+今週即反映（PFV 書込後 reset_visits_to_fixed(patient_id=)）/ B=今週のみ・型不変 |
| この週だけの visit | `visits.source='manual_week'` | 週生成・固定枠戻の両方で保護。**再生成側も同(patient,date)の日をスキップ**（M-2恒久対策）。「今週のみ」チップ+昇格導線 |
| 昇格 | sync-week-visits-to-fixed（1患者） | 週→型。U-0 で pin/movability 保持修正済み |
| 操作ジャーナル | `schedule_op_log` (0052) | op_group_id=1操作1グループ・forward/inverse payload・undone。undo/redo は自分のみ・状態照合409 |
| 不可避連続 | `auto_committed_notices` | 代替候補0（single_staff/all_recent）→自動確定+理由。判定は _cost_single_cell 再利用 |
| 定員超過相談 | `include_overcapacity` / `capacity_override_reason` | +1列挙は config コピーで完全分離。採用は理由必須 |
| ペア待ち | `MonitorVisit.pair_waiting` | 判定起点=max(予定, 相方退出??到着+所要)。compute_pair_effective_starts を monitor/notify 共有 |

## 5. コード地図（今回の主要ファイル）

**BE**: `services/op_log_service.py`(undo/redo) / `api/v1/op_log.py` / `models/schedule_op_log.py` /
`services/checkin/monitor.py`(ペア補正+compute_pair_effective_starts) / `services/checkin/notify.py` /
`api/v1/schedule_v2.py`(sync-fixed-to-week・visit-move-week-only・pool-overview・scope apply 3値・
_apply_visit_move_week_only) / `api/v1/patient_fixed_visits.py`(change_scope+week_sync・
全件保存ピン修正) / `services/scheduling/propose_slots_service.py`(delta・excluded_summary・
overcapacity) / `layer3_assignment.py`(不可避判定・フラグ化) / `layer1_expander.py`+
`auto_allocator_v2.py`(manual_week スキップ・reset patient_id フィルタ・resolve_reset_office_ids)

**FE**: `ChangeScopeChoice.tsx`(共通2択・全操作で使い回す統一部品) / `opLog.ts`(schema+queries) /
`PoolOverviewPane.tsx` / `PoolCandidateList.tsx`(delta・除外理由・超過相談・A/B) /
`_proposeSlotUtils.ts`(buildWeekOnlyPlaceAndFixRequest 共通ヘルパ) / `visitMoveWeekOnly.ts` /
`CourseDayTablePanel.tsx`(↶↷・Ctrl+Z/Y・op_group_id・DnD B化) / `CourseDayTable.tsx`(今週のみチップ) /
monitor: `MonitorTimeline.tsx`(行キー選択 monitorRowKey・レーン assignVisitLanes・ペア待ち) /
`MonitorMapClient.tsx`(合体ピル groupStopsByCoord) / `constants.ts`

## 6. 設計文書（docs/plans/）

- `change-scope-unification-design.md` — **本セッションの中心**。反映先統一の全設計＋
  D-1〜D-4 PO決定＋§2.5/2.6/3.1 に既知制約と v2 方針を記録
- `pool-unification-design.md` — C案3段階（完了）
- `l3-assign-notice-redesign.md` — 不可避連続＋都賀フラグ化（完了）
- 調査レポート（会話内・要点はメモリに保存）: Layer3 徹底解析 / 配置5エンジン比較 /
  操作×書込先マトリックス / 定員制約調査

## 7. 残タスク・バックログ（優先度つき）

**現場フィードバック待ち（最優先で確認）**
1. ドラッグ既定B・2択・Ctrl+Z の現場での使用感（周知含む）
2. チェックインペア補正のタイミング感（grace 20分のままで良いか）
3. Layer 3 閾値の現場調整 — スケジュール診断/警告件数の実績値をもらってから

**実装バックログ（設計記録あり・着手可能）**
4. **undo v2**: PFV系・一括系のスナップショット undo / visit_ids ベース移動 / FOR UPDATE /
   復元時スロット衝突検知（設計書 §3.1）
5. 定員超過の**承認記憶**（診断/改善提案が承認済み7人目を蒸し返さない — suggestion_dismissals 類似）
6. I-12: 2名体制の相方枠自動作成（採用系）
7. L3 マネージャー救済(2nd pass)のローテ考慮（解析レポート案F・低コスト）
8. ペアの明示リンク列 or ペア補正 ON/OFF 設定（現状は座標推定・(0,0)ガードあり）
9. arrival_delay_min の表示が補正前基準（判定は正しい・ラベルのみ紛らわしい）
10. ProposeNewModal B経路の非アトミック（バッチ week-only API）/ 週次ガイドへの2択・Ctrl+Z 説明追記

**懸案（コード外）**
- `docs/HANDOFF.md` が**依然 untracked**（2026-06-14 作成・内容は一部陳腐化。コミットするなら
  内容更新とセットで。PO 判断待ちのまま）
- 既存 fail（無関係・従来から）: BE `test_reset_to_fixed_auto_shifts_*` `test_reset_to_fixed_same_address_*`
  `test_apply_week_only_soft_deletes_*` / FE CourseDayTablePanel 系 24件（useSchedulingSettings 未モック）・
  SessionProvider 系

## 8. 開発プロセス規約（従来＋今回の教訓）

- 体制: **executor(複雑=opus) 実装 → code-reviewer 独立レビュー → 指摘反映 → 再判定 →
  ディレクターがコミット → デプロイ**。自己approve禁止。今セッションのレビューは 10 回
  （APPROVE 6 / 条件付き 4 → 全て対応後確認）
- **日本語ファイルに PowerShell Get-Content/Set-Content 絶対禁止**（今セッションで 3 回目の
  文字化け事故を実際に起こし git checkout で復旧した。ASCII のみの置換でも使わない。Edit ツール一択）
- エージェント並行時は**ファイル所有権を明示**（触ってよい/禁止リストをプロンプトに書く）。
  同一ファイルに2Wave の変更が混ざったら commit は明示パス指定の git add で分離
- migration テストは**ヘッド名を固定しない**（単一 head 検証のみ・0043 規約。0049/0050 が
  固定していて 0051 追加時から静かに壊れていた）
- API エラー中断したエージェントは SendMessage で「git status で現物確認してから再開」指示
- BE テスト `python -m pytest -q -p no:warnings`（uv run 不可）/ ruff format は変更ファイルのみ /
  デプロイは build→migrate→recreate 順・pg_dump 必須

## 9. 次の候補

1. 現場フィードバック収集（§7 の 1-3）→ 閾値・文言調整
2. undo v2 か 承認記憶 — 現場の困りごとが出た方から
3. 週次ガイド・運用マニュアルの改訂（2択・Ctrl+Z・ペア待ちの説明）
