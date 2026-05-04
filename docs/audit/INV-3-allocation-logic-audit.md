# INV-3: 自動算出ロジック深層監査レポート

## 1. 割当エンジン全体像

### 1.1 AllocationEngine クラス
ファイル: `lib/allocation_engine.py` (2521行)

**責務**:
- 10入力モデル → AssignmentResult 生成
- 制約条件下で訪問をスタッフ配置
- 多試行戦略（5パターン）で未割当最小化

**内部状態**:
- `self.results`: 割当 + 未割当 結果リスト
- `self.staff_day_visits`: "staffId|dateStr" → [result_idx]
- `self.assign_count`: "staffId|dateStr" → count
- `self.pid_date_staff`: "pid|dateStr" → {staffId} 重複防止
- `self._request_constraints`: result_idx → 制約復元用

### 1.2 8段階パイプライン

`_run_pipeline()` (line 231-350) のフロー:

| 段階 | メソッド | 機能 |
|---|---|---|
| Step 1 | `_insert_events()` (414-450) | スタッフイベントを固定アンカー挿入 |
| Step 2 | `_sort_requests()` (454-461) / `_sort_requests_smart()` (463-479) | 5パターン順序生成 |
| Step 3.0 | `_level0_allocate()` (484-567) | 初期貪欲割当（最良スコアスタッフ配置） |
| Step 3.1 | `_enforce_coupled_atomicity()` | 2名体制片方未割当なら両方未割当 |
| Step 3.2 | `_sync_coupled_times()` (878-953) | 2名体制時刻同期（時間窓交集合） |
| Step 4 | `_gap_pack()` (957-1073) | 固定アンカー間の隙間に柔軟訪問詰め直し |
| Step 5 | `_level1_reinsertion()` (1077-1148) | 未割当を全スタッフ候補の距離順再試行 |
| Step 6 | `_ejection_chain()` | 入替え挿入: 既割当を一時解除して未割当挿入 |
| Step 7 | `_relaxed_reinsertion()` | 段階的制約緩和（性別/NG職員等） |
| Step 7.5 | `_day_shift_strategy()` | 希望日以外への振替 |
| Step 8 | `_level3_route_optimize()` | nearest-neighbor + 2-opt（最大50反復） |
| Step 9 | `_fix_cross_patient_overlaps()` | 同一スタッフ患者別時刻重複解消 |
| Step 10 | `_apply_mentor_pairs()` | 同行訪問ペア展開（post-processing） |
| Step 11 | `_final_overlap_sweep()` | 最終重複チェック・解除 |
| Step 11.5 | Post-Sweep Rescue (303-330) | Sweep後の救済（Level1+EjectionChain+緩和） |
| Step 12 | `_day_shift_strategy()` 再 (334-342) | 最終曜日シフト |
| Step 12.5 | `_rescue_partial_coupled()` (345-347) | 2名体制レスキュー（1名のみ→2人目補充） |

**計算量ボトルネック**:
- `_level0_allocate()`: O(R × S)
- `_can_insert()`: O(D × V) × 重複チェック
- `_sync_coupled_times()`: O(V log V)
- `_level3_route_optimize()`: O(V² × 50)

### 1.3 多試行エンジン（5パターン）
`allocation_engine.py:149-180`:
```python
orderings = self._generate_orderings(active_requests)  # 5パターン
for trial_idx, ordering in enumerate(orderings):
    self._reset_state()
    self._run_pipeline(ordering, active_requests)
    if unassigned_count < best_unassigned_count:
        best_results = list(self.results)
        if unassigned_count == 0: break
```

5パターン推定: 日付昇順+need_staff降順 / smart_sort（候補少ない先） / need_staff優先（2名先確保） / ローテーション優先 / 距離順

---

## 2. 入力モデル（10種類）

ファイル: `allocation_models.py`

