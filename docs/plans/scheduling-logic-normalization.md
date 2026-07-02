# スケジュール自動算出ロジック 正規化ルール（v1・調査版）

作成 2026-07-02 / 対象 HEAD `5a008f1` / 調査: architect×3（layer3 / auto_allocator_v2 / propose-slots）＋ UI配線調査

目的: 5つの自動算出ロジックのルールを1枚に正規化し、以後の個別レビュー・修正が
「統一ルールとの差分」として judged されるようにする。file:line は調査時点のもの。

---

## 1. 全体マップ（5ロジック → 3エンジン → 適用経路）

| # | UIロジック | 起動UI | 提案エンジン | 提案API | 適用API |
|---|---|---|---|---|---|
| 1 | 自動スタッフ割付 | CourseDayTablePanel「自動スタッフ割付」 | **layer3_assignment**（ハンガリアン法） | POST /schedule/assign-staff-only（**即書込＋review_items返却**） | POST /schedule/apply-staff-review（レビュー承認分） |
| 2 | 全面最適化 | 「全面最適化」→ FullOptimizeDialog | **auto_allocator_v2** run_v2_pipeline(mode=full_optimize)（read-only） | POST /v2/full-optimize | /v2/apply-individual（PFV全置換）or /v2/apply-week-only（visitsのみ） |
| 3 | プール投入（一括） | 「プール投入」→ DiffAddDialog | **auto_allocator_v2** run_v2_pipeline(mode=diff_add)（read-only） | POST /v2/diff-add | /v2/apply-individual |
| 4 | 新規提案 | 「新規提案」→ ProposeNewModal | **proposal_solver + propose_slots_service**（read-only） | POST /v2/propose-slots | PUT /patients/{id}/fixed-visits（FE側マージ） |
| 5 | プール個人の空き枠候補 | プールカードクリック → PoolCandidateList(primary) | 同上（G-113で一本化） | POST /v2/propose-slots | 同上 |

- ①はコース→スタッフの割付（visit時刻は動かさない）。②③④⑤は患者visit の時刻・コース配置（スタッフ個人への割付は範囲外）。
- ③の一括ダイアログには④⑤と同じ PoolCandidateList（on-demand）が併設され、**1画面に2エンジンの提案が並ぶ**。

---

## 2. 現状ルールカタログ（検証済みの事実）

### 2.1 時間・地理モデル

| ルール | layer3 (①) | auto_allocator_v2 (②③) | propose-slots (④⑤) |
|---|---|---|---|
| 距離計算 | Haversine（レポート用のみ。コストからはG-90で撤去） | Haversine 直線 | 同左（v2から import） |
| 移動速度 | —（未使用） | TRAVEL_SPEED_KMH=20（config可） | 同一値 import |
| 移動バッファ | —（visit間モデルなし） | VISIT_BUFFER_MINUTES=8（同住所=0、config可） | 同一値 import |
| イベント保護バッファ | **BUFFER_MINUTES=15**（StaffEvent×visit重複判定） | — | — |
| 前方制約 | — | forward主体（prev占有終端+移動+バッファ→earliest） | あり（H10） |
| **後方制約** | — | **G-98の限定blame のみ**（cur=固定/pinned × prev=movable pool の組合せに限定） | **完全実装**（候補end+移動+バッファ ≤ next.start, :622-628） |
| 5分刻み切上げ | — | 非固定visitのactual_startに適用 | 同（非固定のみ） |
| shortage許容 | — | <5分=警告で固定配置 / ≥5分=未割当 | <5分=warning付き候補 / ≥5分=除外 |
| 同住所判定 | なし | _address_bucket（tolerance 0.001≒100m） | 同一 |
| 同住所90分占有 | なし | SAME_ADDRESS_PAIR_MIN_OCCUPANCY=90（Stage6 earliest＋G-94段b） | **あり**（_existing_occupancy_end :502-526 — v2と整合） |
| 営業枠 | — | AM 09:30–12:00 / PM 13:00–18:00（start/end config可、正午境界固定） | 同一 import |
| 昼休み | — | compute_lunch_window 動的（60→45→30分fallback、CRITICAL#1再検証） | 同関数を import |
| 座標欠損 | レポート距離12km固定 | 患者skip（build_visits_for_pool前提ガード）※注入visitは0.0扱いの既知限定事項 | **候補0件で早期return** |

