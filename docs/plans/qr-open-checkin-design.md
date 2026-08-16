# QR打刻の開放（担当外・予定外訪問の記録）+ モニター表示 — 設計書

作成: 2026-08-16（ドラフト・実装前）
ステータス: **設計確定（ユーザー/PO 2026-08-16 論点全決着）・未実装**

前提となる既存設計: `qr-checkin-implementation-plan.md` / `qr-checkin-backend-design.md`（QR打刻の基盤）、
`patient-ng-staff-design.md`（NG スタッフ・通知パターン）。
先行タスク: `/q/{token}` ディープリンク（汎用カメラ404根治・別タスクで実装中）。本設計は
その `/q` ページと resolve API を**入口として拡張**する第2弾。

---

## 0. 背景（要求の原文旨）

- イレギュラー対応（急な代行等）で**担当外のスタッフが訪問する**ことが現実にある。
- 訪問した記録は**患者側の履歴として絶対にシステムに残す**必要がある（訪問記録の確実な取得）。
- → **モバイル「楽スケ」にログインしているスタッフなら誰でも**、患者宅の QR 読取で
  訪問の開始・完了を記録できるようにする。**その患者に予定(visit)が無くても**記録できること。

## 1. 確定事項（2026-08-16）

| # | 論点 | 決定 |
|---|---|---|
| 1 | 認可モデル | **QR トークン所持＝現地証明を認可の鍵**とする。ログイン済みスタッフ + 現地 QR + GPS 判定の3点で正当な記録とみなす。一覧/詳細 API の担当外秘匿（404）は**現状維持**し、QR 経由の打刻だけを開放する |
| 2 | 予定がある場合 | 読取後に当日予定（時間帯・予定スタッフ名）を候補表示し、**スタッフ本人が「この予定の代行」か「予定外訪問として記録」かを選ぶ**（自動マッチはしない） |
| 3 | 予定がない場合 | **`is_unplanned` フラグ付き visit をその場で自動生成**（開始打刻=開始時刻、退出打刻で完了）。visit 無し打刻（visit_id NULL 化）は下流全部に波及するため採らない |
| 4 | 予定の担当者 | **書き換えない**。「予定した人」= visit 側、「実際に行った人」= `visit_checkins.staff_id`。乖離はスキーマ変更なしで表現される |
| 5 | NG/性別制限との交差 | **記録は止めない**（打刻=事実記録）。mismatch 通知と同じパターンで**管理者ベルへ冪等通知** |
| 6 | QR 必須化 | **担当外の打刻は QR 読取必須**（手動フォールバック無し）。担当スタッフは現行どおり手動可。予定外訪問は患者特定に QR が構造上必須でもある |
| 7 | モニター: 代行 | **バー単位**に「代行」バッジ + 実績スタッフ名。詳細パネルに「予定: ○○ / 実績: △△」並記。既存の行レベル⚠（スケジュール担当≠visit予定担当）とは別物として視覚区別 |
| 8 | モニター: 予定外 | **「📌予定外訪問」専用行を1本**追加（発生日のみ表示・独自配色）。バーに患者名+実績スタッフ名。通常コース行への推定混載はしない |
| 9 | 要対応アラート | 代行・予定外とも**トレイに載せ、ベル通知も出す**（mismatch と同格・冪等）。既存の「確認済み」(visit_reviews) で消す運用に乗せる |

## 2. 概念モデル

- 打刻の3形態:
  1. **担当打刻**（現行）: 自分の visit に checkin/checkout/no-show。手動フォールバック可。
  2. **代行打刻**（新設）: 他スタッフ担当の当日 visit に、QR を鍵として checkin/checkout。
     visit の担当は不変。実績は checkin 行の staff_id。
  3. **予定外打刻**（新設）: 当日予定が無い（or 本人が予定外を選択）→ `is_unplanned` visit を
     生成して checkin/checkout。