| モデル | 主要属性 | 業務的役割 |
|---|---|---|
| Patient (10-30) | pid, name, area, lat/lng, service_minutes, weekly_count, need_staff(2名体制), sex_limit, cont_pref(同じ人/ローテ), fixed_staff_ids, time_type(固定/午前/午後/終日), start/end_pref_min | 利用者マスタ |
| Staff (33-54) | sid, name, gender, lat/lng, shift_start/end_min, work_days, areas, max_per_day, alloc_pref(均等/多め/少なめ) | スタッフマスタ |
| VisitRequest (57-81) | request_id, date_str, weekday, pid, area, start/end_min, service_min, need_staff, specified/ng_staff_ids, sex_limit, cont_pref, time_type, earliest/latest_min, prev_staff_id, change_type | 単一訪問 |
| Event (84-95) | event_id, staff_id, date, type, title, start/end_min, fixed_slot | 会議/研修（固定アンカー） |
| StaffChange (98-105) | staff_id, date, restriction_type(休み/午前休/午後休/遅刻/早退/時間指定), start/end_min | 特定日制限 |
| WeeklyPattern (108-118) | pid, day_code, start/end_min, service_min, need_staff | 定期訪問パターン |
| ConfirmedHistory (121-128) | week_start, date_str, pid, staff_id, name | 過去実績（ローテーション用） |
| SpecialWeek H+D (180-205) | mode(ADD/REPLACE), date_str, time_type, start/end_min | 特別週上書き |
| MentorPair (208-219) | trainee_id, mentor_id, start/end_date, band(午前/午後/終日) | 同行訪問 |

CareLink 実装注意:
- time_type 4値（固定/午前/午後/終日）統一
- cont_pref 正規化マップ (`allocation_utils.py:212-223`) 移植必須
- need_staff=2 = 「2名同行必須」（同時刻同一訪問）

---

## 3. 出力モデル

`allocation_models.py:131-151`:
```python
@dataclass
class AssignmentResult:
    visit_id: str           # "V001", "V001-1", "V001-2"（2名体制）
    date_str: str
    weekday: str
    staff_id: str           # 未割当時 ""
    staff_name: str
    pid: str
    pname: str
    area: str
    start_min: Optional[int]
    end_min: Optional[int]
    service_min: int
    time_type: str
    earliest_min: Optional[int]
    latest_min: Optional[int]
    note: str               # "[未割当: 条件を満たすスタッフなし]" 等
    is_event: bool
    is_coupled: bool
    movement_km: Optional[float]
```

summary 出力 (`allocation_engine.py:211-229`):
```python
{
    "total": 150, "assigned": 135, "unassigned": 15,
    "unassigned_detail": {"患者A(山田)|2026/04/15": 2},
    "day_shifts": [...], "day_shift_failures": [...],
    "trials": 5,
    "message": "..."
}
```

---

## 4. 制約処理の詳細

### 4.1 ローテーション (`allocation_engine.py:604-752`)
```python
history_records = self.rotation_history.get(req.pid, [])
sorted_hist = sorted(history_records, key=lambda h: h.date_str, reverse=True)
max_exclude = min(len(unique_recent_sids), len(candidates) - 1)
for n_exclude in range(max_exclude, 0, -1):
    exclude_sids = set(unique_recent_sids[:n_exclude])
    remaining = [c for c in candidates if c["staff"].sid not in exclude_sids]
    if remaining:
        best_candidates = remaining
        break
```
直近N人を除外しつつ「少なくとも1人候補は残す」を保証。

### 4.2 性別制限 (655-658, 1162-1165)
```python
if req.sex_limit == "女性のみ" and staff.gender != "女性": continue
if req.sex_limit == "男性のみ" and staff.gender != "男性": continue
```

### 4.3 必要スタッフ数（2名体制）(488-567)
```python
for slot in range(1, req.need_staff + 1):
    visit_id = f"V{visit_counter:03d}"
    if req.need_staff > 1: visit_id += f"-{slot}"
    chosen = self._find_best_staff(req, used_staff_ids)
```

2人目補充: `_rescue_partial_coupled()` (Step 12.5)

### 4.4 同行訪問（mentor_pair）
`_apply_mentor_pairs()` (Step 10):
- trainee_id と mentor_id を同時刻配置
- start_date~end_date 範囲対象
- band(午前/午後/終日) で時間帯フィルタ

### 4.5 拠点制限 ⚠️ 既知バグ
```python
for staff in self.staff_list:
    if staff.areas and req.area not in staff.areas:
        continue  # 実装されていない
```
`areas` 定義はあるが**実装で未使用**。CareLink で必ず実装すること。

