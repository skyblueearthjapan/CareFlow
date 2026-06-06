# Phase G-88: 自動最適化ルールの事業所別設定化（合意済み設計）

Step1(調査)=`optimization-config-catalog.md`。本書は Step2/3(協議→合意)の確定設計。

## スコープ確定：管理UIで設定可能にする項目（すべて事業所共通・1セット）
| # | 項目 | 既定 | 範囲 | 対応する内部定数 |
|---|---|---|---|---|
| ① | 訪問間バッファー(分) | 8 | 0〜30 | `VISIT_BUFFER_MINUTES` |
| ② | 移動速度(km/h) | 20 | 10〜40 | `TRAVEL_SPEED_KMH` (UIは「1kmあたり◯分」併記) |
| ③a | 昼休み・標準の長さ(分) | 60 | 30〜90(5分刻) | `LUNCH_DURATION_PREFERRED` |
| ③b | 昼休み・取得時間帯 | 11:30〜13:30 | 開始10:30-12:00/終了12:30-14:00 | `LUNCH_EARLIEST_START`/`LUNCH_LATEST_END` |
| ④ | 営業時間 | 09:30〜18:00 | 開始07:00-10:00/終了16:00-20:00 | `AM_BLOCK_START`/`PM_BLOCK_END` (AM/PM固定休憩帯は廃止) |
| ⑤ | 1コース最大人数 | 6 | 1〜10 | `MAX_PATIENTS_PER_COURSE` (コース×曜日の個別定員はこの上限内で従来どおり) |

## 内部固定（UIに出さない・据え置き）
- 警告の閾値: 移動不足5分・希望時刻ズレ30/60分・長距離30分・同住所100m/最低占有90分。
- スコアの重み: propose(近接50/希望30/平準20/ペア+1000)・Layer3コスト係数。
- 昼休み最低30分(労基/人道的に固定)・5分刻み・同住所0バッファ・正午12:00境界(午前/午後判定)。
- 昼休み短縮fallback(標準→…→30分)は内部自動処理(③aの標準値を起点に短縮)。

## 営業時間の整理(④)
- 「AM 09:30-12:00 / PM 13:00-18:00 + 12:00-13:00固定非営業」→「**09:30〜18:00 連続営業 + ③の動的昼休み**」に整理。
- 正午12:00は患者の午前/午後希望の判定用に内部維持(営業枠とは別概念)。

## データ設計
- **事業所(=DB/テナント)単位の単一設定**。拠点別ではない。現状1DB=1事業所→**シングルトン1行**。将来サブスクで他事業所はDB分離→「DBごとに1行」で自然に拡張。
- 置き場所: 専用テーブル(例 `scheduling_settings`、1行、各列 nullable + コード既定値fallback)。
  - 既存の拠点別設定(`Office.operating_weekdays` / `CourseTemplate.capacity`)はそのまま(責務分離: 曜日=office, 個別定員=course_template, 時間/移動/昼休み/上限=新設定)。
- **伝播方式**: グローバル定数のDB上書きはしない(propose と full-optimize の一貫性・並行最適化の安全のため)。`SchedulingConfig` dataclass を作り、ローダー `load_scheduling_settings()` (既存 `_load_office_operating_weekdays` と同型) で読み、最適化パイプラインへ**引数注入**。コード既定値は単一ソース(下記リファクタ)を fallback に。

## UI設計(合意済み)
- **入口**: 親機ヘッダー右上に ⚙ 設定アイコン(admin/manager のみ)。
- **形式**: **専用ページ**へ全画面遷移(例 `/settings` or `/admin/scheduling-settings`)。
- **操作部品**: 範囲もの=**スライダー＋数値**、時刻=時刻ピッカー、定員=−/＋ステッパー。
- **構成**: グループ分け「⏱ 時間のルール(バッファ/昼休み) / 🚗 移動の見積もり / 🏢 営業・コース」。各項目に平易な一言説明＋ライブ表示(例 20km/h=1kmあたり約3分)。
- **安心感**: 「変更は次回の最適化から反映」明示・「既定値に戻す」・保存。
- 設計思想: シンプル・直感的・迷わない・誤操作してもすぐ戻せる。