- no-show（未訪問記録）は**担当スタッフ専用のまま**（担当外が「行かなかった」を記録する
  ユースケースは無い）。

## 3. データモデル（migration 0071 想定）

- `visits.is_unplanned` BOOLEAN NOT NULL DEFAULT FALSE を追加（既存行は false のまま）。
- 予定外 visit の生成値:
  - `patient_id` = QR 解決患者 / `visit_date` = 当日(JST) / `start_time` = 打刻時刻(JST)
  - `end_time` = 暫定で 開始 + 患者の基本訪問時間（`service_minutes`。無ければ 60 分）→
    **checkout 時に実退出時刻へ更新**
  - `course_id` = NULL / `primary_staff_id` = **打刻スタッフ**（生成後に本人が /m/today で
    自分の visit として見え、checkout 導線が既存可視性のまま成立する。決定#4の「書き換えない」
    は既存予定 visit の話であり、予定外は実績者=担当で矛盾しない）
  - `status` = in_progress（生成と同時に到着打刻するため）
- 代行はスキーマ変更なし（検出 = 最新 arrival checkin の staff_id が visit の担当集合
  {primary/secondary/mentor/assignments/同行} に含まれない）。

## 4. API

### 4-1. resolve API の拡張（`GET /visits/resolve-qr/{token}` — 先行タスクで新設）
- 現行（第1弾）: 自分の担当候補のみ返す。
- 拡張（本設計・**凍結コントラクト 2026-08-16**）:

```
200: {
  "patient_name": "田中 太郎",        // v2追加。代行/予定外の確認表示用（氏名のみ・住所等は返さない）
  "candidates": [{
    "visit_id": "...", "start_time": "09:00:00", "end_time": "10:00:00", "status": "planned",
    "planned_staff_name": "山田 花子",  // 予定担当(primary)名。未割当は null
    "is_mine": false,                   // 自分の担当集合(primary/secondary/mentor/assignments/同行)か
    "is_unplanned": false               // is_unplanned 既存行も返す（二重生成の抑止・退出導線に使う）
  }]  // その患者の当日(JST)visit全件・未削除・非取消・start_time昇順
}
404 未知トークン / 410 失効 / 403 staff未紐付け（第1弾のまま）
```

- 担当外スタッフに患者氏名・予定スタッフ名・時間帯を開示するのは、QR 所持=現地に居る
  前提（決定#1）で許容。氏名は誤った患者への記録を防ぐ確認表示に必須。

### 4-2. 担当外打刻の許可（QR capability 分岐）
- `POST /visits/{id}/checkin` / `checkout`: `_load_visit_for_checkin` の可視性判定に
  **QR capability 分岐**を追加。可視でない場合でも、payload の `qr_token` が
  **その visit の患者**に解決できれば通す（解決失敗・別患者は従来どおり 404/409/410）。
  `qr_token` 無しの担当外は従来どおり 404（= 決定#6 の QR 必須をサーバ側で強制）。
- `GET /visits/{visit_id}`: 同じ capability 分岐を**読み取りにも適用**（担当外の訪問詳細
  フォールバック表示を一本化するため。token が患者一致なら 200）。トークンは
  **`X-QR-Token` ヘッダで送るのが正**（`?qr_token=` クエリは後方互換のみ。アクセスログ/
  Referer/履歴への露出対策・レビュー指摘 2026-08-16）。
- **担当外への GET は絞り込み projection**（レビュー指摘→ディレクター決定 2026-08-16）:
  capability 分岐を通った担当外に返す VisitRead は `note=None` / `kaipoke_id=None` /
  `staff_assignments=[]` / `accompaniment=None` に落とす。患者氏名・住所・座標・性別・
  時刻・status・latest_checkin・primary 担当名は維持（現地前提で正当・resolve v2 開示済み
  範囲）。担当者本人の GET は従来どおり全量。