### 4.6 時間タイプ (`allocation_utils.py:19-24`)
```python
TIME_TYPE_DEFAULTS = {
    "午前": (540, 720),     # 09:00-12:00
    "午後": (780, 1020),    # 13:00-17:00
    "終日": (540, 1080),    # 09:00-18:00
}
```

固定時刻処理 (789-826):
```python
fixed_start = req.start_min
if fixed_start is None and req.time_type == "固定" and req.earliest_min is not None:
    fixed_start = req.earliest_min
fixed_end = fixed_start + (req.service_min or 30)
```

### 4.7 スタッフ稼働状況・シフト (`allocation_engine.py:827-854`)
```python
def _get_blocked_intervals(self, staff_id, date_str):
    for sc in changes:
        if sc.restriction_type == "休み":      intervals.append(Interval(0, 1440))
        elif sc.restriction_type == "午前休":  intervals.append(Interval(shift_s, 720))
        elif sc.restriction_type == "午後休":  intervals.append(Interval(720, shift_e))
        elif sc.restriction_type == "遅刻":    intervals.append(Interval(shift_s, sc.start_min))
```

シフト時間チェック (800-801):
```python
if fixed_start < staff.shift_start_min or fixed_end > staff.shift_end_min: return False
```

---

## 5. 差分検出エンジン（diff_engine.py 944行）

### 5.1 3パスマッチング

**Pass 1 完全一致** (432-472):
```python
if (cur.service_type == opt.service_type and cur.start_time == opt.start_time):
    if has_diff: corrections.append(Correction(action="edit", ...))
```

**Pass 2 サービス内容一致** (473-522):
```python
svc_match = (cur.service_type == opt.service_type or
    cur.service_type in opt.service_type or
    opt.service_type in cur.service_type)
if svc_match:
    corrections.append(Correction(action="edit", ...))
```

**Pass 3 日付変更** (523-566):
```python
for cur_idx, cur in unmatched_current:
    for opt_idx, opt in unmatched_optimized:
        if cur.service_type == opt.service_type:
            if cur.date != opt.date:
                corrections.append(Correction(action="date_change", ...))
```

### 5.2 異体字正規化マップ (`commands/auto_apply.py:45-66`)
```python
{"栁":"柳","﨑":"崎","髙":"高","濵":"浜","邊":"辺",
 "廣":"広","齋":"斎","齊":"斎","澤":"沢","櫻":"桜",
 "渡邉":"渡辺","渡邊":"渡辺"}
```

### 5.3 同行訪問の比較 (88-89)
```python
def has_staff_change(self) -> bool:
    return (self.staff1_from != self.staff1_to or self.staff2_from != self.staff2_to)
```

### 5.4 修正シート JSON 出力 (625-699)
```json
{
    "total_corrections": 42,
    "summary": {"time_changes":5,"staff_changes":8,"date_changes":3,"additions":15,"deletions":11},
    "corrections": [
        {"user_name","date_from/to","start_time_from/to","end_time_from/to",
         "staff1_from/to","staff2_from/to","service_type","action","business_type","remarks"}
    ]
}
```

---

## 6. 既知のバグ・課題

### 6.1 修正履歴（git log）

| コミット | 説明 |
|---|---|
| 5e5feac | 2名体制レスキュー: `_P2`→`V###-N` ハイフン形式 |
| 102a762 | 2名体制レスキューバグ修正: date_str=None 防止、重複追加防止 |
| c0968ce | 2名体制: is_coupled フラグから検出に変更 |
| aa99037 | 多試行エンジン: 5順序試行で最良採用 → 未割当 -80% |
| 69cc2f6 | FinalSweep後救済フェーズ追加 (Step 11.5) |
| 622cfba | 2名体制レスキュー追加 (Step 12.5) |
| 8f4b394 | ローテ除外を動的拡張（候補0防止） |

### 6.2 既知の不完全実装

| 機能 | 状態 | 修復 |
|---|---|---|
| 地域制限 (areas) | 定義のみ未使用 | `if staff.areas and req.area not in staff.areas: continue` 追加 |
| 同行訪問 (mentor_pair) | post-processing のみ | Level 0 で優先割当する専用ロジック検討 |
| StaffChange 時間指定 | 不完全 | restriction_type 「時間指定制限」処理確認 |

### 6.3 パフォーマンス予測