### 2.2 ハード制約マトリクス（enforce=違反不可 / soft=警告 / KPI=計測のみ / —=概念なし）

| 制約 | layer3 | v2 full_optimize | v2 diff_add | propose-slots |
|---|---|---|---|---|
| 勤務曜日（シフト+当日休） | **enforce**（INF） | 人数カウントに反映のみ（H6） | 同左 | **なし** |
| 性別制限 | **enforce**（INF、AND意味論） | 対象外（H7=呼出側責務と明記） | 同左 | **なし** |
| 拠点一致 | **enforce**（G-90、effective_office_for_weekday） | コースのoffice_id粒度 | 同左 | コースのoffice_idフィルタのみ |
| StaffEvent重複 | **enforce**（±15分） | — | — | — |
| 直近1週同コース | **enforce**（course_code粒度） | — | — | — |
| 患者間時間衝突 | —（コース割付のみ） | **なし**（G-94はdiff_add限定→pinned×非pinned衝突は未検出） | **enforce**（G-94/95/99、90分占有込み） | **enforce**（gap走査の構造上不可能） |
| 受入カレンダー× (H5) | — | **スキップ**（設計判断） | **enforce** | **なし** |
| 昼休み確保 (H10) | — | enforce | enforce | enforce（H5昼休み回避） |
| コース容量6名/480分 | — | enforce（H9）＋480分は警告 | 同左 | **enforce**（H1/H2: 除外） |
| ケアアラーム乖離 | — | 30-60分=警告 / 60分超=未割当 | 同左 | —（time_type適合で代替） |
| time_type適合 | — | actual_start決定規則（固定/時間帯/午前/午後/終日） | 同左 | slot_feasible（H6-H9）。**時間帯下限が v2=desired / solver=09:30 と意図的差** |
| 週次統一 (H1) | — | **KPIカウントのみ**（enforceなし） | 同左 | —（単一候補提示） |
| 翌日跨ぎ | — | （営業枠内で構造的） | 同左 | enforce（H4） |

### 2.3 スタッフ適格性の3段階非対称（最重要の構造差）

| レベル | 内容 | ①layer3 | ②③v2 | ④⑤propose-slots |
|---|---|---|---|---|
| L2: 個人割付 | 個別スタッフに割付け、個人単位の適格性を保証 | ✅（本業） | ❌（apply側責務と明記） | ❌ |
| L1: 人数実在 | 曜日粒度の出勤者数（shift＋当日休 off 反映、trainee除外、応援転入G-45） | ✅ | ✅（コース数上限にのみ使用） | ❌ |
| L0: 表示のみ | 割付済みstaff_nameを表示するだけ | — | — | ✅（**シフト・当日休・性別・未割当コースを一切見ない**。2名体制も warning のみ） |

帰結: ④⑤の候補は「当日休みのスタッフのコース」「スタッフ未割当のコース」「性別不適合」でも提示され得る。
③は「当日 StaffShift ゼロの曜日→全コースM」「M未生成コースの提案」（幸P090系）が構造的に残る。

### 2.4 同住所・2名体制

| ルール | v2 | propose-slots | layer3 |
|---|---|---|---|
| ペア成立 | 同住所+同時刻 or 連続、90分占有 | 同（_same_address_pair_slots、既ペア飽和はskip） | — |
| pair_mode (blocked/preferred/required) | enforce（Stage3） | **参照しない** | — |
| ペア候補の time_type | actual_start規則に従う | **固定時刻チェックをスキップ**（営業枠のみ検査） | — |
| ペアのスコア | —（構造で優遇） | _PAIR_BONUS=1000で最優先 | — |
| 2名体制 | 人数≥2の粗チェック（警告のみ）。visit_group_id は apply側 | two_staff_not_guaranteed 警告のみ。**採用時に相方枠を自動作成しない（MVP制約toast）** | persist時に partner course から secondary 解決（2行INSERT） |

### 2.5 pinned/確定の保護