- no-show は QR capability 分岐を**適用しない**（担当のみ・現行維持）。
- **代行の退出は再スキャン必須**: FE はトークンを保持しない（第1弾の「1記録で消費・
  退出は改めて現地で読む」を踏襲）。退出時の再スキャン → `/q/{token}` → 候補に
  in_progress の当該 visit が出る → 詳細 → 退出、で一周する。

### 4-3. 予定外打刻（新設 `POST /visits/adhoc-checkin`）
- 入力 = `CheckinCreate` 同等（qr_token 必須）。1トランザクションで
  「visit 生成（§3）+ arrival checkin（judge 経由）+ 通知」まで行う。
- 二重生成ガード: 同患者×同スタッフ×当日で in_progress の `is_unplanned` visit が
  既にあればそれを返す（再スキャンで増殖させない）。同時 POST の競合は
  **advisory lock**（`try_advisory_xact_lock`・hash(patient_id, staff_id, 当日JST)）で
  TX 冒頭に直列化する（read-then-insert だけでは二重生成し得る。レビュー指摘 2026-08-16）。
- **checkout にも substitute / ng_staff 通知を配線**（退出のみの代行が無通知で
  completed になる穴を塞ぐ。reference 冪等なので到着時通知済みなら重複しない）。
- checkout は生成された visit に対する通常の `POST /visits/{id}/checkout`
  （primary=本人なので既存可視性で通る）。checkout 時に `end_time` を実時刻へ更新。

### 4-4. 通知（`services/checkin/notify.py` に producer 追加）
- `checkin_substitute`（代行発生・reference=visit）/ `checkin_unplanned`（予定外発生・
  reference=visit）/ `checkin_ng_staff`（NG または性別制限に抵触するスタッフの打刻・
  reference=visit）。いずれも既存 notifications の reference_type+reference_id 部分
  UNIQUE で冪等（reference_id は必ず非 NULL = `IS NULL` 罠は踏まない）。
- 宛先・文面は `notify_checkin_mismatch` に準拠（admin 向け・患者名+スタッフ名+種別）。
- NG 交差判定: `patient_ng_staff` に (patient_id, staff_id) 行があるか +
  `sex_restriction` と打刻スタッフ性別の不一致。**打刻フロー内では警告もブロックもしない**
  （事実記録・決定#5）。

## 5. モバイル FE

- 入口 = `/q/{token}` ページ（先行タスク）の分岐拡張:
  - 候補に `is_mine` があれば従来どおり自分の visit へ。
  - 担当候補ゼロ（or 本人が選択）→ **代行/予定外の選択画面**:
    当日予定カード（時間帯・予定スタッフ名）+「この予定の代行として記録」ボタン、
    最下部に「予定外の訪問として記録」ボタン。
  - 代行選択 → `/m/today/{visitId}?qr={token}` へ（担当外でも visit 詳細の打刻フローが
    動くよう、詳細ページは qr 付きなら resolve 結果ベースの最小表示を許容する。
    詳細 GET が 404 の場合のフォールバック表示を用意）。
  - 予定外選択 → GPS 取得 → プレビュー（距離判定表示）→ `adhoc-checkin` POST →
    以後は自分の visit として通常フロー（メモ・checkout）。
- アプリ内スキャナ（/m/today の読取）でも同じ分岐を通す: 読取トークンが表示中 visit の
  患者と不一致だった場合、現行の 409 エラー止まりではなく「この QR は別の利用者です。
  代行/予定外として記録しますか？」への導線を出す。
- オフライン退避（`checkin-queue`）: 予定外用に **トークンベースの新 kind**
  （`adhoc_arrival`）を追加。復帰時 flush で adhoc-checkin を叩く。圏外時は候補表示が
  できないため**担当外はすべて予定外扱いで退避**する（復帰後の予定突合はしない・
  モニターの専用行+アラートで管理者が把握して必要なら整理する、をシンプルさ優先で採用）。

## 6. PC 訪問モニター