| 処理 | 計算量 | 最悪 | 改善案 |
|---|---|---|---|
| _level0_allocate | O(R×S×C) | 300×50×20 = 300K回 | 候補早期フィルタ |
| _can_insert | O(V²×Interval) | 二重ループ | rtree インデックス |
| _sync_coupled_times | O(V log V) | coupled多数時 | 不要同期スキップ |
| _level3_route_optimize | O(V²×50) | V=200 = 2M回 | 遺伝的アルゴリズム |

---

## 7. CareLink への移植戦略

### 案A: HTTP API 流用（変更ゼロ）
- プロス: 検証済、Python知識で保守容易
- コンス: cold start遅延、ネットワークI/O、シリアライズ負荷
- 工数: 0.5週、リスク: 低

### 案B: 部分移植（推奨）
- Python保持: allocation_engine, models, utils
- CareLink再実装: diff_engine, auto_apply
- プロス: 複雑な割当はそのまま、UI操作はCareLink内完結
- コンス: スキーマ統一必須、テストコスト
- 工数: 2週、リスク: 中

### 案C: 完全書き直し
- 対象: Node.js/TS/C# 等
- プロス: 言語統一、デプロイ簡潔
- コンス: 8段階+多試行+制約を再実装、検証3ヶ月以上
- 工数: 4-6週、リスク: 高

### 推奨: **案B 部分移植**

理由:
1. 割当ロジック（最複雑）は Python で検証済 → 言語変更リスク排除
2. 修正シート適用（Playwright）は CareLink 実装パターン確立
3. JSON スキーマで仕様明確化 → テスト容易
4. 工数とリスクのバランス最適

実装工程:
- Week 1: データスキーマ統一 + Python API 仕様書
- Week 2: CareLink Backend で修正シート生成実装
- Week 3: Playwright で修正シート適用実装
- Week 4: E2E テスト + バグ修正

---

## 8. 実装詳細引用

### coupled visit ID パース (`allocation_engine.py:38-39`)
```python
_COUPLED_RE = re.compile(r'^(V\d+)-(\d+)$')
```
CareLink 実装時: 言語別正規表現エンジンの差異注意。

### 距離スコア計算 (`allocation_utils.py:59-66`)
```python
DIST_SCORE_THRESHOLDS = [(2.0, 0), (5.0, 1), (10.0, 2)]
DIST_SCORE_FAR = 5
DIST_SCORE_UNKNOWN = 99
```

Haversine (39-48):
```python
def haversine_km(lat1, lng1, lat2, lng2):
    to_rad = math.pi / 180.0
    d_lat = (lat2 - lat1) * to_rad
    d_lng = (lng2 - lng1) * to_rad
    a = (math.sin(d_lat/2)**2 +
         math.cos(lat1*to_rad)*math.cos(lat2*to_rad) * math.sin(d_lng/2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return EARTH_RADIUS_KM * c
```

### Level 1 再挿入制約 (`allocation_engine.py:1149-1179`)
```python
def _is_staff_available_for_reinsertion(self, staff, r, ng_staff_ids, sex_limit):
    if ng_staff_ids and staff.sid in ng_staff_ids: return False
    if sex_limit == "女性のみ" and staff.gender != "女性": return False
    if staff.work_days and r.weekday not in staff.work_days: return False
    key = f"{staff.sid}|{r.date_str}"
    if self.assign_count.get(key, 0) >= staff.soft_cap(): return False
    blocked = self._get_blocked_intervals(staff.sid, r.date_str)
    for iv in blocked:
        if iv.start <= 0 and iv.end >= 1440: return False
    return True
```

---

## まとめ

| 項目 | 仕様 | CareLink 影響 |
|---|---|---|
| 入出力 | 10モデル → 1結果 | JSON 統一必須 |
| 制約評価 | 指定→継続→スコア順 | 同順序遵守（バグ回避） |
| パイプライン | 8〜12.5段階 | Level 0-3 を優先実装 |
| 多試行 | 5パターン並列 | 計算量5倍（キャッシュ緩和） |
| 修正シート | 3パス + 異体字 | マップ更新必要 |
| 性能 | 300訪問×50スタッフ = 数秒 | 実規模プロファイリング必須 |
| バグ | areas未使用、mentor_pair事前制約なし | 早期修正推奨 |