| 経路 | 保護規則 |
|---|---|
| v2 提案段階 | pinned は制約計算に含めるが補正後に snapshot 復元（監視のみ）。Stage5 で code 不動。G-98 は pinned を守り pool 側を落とす |
| apply-individual | pinned PFV があれば **422 全拒否** |
| apply-week-only | pinned の4属性（weekday/start/duration/office）変更で 422。保護visit衝突は skip+警告 |
| layer3 | staff_assigned 済コースを固定集合に保護（W25）。admin手動割付を巻き戻さない |
| propose-slots | read-only。既存visitは壁としてのみ扱う（pinned概念なし） |

### 2.6 スコアリング比較

| | layer3（コスト最小化・ハンガリアン） | v2（greedyヒューリスティック、明示目的関数なし） | propose-slots（加点スコア） |
|---|---|---|---|
| 主要項 | 患者直近担当 1e6/5e5/2e5 ≫ 前日同コース100 ≫ コースローテ5×加重 ≫ 決定的乱数0-10 | 距離greedyクラスタ→AM/PM重心マッチ→同住所集約→固定優先/希望フォールバック | pair 1000 ≫ 近接50 ≫ 希望適合30 ≫ 残容量20 − 警告1 |
| タイブレーク | 行列index順（＋manager fallbackはUUID昇順） | 早い時刻優先/先頭退避/alphabetical/（start,patient_id）ソート | (start,office,weekday,code)昇順 |
| 距離の扱い | **不使用**（G-90撤去） | クラスタリングの根幹 | 近接スコア（5km飽和） |

### 2.7 定数カタログ

**単一ソース（constants.py / auto_allocator_v2 定義 → propose-slots が import。二重定義なし＝良好）**:
TRAVEL_SPEED_KMH=20・VISIT_BUFFER_MINUTES=8・SHORTAGE_THRESHOLD_MIN=5・SAME_ADDRESS_PAIR_MIN_OCCUPANCY=90・
MAX_PATIENTS_PER_COURSE=6・COURSE_MAX_MINUTES=480・SAME_ADDRESS_TOLERANCE=0.001・営業枠/昼窓・
_COURSE_CODES(A–E)/_M_OVERFLOW_CODES(M–M9)・デフォルトservice 35分。
config注入（SchedulingSettings）: buffer/speed/business_start/end/max_patients/lunch 3種。

**エンジン独自**: layer3（HUNGARIAN_INFINITY=1e12、BUFFER_MINUTES=15、ローテ各種、**COST_GAMMA/DELTA=math.inf は宣言のみで未使用**）、
propose-slots（_W_PROXIMITY=50/_W_PREFERENCE=30/_W_BALANCE=20/_PAIR_BONUS=1000/_PROXIMITY_SAT_KM=5/_NEAR_LUNCH_MARGIN_MIN=5）。

**FE複製（UI配線調査で14件特定）** — 主要なもの:
| FE | BE対応 | リスク |
|---|---|---|
| freeGaps.ts BUSINESS_BLOCKS/90分占有/MIN_FREE_GAP=60/同住所キーtoFixed(3) | constants複製と明記 | config変更（business_start等）に**FEが追随しない** |
| DiffAddProposalTimeline.insertionGap（バッファ8分表示計算） | BE Stage6 とは別実装 | 判定差→表示と実際の乖離 |
| _proposeSlotUtils.mergeAdoptedIntoNormalFixedVisits | **PUT fixed-visits は全削除→INSERT**。他曜日保持はFEマージだけが担保 | FE以外の呼出しで他曜日消失し得る（サーバ不変条件でない） |
| course_template解決/ISO週変換/slot0規則/警告訳語辞書ほか | 各所 | drift |

### 2.8 提案粒度・surface規則

- ③diff_add: **1患者1提案**（pool_visitsが真のソース）。proposal_source=fixed/fixed_fallback_preferred/preferred。
  未割当でも surface する設計（orphan救済）＋ G-100 で g94 衝突分のみ抑制。fixed_unavailable_reasons は3コード。
- ②full_optimize: 曜日×コース Before/After ＋ 患者別 PFV 差分。unassigned reason は _classify_warning_reason の写像。
- ④⑤: 上位 limit 件（既定10/最大50）＋カバレッジ。**0件時に個別の除外理由は返さない**。
- ①: 即書込＋review_items（連続=黄/性別=赤）だけ保留。警告4系統。

### 2.9 適用(apply)経路の検証規則

