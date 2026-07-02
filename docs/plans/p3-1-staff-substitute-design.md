# P3-① 詳細設計: 当日欠勤の代替スタッフ提案

作成 2026-07-03（architect 設計・ディレクター承認済み）/ 親: schedule-advisor-design.md §3 Phase 3

## 0. 概要
「今朝◯◯さんが欠勤 → 今日の担当visit、誰が代われる？」に答える。Layer3 の適格性資産を日粒度で再利用し、
新規ロジックは「候補スタッフの当日既存visitとの時間衝突判定」のみ（pfv_validator._find_conflict を再利用）。
提案(GET, read-only)→人間が選ぶ→適用(POST, all-or-nothing) の3段。恒久（Course.assigned_staff_id / PFV）は触らない。

## 1. API 契約
### GET /api/v1/schedule/staff-substitute/candidates?absent_staff_id&target_date
- 欠勤スタッフの当日 planned visit 一覧（VSA 由来。completed/in_progress/cancelled 除外）
- visit ごとに候補ランキング `candidates[]{staff_id, staff_name, staff_sex, score, score_breakdown{continuity_bonus, travel_penalty, load_penalty}}`
- 2名体制: `visit_group_id / required_staff_count / is_secondary` を明示
- 候補ゼロ visit は `no_candidate_reasons[]{code, count}`（N-6）:
  no_work_day / sex_mismatch / office_mismatch / event_overlap / time_conflict / trainee_solo

### POST /api/v1/schedule/staff-substitute/apply
- body: `{substitutions:[{visit_id, substitute_staff_id}], register_absence: bool=false}`
- **N-4 再検証**: GET と同一ハード制約を適用直前に再実行。violation は 422、status 遷移済は 409。**all-or-nothing**
- 書込: 対象 visit の VSA を absent→substitute に差替え＋Visit.primary/secondary_staff_id 同期（_persist 規約 :2988-2990 踏襲）
  ＋ visit_group partner の同期＋ `Visit.manual_staff_override=True` 設定
- register_absence=true で StaffWeeklyOverride(off) を同時登録（既存あれば skip）
- レスポンス: applied[]{visit_id, original_staff_id, substitute_staff_id, visit_group_partner_visit_id} + warnings

## 2. ハード制約（GET/POST 共通・全て既存資産の再利用）
| 制約 | 再利用元 |
|---|---|
| 当日出勤（shift+当週off） | load_active_staff の work_days（layer3:2247-2384） |
| 性別 | sex_satisfies_restrictions（公開エイリアス :3212） |
| 拠点 | StaffInfo.effective_office_for_weekday（:228-262） |
| StaffEvent±15分 | _has_event_overlap_with_buffer（:526-566）。**visit 単位**で判定（コース単位だと過剰除外） |
| trainee 単独禁止 | Staff.is_trainee ∧ required_staff_count==1 |
| 時間衝突（新規データソース） | pfv_validator._find_conflict（:173-208。前方/後方・移動+バッファ8分・90分占有・同住所=N-1正典） |

## 3. ソフトスコア（命名定数）
WEIGHT_CONTINUITY_1/2/3 = 100/50/20（患者直近担当）/ WEIGHT_TRAVEL = -1.0（追加移動1分あたり）/
WEIGHT_LOAD = -5.0（当日visit 1件あたり）。降順ソート。

## 4. assign-staff-only からの保護（重要）
`Visit.manual_staff_override: bool = False` カラムを追加（**migration 0048**）。
layer3 `_persist`（:2919-2992）の VSA DELETE/INSERT 対象から manual_staff_override=True の visit を除外。
（代替案の VSA フラグ / 警告のみ は却下: 複雑化・事故リスク）

## 5. その他の決定
- completed/in_progress/cancelled visit は対象外（planned のみ）
- モバイル self-scope は VSA ベースのため差替えで自動追随（追加対応不要）
- 2名体制: primary欠勤=対象visitのprimary＋partnerのsecondaryを同期 / secondary欠勤=逆。ケースC（両方欠勤）は各visit独立に差替え

## 6. コミット分割
- **Commit 1 (BE-core)**: migration 0048（Visit.manual_staff_override）＋モデル＋ staff_substitute.py（エンジン＋適用）＋ API 2本＋ルーター登録＋テスト（各ハード制約/衝突境界/スコア/2名体制/再検証409-422/override登録）
- **Commit 2 (BE-protect)**: layer3 _persist の override 除外＋回帰テスト（既存 assign-staff-only 全pass）
- **Commit 3 (FE)**: ツールバーボタン＋ StaffSubstituteDialog（スタッフ+日付選択→visit一覧+候補→適用確認+欠勤登録チェックボックス）＋hooks/zod＋テスト
