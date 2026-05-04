# INV-3 自動算出ロジック 監査 クロスレビュー B（受入観点）

## Verdict
**Status**: INCOMPLETE
**Confidence**: high
**Blockers**: 3
**Recommendation**: REQUEST_CHANGES

## Evidence

| Check | Result |
|---|---|
| Tests | **FAIL** — `pytest python_engine/` で 0件収集・0件実行（テストファイル自体存在せず） |
| Build / Import | pass — `python -c "import allocation_engine"` OK |
| Scale Runtime | pass — 規模テスト（300×50）elapsed=1.292s, assigned=300/300, unassigned=0 |
| 2名体制ID | pass — V001-1/V001-2、is_coupled=True、同時刻確認 |
| **areasバグ** | **FAIL（バグ現存）** — areas=['B'] のスタッフが area='A' に割当 → ASSIGNED（本来 UNASSIGNED） |
| JSON スキーマ | pass — AssignmentResult 18フィールド + summary 直列化 OK |
| StaffChange 時間指定 | pass — `restriction_type in ("時間指定","時間指定制限")` 実装済（監査の「不完全」と不一致） |

## 受入基準評価

| # | 観点 | Status |
|---|---|---|
| 1 | 移植検証テストケース必要数（300×50規模） | PARTIAL — エンジン自体は1.3秒で完走、ただし**テストコード皆無**（pytest 0件） |
| 2 | 推奨案B（部分移植）の受入基準 | MISSING — 「Week 1-4」あるが各週 exit criteria/品質ゲート/統合境界未定義 |
| 3 | JSON スキーマ統一の検証手段 | PARTIAL — 18フィールド整合だが**スキーマファイル（.json/TypedDict）が存在しない**、CareLink との契約（version/validation）未整備 |
| 4 | 既知バグ修正の優先順位とテスト | FAIL — areas バグ実コードで再現確認、`_build_candidate_list`/`_is_staff_available` どちらにも `staff.areas` フィルタなし。**修正未着手かつテストなし** |

## Gaps

**Gap 1: テストスイートの完全欠落（HIGH）**
- 2521行のエンジンに pytest 0件
- 回帰リスクが定量化不能
- 提案: 最低限、2名体制・areas・ローテ・性別・StaffChange 各タイプの単体テスト（pytest parametrize）を `python_engine/test_allocation_engine.py` 作成

**Gap 2: areasバグ未修正（HIGH）**
- 拠点制限が完全に無効化
- `_build_candidate_list` 内、性別フィルタ直下に `if staff.areas and req.area not in staff.areas: continue` 追加し、テスト確認まで移植着手不可

**Gap 3: 移植受入基準（案B）の定義不足（MEDIUM）**
- 「JSON スキーマで仕様明確化」と述べるがスキーマファイル不在
- 提案: OpenAPI/JSONSchema 定義 or Pydantic モデルで `VisitRequest` → `AssignmentResult` の契約を文書化、CareLink 側の受入テストで照合可能化

**Gap 4: 監査文書と実装の記述不一致（LOW）**
- StaffChange「時間指定制限」は監査で「不完全」だが、実装では `restriction_type in ("時間指定","時間指定制限")` 処理済 (line 774)
- §6.2 を実態に合わせて更新

## 受入チェックリスト

| # | 項目 | 必須度 |
|---|---|---|
| 1 | areas バグ修正 + テスト追加 | MUST |
| 2 | pytest スイート整備（コア制約 7種を parametrize） | MUST |
| 3 | JSON スキーマファイル作成（OpenAPI or Pydantic） | MUST |
| 4 | Codex 指摘の状態管理問題（_unregister_assignment が pid_date_staff を戻さない等）の修正 | MUST |
| 5 | 多試行 best 復元時の状態 map 完全復元 | MUST |
| 6 | 必須指定/同じ人希望経路の制約迂回修正（性別・soft_cap・同一患者同日チェック追加） | MUST |
| 7 | 2名体制 hard constraint 化（`_sync_coupled_times` 失敗時の hard fail） | SHOULD |
| 8 | mentor_pair の事前制約検証（勤務日・休み・partial block・max_per_day） | SHOULD |
| 9 | diff_engine の異体字正規化追加 + key index 利用 | SHOULD |
| 10 | dead code 削除（`_add_missing_coupled_entries` 等） | LOW |

## Recommendation

REQUEST_CHANGES — テストスイート皆無 + areas バグ現存 + Codex 指摘の状態管理問題が未解決の状態では、移植受入基準の根拠が証拠として成立しない。