| 経路 | 書込対象 | 再検証 | 拒否条件 |
|---|---|---|---|
| apply-individual | PFV **全置換**（visit_plans に無い曜日は DELETE） | endpoint H10ゲートのみ。**週全体の患者間衝突は再検証なし**（提案時結果を信頼＝TOCTOU窓） | pinned存在=422 / H10=422 |
| apply-week-only | visits のみ | H10 は **warning継続**（#113 hotfix） | pinned 4属性変更=422 |
| apply-staff-review | Course/VSA | _persist 同一経路 | — |
| PUT fixed-visits（④⑤採用） | PFV 全削除→INSERT | **なし**（時間衝突・容量の検査なし） | — |

---

## 3. 不整合・欠陥一覧（統一ルール策定の根拠）

重大度: ◎=実害が本番で観測済み/確実に起こる、○=条件が揃えば起こる、△=品質・保守性。

| ID | 重大度 | 内容 | 根拠 |
|---|---|---|---|
| I-01 | ◎ | **④⑤がスタッフ実態を見ない**: 当日休スタッフのコース・スタッフ未割当コース・性別不適合でも候補提示（幸P090の「川名休み」系は propose-slots 側でも未解決） | proposal_solver に staff 参照ゼロ |
| I-02 | ◎ | **③のスタッフ数はコース付番上限にしか使われない**: StaffShift ゼロ曜日→全部M、当週Course実体非参照→未生成Mを提案（ファントムM） | :8038-8060, feasibility-gaps③ |
| I-03 | ◎ | **PUT fixed-visits（④⑤の採用経路）に検証ゼロ**: 全削除→INSERT・他曜日保持はFEマージ頼み。時間衝突・容量・90分占有の検査なしで確定できる | 2.9表 / FE複製#8 |
| I-04 | ○ | **apply-individual の TOCTOU**: 提案と適用の間に他ユーザー変更が入っても週全体衝突を再検証しない | v2調査 M3 |
| I-05 | ○ | **H10 の適用非対称**: individual=422 / week-only=warning。同一提案が経路で受理/拒否が割れる | schedule_v2:766/1018 |
| I-06 | ○ | **full_optimize に G-94 がない**: pinned×非pinned の同時刻衝突が検出されない可能性 | v2調査 M8 |
| I-07 | ○ | **H5（受入カレンダー）**: full_optimize はスキップ（設計判断）だが apply-week-only はそのまま書込む→受入×時間帯に visit が入り得る。④⑤も H5 を見ない | v2調査 M1 / D表 |
| I-08 | ○ | **v2 の後方制約が不完全**: G-98 は「固定/pinned の前の movable pool」に限定。propose-slots は完全両方向。同じ配置が④⑤では除外・③では通る非対称 | F表 |
| I-09 | ○ | **used_minutes の二重定義**（propose-slots）: スコア用=単純合計 / 制約用=移動・占有込み → スコアが甘く出る | propose-slots M4 |
| I-10 | ○ | **同住所ペア候補が time_type（固定時刻）チェックをスキップ**（④⑤） | proposal_solver:652-712 |
| I-11 | ○ | **pair_mode(blocked/required) を④⑤が参照しない**: blocked ペアに同時刻候補を出し得る | 2.4表 |
| I-12 | ○ | **2名体制の非貫通**: ④⑤採用時に相方枠を作らない（toastのみ）→片肺確定が正規動線に存在 | G-102 MVP制約 |
| I-13 | ○ | **FE/BE二重持ち14件**: 特に営業枠・90分・60分gap の定数複製は config 変更でFEが古い値のまま | UI調査表 |
| I-14 | △ | **H1（週次統一）が KPI のみ**: enforce も警告もなし | v2調査 D表 |
| I-15 | △ | **「バッファ」の二義性**: 移動バッファ8分 vs イベント保護15分。命名・文書で未区別 | layer3 M2 |
| I-16 | △ | 座標欠損の扱い不一致: ②③=患者skip（注入visitは0.0の既知事項）/ ④⑤=0件return / ①=12km固定 | 2.1表 |
| I-17 | △ | layer3 の宣言乖離（COST_GAMMA/DELTA=math.inf 未使用）・「都賀」office名ハードコード・manager fallback がローテ完全無視 | layer3 M1/M4/M5 |
| I-18 | △ | ④⑤の0件時に除外理由を返さない（「なぜ不可」が仕上げ課題③-4のまま） | propose-slots L |
| I-19 | △ | 時間帯 time_type の下限差（v2=desired_start / solver=09:30）は意図的だが文書化されていない | propose-slots M3 |
| I-20 | △ | diff_add が pending_edits 非対応（full_optimize のみ）→ 今週限定変更を踏まえたプール投入プレビュー不可 | v2調査 A表 |

