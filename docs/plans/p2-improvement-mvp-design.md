# P2 詳細設計: 改善提案MVP（可動域フラグ＋単発改善提案＋却下記憶）

作成 2026-07-03（architect 設計・ディレクター補正済み）/ 親: schedule-advisor-design.md §3 Phase 2 / 前提: Phase 0-1 本番稼働（e4fb9e6）

## 0. コミット分割
P2-A データ基盤（migration 0047＋モデル＋スキーマ運搬）→ P2-B 改善提案API → P2-C FE。各コミット独立デプロイ可・既定挙動不変。

## 1. P2-A: データ基盤

### 1.1 可動域フラグ = **PFV 単位** `movability`（String(16), NOT NULL, server_default='unknown'）
値: `unknown`(既定・保守的) / `time_flexible`(同曜日内の時刻変更可) / `day_flexible`(曜日も変更可) / `locked`(完全固定)。
DB CHECK: `ck_pfv_movability IN (4値)`。index 不要。

**意味論整合（3軸は別物）**: `is_pinned`=自動化からの保護(apply 422) / `time_type`=希望時刻の性質(weekly_pattern) / `movability`=提案の可否。
- **含意: is_pinned=True ⇒ movability='locked'**。矛盾は DB CHECK でなく **pfv_validator に V6**（proposed_items で is_pinned=True かつ movability≠'locked' → 'locked' に自動矯正＋warning。V2同一性比較の**前**に矯正）。既存 PATCH pin 経路を壊さないための判断。
- migration で既存行 backfill: `UPDATE ... SET movability='locked' WHERE is_pinned=true`。
- time_type='固定' と movability は制約なし（「固定希望だが管理者判断で動かせる」は正当）。

### 1.2 suggestion_dismissals テーブル
`id / patient_id(FK CASCADE) / kind('time_change'|'day_change') / target_weekday(0-6) / reason('day_immovable'|'time_immovable'|'staff_relation'|'other') / reason_note / dismissed_by(FK users SET NULL) / dismissed_at / expires_at(NULL=無期限)`。
CHECK 3本（kind/reason/weekday）＋ index(patient_id, kind, target_weekday)。
**同一提案の指紋 = (patient_id, kind, target_weekday)**（時刻まで含めると僅差の蒸し返しを防げず、kindだけでは粗すぎる）。同一指紋の再dismissは既存行のupsert（reason更新）。
**可動域昇格は自動でしない**: dismiss POST の `promote_movability: bool=false` で人間確認後にのみ更新（day_immovable→time_flexible / time_immovable→locked）。

### 1.3 運搬の必須事項（ディレクター補正・P0-2のis_pinned BLOCKERと同型の罠）
- **PUT fixed-visits の INSERT ループに `movability=item.movability` を必ず追加**（漏れると保存のたび unknown に戻る）。ラウンドトリップテスト必須。
- BE `PatientFixedVisitV2Base/Read` に movability 追加（Read への追加は extra="forbid" のシリアライズ都合で P2-A 必須）。
- **FE `_proposeSlotUtils.ts` の existingFixedVisitToItem が movability を運搬**するよう P2-A で同時修正（zod は optional+default('unknown') で BE 先行デプロイ互換）。proposedSlotToFixedVisitItem（新規採用枠）は movability 未指定=unknown で正。
- pinned 同一性タプル（pfv_validator V2）には movability を**含めない**（V6矯正との干渉回避。pinned行は backfill+V6 で常に locked のため実害なし）。

## 2. P2-B: 改善提案API

### 2.1 効果差分 = 限界コスト方式
`delta = marginal_cost(現在枠) − marginal_cost(候補枠)`（分/週・km/週）。
- marginal_cost = travel(prev→X)+travel(X→next) − travel(prev→next)（先頭/末尾は該当辺のみ。同住所は0。`_travel_buffer_between`/`haversine_km`/config を再利用 = Phase 1 健康診断と同一物差し）。
- 候補列挙は `find_available_slots_for_candidate` に**自分を除いた** existing を渡す（配置済み患者を候補として評価）。90分占有・前方/後方制約・営業枠・昼窓は正典のまま。
- 計算ベースは **PFV（恒久パターン）**。当週Visitベースは Phase 3。
- 閾値 `IMPROVEMENT_THRESHOLD_MIN = 10`（分/週・命名定数。config化は Phase 3）。

### 2.2 可動域の尊重規則
| movability | 時刻変更提案 | 曜日変更提案 |
|---|---|---|
| locked / is_pinned | 出さない | 出さない |
| unknown | **出す（requires_patient_confirmation=true の要確認ラベル付き）** | 出さない |
| time_flexible | 出す | 出さない |
| day_flexible | 出す | 出す |
（unknown に時刻提案を出すのは P4「却下がデータを育てる」との均衡。曜日はP3/P5優先で保守的に）

### 2.3 API 契約
- `GET /v2/improvement-suggestions?patient_id&iso_year&iso_week`（admin/manager・read-only・単一患者MVP。office横断フィードは Phase 3）
  レスポンス: `suggestions[]`（kind/現在枠/candidate{weekday,start,end,course,staff,office}/delta{travel_minutes_saved,travel_km_saved}/changes{changes[],unchanged[]}/staff_warnings[]（P0-1の3コード再利用）/feasibility_basis/requires_patient_confirmation）＋ **`filtered_summary`（pinned/locked/dismissed/below_threshold/day_restricted の件数 = N-6「黙って消さない」）**。0件でも200＋内訳。
- `POST /v2/improvement-suggestions/dismiss`: {patient_id, kind, target_weekday, reason, reason_note?, promote_movability=false} → {dismissal_id, movability_updated, new_movability}。同一指紋は upsert。
- movability の編集経路は既存 PUT fixed-visits に載せる（専用PATCH なし）。

## 3. P2-C: FE
- `PatientScheduleDetailDialog` の「固定枠vs今週比較」と「プール投入提案」の間に `ImprovementSuggestionsSection`（`!enablePoolProposal` 時のみ = 配置済み患者向け）。
- 提案カード: 効果（-18分/週・-2.1km/週）/ 変わるもの・変わらないもの / 要確認ラベル / スタッフ警告 / [採用][見送り]。
- 採用 = `useConfirmFixedVisits`＋`mergeAdoptedIntoNormalFixedVisits` 流用（P0-2の警告トースト連動）。**マージ時に movability/is_pinned を運搬**（1.3）。
- 見送り → 理由選択ダイアログ（4択＋自由記述）→ dismiss POST → day/time_immovable の場合のみ昇格確認ダイアログ →「はい」で promote_movability=true。
- 可動域編集UI = `PatientFixedVisitsPanel` の各曜日行にセレクタ（未設定/時刻変更可/曜日変更可/完全固定）。is_pinned 行は locked 固定表示。
- 0件時は filtered_summary から「ピン留めN件/閾値未満N件/却下済みN件で非表示」を表示。

## 4. リスク・展開・テスト
- R1 旧FE互換: server_default で挙動不変。R2 負荷: 単一患者のみ。R3 精度: PFVベースと明記。R5 unknown誤採用: 要確認ラベル。
- 展開順: A→B→C（各々独立デプロイ可。C は API 未deploy時にセクションを静かに非表示）。
- BEテスト: improvement_engine（限界コスト基本/同住所/先頭末尾/閾値上下/locked除外/unknown曜日除外+要確認/却下除外/filtered_summary）、dismissals（作成/promote/同一指紋upsert）、PUT movability ラウンドトリップ＋pinned→locked矯正。
- FEテスト: 提案カード表示/採用配線/見送り→昇格分岐、Panel の movability セレクタ＋pinned固定。