## 実装ロードマップ
1. **重複定数の単一ソース化リファクタ(挙動不変・先行)** ✅完了: `MAX_PATIENTS_PER_COURSE`(4箇所)・`COURSE_MAX_MINUTES`(2)・`SAME_ADDRESS_TOLERANCE`(2)・稼働曜日default(2)を leaf モジュール `scheduling/constants.py` へ集約しimport。**A-E/A-D は調査の結果“別機能の上限”と判明したため統一しない**(`_COURSE_CODES`=A-E は v2 full-optimize の5コース上限／`COURSE_CODES`=A-D は旧 `/courses/generate` の Layer2 案生成で API も staff_count≤4。誤解防止コメントを両所に追記)。テスト厳格・pg_dump。
2. **設定テーブル + マイグレーション** + `SchedulingConfig` + ローダー。
3. **最適化パイプラインへ引数注入**(buffer/速度/昼休み/営業時間/定員)。propose・full-optimize 双方に反映。④の営業枠連続化(AM/PM固定休憩帯廃止)も含む。フロント `freeGaps.ts` の営業枠/MIN_FREE_GAP は API 配信で同期。
4. **管理UI(専用ページ + ⚙入口)**。
5. 各段階 executor + code-reviewer、pg_dump の上デプロイ。

## 実装契約(全エージェント共有)

### SchedulingConfig (dataclass, frozen)
`backend/app/services/scheduling/config.py`:
```
@dataclass(frozen=True)
class SchedulingConfig:
    visit_buffer_min: int          # ① 既定8 / 0..30
    travel_speed_kmh: float        # ② 既定20 / 10..40
    lunch_duration_min: int        # ③a 既定60 / 30..90
    lunch_window_start: time       # ③b 既定11:30
    lunch_window_end: time         # ③b 既定13:30
    business_start: time           # ④ 既定09:30
    business_end: time             # ④ 既定18:00
    max_patients_per_course: int   # ⑤ 既定6 / 1..10
```
既定値は `scheduling/constants.py`(土台で集約済) を出所にする(新規 default も constants へ追加)。

### 設定テーブル `scheduling_settings` (事業所=DB 単位の単一行)
- シングルトン: `id` PK + `is_singleton bool` に部分 UNIQUE 制約 or 固定行。各設定列は **nullable**
  (NULL=既定値 fallback)。+ created_at/updated_at。Alembic マイグレーション(head 単一を確認)。
- 列: visit_buffer_min(int) / travel_speed_kmh(numeric) / lunch_duration_min(int) /
  lunch_window_start(time) / lunch_window_end(time) / business_start(time) / business_end(time) /
  max_patients_per_course(int)。CHECK 制約で範囲(buffer 0-30, speed 10-40, lunch 30-90,
  capacity 1-10, lunch_start<lunch_end, business_start<business_end)。

### ローダー
`async def load_scheduling_config(db) -> SchedulingConfig`: 単一行を読み、各フィールド
row 値 or constants 既定で SchedulingConfig を組む(行が無ければ全既定)。

### API (`/api/v1/scheduling-settings`)
- `GET`: 現在の有効値(SchedulingConfig)を返す(各値 + 既定かどうか)。require_role admin/manager。
- `PUT`: 更新(部分可)。範囲バリデーション(422)。admin/manager。更新後の有効値を返す。
- フロント freeGaps 同期用に営業枠/定員も GET に含める(またはこの GET をフロントが読む)。

### Step3 注入方針
- `run_v2_pipeline` / propose-slots が `SchedulingConfig` を受け取り、module 定数の代わりに使用:
  buffer(`VISIT_BUFFER_MINUTES`)・速度(`haversine_minutes` の TRAVEL_SPEED_KMH)・昼休み
  (`compute_lunch_window` の duration/window)・営業枠(AM/PM_BLOCK→連続 [business_start,end])・
  定員(`MAX_PATIENTS_PER_COURSE`)。**グローバル定数の書換はしない**(引数注入)。
- 営業枠連続化: 12:00-13:00 固定非営業を廃し、[business_start,business_end] 連続 + ③動的昼休み。
  正午12:00(午前/午後判定)は内部維持。フロント `freeGaps.ts` は GET API から営業枠を取得。
- propose と full-optimize で同一 config を使い一貫性維持。

### UI (Step4)
- 親機ヘッダ右上 ⚙(admin/manager) → 専用ページ `/settings`(or `/admin/scheduling-settings`)。
- 3グループ(時間のルール/移動の見積もり/営業・コース)、スライダー+数値・時刻ピッカー・±定員。
- 各項目に平易説明＋ライブ表示(速度→1kmあたり分)。既定に戻す・保存・「次回の最適化から反映」。

## 未決(実装時に確認)
- ~~A-E/A-D 不整合の正しい値~~ → ✅調査完了: 別機能の上限のため統一しない(v2=5 / `/courses/generate`=4)。
- 別系統 `/allocate`(`allocation/engine.py`)が運用中か(運用中なら設定整合の要否)。
- 設定ページのURL/ナビ配置の細部。