---

## 4. 正規化ルール宣言（提案 — 以後のレビューの判定基準）

**N-1（時間実行可能性の正典）** 「患者visitの時間配置が実行可能」の定義は
*前方: prev占有終端(90分占有込み)+移動+バッファ ≤ start* **かつ** *後方: end+移動+バッファ ≤ next.start*、
同住所は移動0/バッファ0、非固定は5分刻み、固定のみ shortage<5分を警告付き許容、営業枠・昼窓内 — とする。
実装の正典は proposal_solver（両方向）。**v2 Stage6 も長期的に同判定へ収斂**させ、当面の差異（G-98限定blame）は既知の縮退として明記する。

**N-2（用語と定数）** 「移動バッファ」（8分・config）と「イベント保護バッファ」（15分・layer3）を別名として扱い、
定数は constants.py 系の単一ソースを維持。**FEに複製する場合は (a)出典コメント必須 (b)可能なものは API（scheduling-settings 拡張）配信へ移行**。

**N-3（スタッフ適格性の段階定義）** L0=表示のみ / L1=曜日人数 / L2=個人割付 と定義し、各ロジックの**目標レベル**を:
①=L2（現状達成）、②③=L1+「当週Course実在」参照、④⑤=**L1.5**（候補コースの割付スタッフについて 当日休・性別不適合・未割付 を検出し、除外でなく理由付き警告/降格）とする。

**N-4（提案→適用の再検証原則）** 適用APIは提案結果を信頼せず、最小セット
（患者間時間衝突[90分占有込み]・H10・容量480分）を適用直前に再検証する。
受理基準は経路間で統一（violation は原則422。#113系の業務詰まりは「警告で強行」フラグを明示的に持たせる）。
**PUT fixed-visits にも同じ最小セット＋「他曜日保持」のサーバ側保証を導入**する。

**N-5（保護順位の統一）** pinned ＞ 確定visit（placed）＞ 固定PFV ＞ 希望パターン。
どのエンジンも「動かせない側を優先し、movable 側を落とす/ずらす」（G-98 の一般化）。

**N-6（surface規則）** 「提案不可・未割当」は黙って消さず、必ず理由コード付きで surface する
（diff_add の fixed_unavailable_reasons 3コードと unassigned reason 写像を共通語彙に昇格。④⑤の0件時にも除外理由サマリを返す）。

**N-7（同住所・2名体制）** 90分占有・ペア成立判定・pair_mode は全エンジン共通に適用する
（④⑤に pair_mode 参照と time_type 検査を追加）。2名体制の採用は「相方枠の同時作成 or 明示ブロック」のどちらかとし、片肺確定を正規動線から排除する。

**N-8（決定性）** 全エンジンでタイブレークは決定的であること（現状達成。乱数は決定的シードのみ許可）。

---

## 5. レビュー・修正ロードマップ（案）

| 波 | 対象 | 主な適用ルール | 候補チケット |
|---|---|---|---|
| Wave A（安全網・小粒） | 適用経路 | N-4 | I-03（PUT fixed-visits 検証＋他曜日保持のサーバ化）→ I-04 → I-05 |
| Wave B（④⑤仕上げ） | propose-slots | N-3/N-6/N-7 | I-01（staff実態の警告/降格）→ I-10/I-11 → I-09 → I-18 |
| Wave C（③本丸） | diff_add | N-3/N-1 | I-02（週Course実在＋staff稼働の反映=③-4残課題）→ I-08 |
| Wave D（②整合） | full_optimize | N-1/N-5 | I-06 → I-07（H5とweek-onlyの整合を仕様決め） |
| Wave E（保守性） | 横断 | N-2 | I-13（FE複製の削減/契約テスト）→ I-14/I-15/I-16/I-17/I-19/I-20 |

各 Wave は「実装→独立レビュー→本番デプロイ」を1単位とし、本文書を判定基準に使う。