- `build_monitor` 拡張:
  - 各 visit に `actual_staff_id / actual_staff_name / is_substitute` を付与
    （**いずれかの arrival 打刻者が担当集合外なら substitute** — 「最新 arrival のみ」だと
    代行後に担当が打刻するとバッジが消え通知と不整合になるため。レビュー指摘 2026-08-16）。
  - `is_unplanned` visit は**専用行**に集約（**実績スタッフの主担当拠点別の行キー**
    `(unplanned, office_id)` — 拠点フィルタで行ごと消える/他拠点が混ざる破綻を防ぐ。
    ラベルは共通「📌予定外訪問」・当日発生分のみ・コース行グルーピングから除外）。
    バー表示は「患者名+実績スタッフ名」。
- `compute_alert` 拡張: `is_substitute` / `is_unplanned` は **review 級**でトレイに載せ、
  理由ラベル「代行」「予定外」を表示（トレイ優先順は 未訪問→場所違い→要確認 の現行を
  維持し、要確認内の一種として扱う）。reviewed（確認済み）で消えるのは現行どおり。
- タイムライン: 代行バーにバッジ+実績名（`MonitorTimeline.tsx`）。既存の行レベル⚠
  （schedule 担当≠visit 予定担当）と視覚的に区別。詳細パネルに「予定/実績」並記。
- 地図・近隣候補・ペア補正は現行ロジックのまま（予定外 visit も患者座標があれば
  距離判定は通常どおり効く）。

## 7. 相互作用・注意事項（実装時に必ず確認）

1. **カイポケ smart-inbound**: 打刻あり日=差分モードの日単位判定に、予定外 visit の打刻が
   加わる。取込・置換・「取り込み前に戻す」との突合を実装時に実機確認すること。
   予定外 visit がカイポケ側実績とどう対応するか（手入力運用か）は PO 確認事項。
2. **未訪問 cron / missing 通知**: 代行到着は既存 `resolve_checkin_missing` で予定 visit の
   未訪問通知を自動解決する（代行を予定に紐付ける最大の利点・退行させない）。
3. **週生成・提案エンジン**: `is_unplanned` visit は PFV 由来ではないため、週生成の
   洗い替え・reset-to-fixed 系が誤って消したり複製したりしないことを確認
   （visit_date 当日限りの行なので通常は無風のはずだが、テストで固定する）。
4. **QR 再発行**: 代行・予定外フロー中にトークンが失効した場合は既存 410 文言で案内。
5. **GPS 2年パージ / 監査**: 新経路も既存 visit_checkins に載るため追加対応不要。

## 8. 実装フェーズ

- **Phase A**（先行・実装中）: `/q/{token}` ディープリンク + resolve API（担当分のみ）。
- **Phase B**（BE）: mig 0071 `is_unplanned` / resolve 拡張（全候補+予定スタッフ名） /
  QR capability 分岐 / `adhoc-checkin` / 通知3種 / モニター合成拡張。
- **Phase C**（モバイル FE）: 代行・予定外の選択フロー / 詳細ページの qr フォールバック /
  オフライン `adhoc_arrival`。
- **Phase D**（PC モニター FE）: 代行バッジ / 予定外専用行 / トレイ理由ラベル。
- テスト観点: 担当外 QR 打刻（成功/トークン無し404/別患者409/失効410）、adhoc 二重生成
  ガード、代行の missing 自動解決、NG 交差通知の冪等、モニター合成（substitute 判定・
  専用行・reviewed で消える）、オフライン flush、当日外 visit を候補に出さない(JST)。

## 9. PO 確認事項（未決・実装前に回収）

1. 予定外 visit の暫定所要時間の既定（基本訪問時間が無い患者の 60 分固定で良いか）。
2. 予定外 visit のカイポケ側の扱い（実績としてカイポケへ手入力する運用か・取込との突合）。
3. 通知文言（代行/予定外/NG 交差の3種）。
