# 新人同行（trainee accompaniment）設計書 v1.1

作成 2026-07-12 / ステータス: **PO要件確定済み・criticレビュー反映済み（v1.1）・実装開始可**
v1.1 変更点: criticレビュー（REVISE→条件反映）— C-1 モバイル可視性3経路の補完、M-1 撤去対象の完全列挙、
M-2/M-3 展開フックの挿入位置とコースstatus条件、S-1/S-2 孤立リンク・soft-deleteテンプレ対策、N-1/N-2 UI明確化
関連: `docs/plans/two-staff-pairing-design.md`（2名体制=別概念・§2参照）・
`docs/plans/kaipoke-reverse-sync-design.md`（逆取込との整合・§9参照）・
メモリ `careflow-staff-assignment-source.md`（PFV正の設計原則）

---

## 0. 背景と目的

新人スタッフ（`staff.is_trainee=true`）が先輩スタッフの訪問に同行する運用を、
スケジュール上で**設定・可視化・カイポケ実績反映**できるようにする。

### 現状の問題（2026-07-12 調査済み・コード根拠は §3）

1. 既存の同行設定（スタッフ詳細の「曜日×午前/午後×同行者」）は**紐付けの向きが逆**
   （新人がコースを持ち、先輩が裏で付く）で、今回確定した運用モデルと矛盾する。
2. 設定は Layer3 が裏側で VSA に注入するだけで、**スケジュール・モニター・モバイルの
   どの画面にも表示されない**。同行者本人のモバイルにも出ない。
3. カイポケへ同行実績（職員名2）が反映されない。

### 確定した運用モデル（PO決定 2026-07-12）★全判断の基準

| # | 決定事項 |
|---|---|
| 1 | **紐付けの主体は新人**。新人が先輩のコース/患者訪問に「同行」として付く |
| 2 | **新人フラグON中はコースを持たない**（自動・手動とも割当対象外）。OFFで通常スタッフへ自動復帰 |
| 3 | 同行しても**患者の枠は1つのまま**（2名体制=PFV slot0/1 の2枠方式には乗せない） |
| 4 | カイポケへは**職員名2に正規スタッフとして**反映（同行フラグ等の特別扱いなし。新人も1人前カウント=2名対応扱い） |
| 5 | 紐付けUIは**入り口を制限しない**: コース（日単位）も患者個別も同一モードで柔軟に混在選択 |
| 6 | 時間重複が残っている間は**確定ブロック**（保存不可） |
| 7 | **毎週の既定**（PFV流「毎週この曜日はこのコースに同行」）を持ち、週ごとの随時編集も可能 |
| 8 | 既存の「曜日×午前/午後」設定機能は**廃止** |

補足（週1コース制約の再解釈・壁打ちで確定）: 当初の「コース丸ごとは週1コースまで」は、
日単位選択への変更に伴い**「時間重複する選択は不可」という物理ルールに吸収**する。
同一日の2コース目は終日重複で自動ブロック（=実質同一日1コース）、別日の別コースは許可。

---

## 1. 用語と概念整理

| 用語 | 意味 | 実装 |
|---|---|---|
| **2名体制** | 患者側の要件で常時2名必要 | PFV `slot_index 0/1` → 同時刻・別コースの visit 2枚 + `visit_group_id`（W-12設計・既存・**本設計では触らない**） |
| **新人同行** | スタッフ側の育成事情で新人が付く。枠は1つ | 本設計。専用テーブルで訪問/コースへリンク |
| 同行リンク | 新人×（コース or 訪問）の紐付けレコード | `trainee_accompaniments`（§4） |
| 毎週の既定 | 「毎週この曜日はこのコースに同行」 | `trainee_accompaniment_defaults`（§4） |
| 実効同行訪問 | ある週で新人が実際に同行する訪問の集合 | コースリンク先コースの planned 訪問 ∪ 個別リンク訪問（§5） |

---

## 2. 設計原則との整合

- **PFV正・マスタ駆動**（PO 6箇条）: 既定→週展開→例外編集 の2層は PFV→週生成→手動編集と同型。
  受け入れ枠の「この週だけ＞毎週」合成とも同じ型で現場の学習コスト最小。
- **ミラー書き込みをしない**: 同行リンクは専用テーブルが**唯一の正典**。
  `visits.primary/secondary/mentor_staff_id`・VSA には**書かない**（過去のVSA⇔primary同期事故
  4件の教訓。読み出し側が JOIN で解決する）。
- **警告主義の例外**: 案αは「警告のみ・ブロックしない」だが、同一人物の同時刻2箇所は
  物理的に不可能なため、時間重複のみ**確定ブロック**とする（PO確定）。

---

## 3. 現状実装と撤去対象

### 現状（調査 2026-07-12・再調査不要）

| 対象 | 場所 | 扱い |
|---|---|---|
| `staff.is_trainee` フラグ | `backend/app/models/staff.py:60` | **存続**（本機能の起点） |
| `staff_companion_assignments` テーブル | `models/staff_companion_assignment.py`（mig 0018） | **廃止**（Phase 2 で drop） |
| 同行CRUD API | `api/v1/staff_companion.py` | **廃止** |
| コース×週 GET スタブ（W15・常に`[]`） | `api/v1/staff_companion_assignments.py` | **廃止**（本設計の新APIが後継） |
| スタッフ詳細の設定/閲覧パネル | `StaffCompanionPanel.tsx` / `StaffCompanionViewer.tsx` | **廃止**（§7.5のサマリ表示に置換） |
| Layer3 の同行者解決＋VSA注入 | `layer3_assignment.py:2651-2711, 3472-3505`・`api/v1/schedule.py:2112-2129` | **撤去**（向きが逆・新モデルと矛盾） |
| pending_request の `staff_mentor` タイプ | `services/pending_request_applier.py:313-433` | **撤去**（既存pending行の残置確認をmigrationで） |
| 新人の自動割当除外（H8） | `auto_allocator_v2.py`（is_trainee=false フィルタ 3352,3362,3509,9848 ほか）・`auto_allocator.py:758-767,981-999` | **存続**（決定事項2の土台） |
| 稼働カウントの trainee 除外 | `schedule_v2.py:2028`（count_active_staff_per_weekday） | **存続** |
| モバイル可視性フィルタ | `api/v1/visits.py:46-57` `_staff_visibility_filter` | **拡張**（§6.4） |

### 撤去対象の完全リスト（Phase 2・criticレビューM-1反映）

Backend:
- `models/staff_companion_assignment.py`＋`models/__init__.py:38` の import
- `models/staff.py:75-88` の `companion_assignments_as_trainee` / `_as_companion` リレーション（**migrationと同時に削除**しないとモデル読み込みエラー）
- `schemas/v2/staff_companion_assignment.py`＋`schemas/v2/__init__.py` の re-export
- `api/v1/staff_companion.py`・`api/v1/staff_companion_assignments.py`＋`api/v1/__init__.py:39-40,97,100` の router 登録
- `services/pending_request_applier.py:313-433`（staff_mentor タイプ）
- `layer3_assignment.py` の `_resolve_companion_staff_id`（2651-2711）・`_persist` 注入（3472-3505）・`schedule.py:2112-2129` の trainee_ids 構築
- `tests/test_staff_companion.py`・`tests/test_staff_companion_assignments_endpoint.py`

Frontend:
- `app/(app)/staff/_components/StaffCompanionPanel.tsx`＋`__tests__/StaffCompanionPanel.test.tsx`
- `app/(app)/staff/[id]/_components/StaffCompanionViewer.tsx`
- `app/(app)/staff/[id]/edit/page.tsx:44` の `useDeleteCompanionAssignments` import と is_trainee OFF 時の呼び出し（§7.5の新ダイアログへ置換）
- `lib/queries/staff_companion.ts`・`lib/queries/staff_companion_assignments.ts`
- `lib/schemas/v2/staff_companion_assignment.ts`＋`lib/schemas/v2/index.ts` の re-export（`lib/schemas/staff.ts` の import 関係も確認）

### 使わないと決めた既存構造（理由つき）

- `visits.secondary_staff_id` / `mentor_staff_id`: v1残置。書くと2名体制（required_staff_count）
  との意味衝突・同期事故リスク。**同行では書かない**。カイポケCSV生成時に同行テーブルから解決する（§9）。
- VSA追加行: 役割マーカーが無く「正規の2人目」と区別不能。**書かない**。

---

## 4. データモデル（新規2テーブル・migration 1本）

### 4.1 `trainee_accompaniment_defaults`（毎週の既定・テンプレ層）

```sql
CREATE TABLE trainee_accompaniment_defaults (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trainee_staff_id    UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    weekday             SMALLINT NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    course_template_id  UUID NOT NULL REFERENCES course_templates(id) ON DELETE CASCADE,
    created_by          UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at / updated_at (TimestampMixin),
    UNIQUE (trainee_staff_id, weekday)   -- 1新人×1曜日 = 1コース既定（同日2コースは物理的に不可）
);
```

- 恒久テンプレート `course_templates` を参照（PFVと同じアンカー。週インスタンスは
  `courses.template_id` 経由で解決）。
- 既定は**コースのみ**。患者個別の「毎週この患者」既定は初版スコープ外（必要になったら拡張）。

### 4.2 `trainee_accompaniments`（週の実効リンク・唯一の表示/連携ソース）

```sql
CREATE TABLE trainee_accompaniments (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trainee_staff_id  UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    target_type       VARCHAR(8) NOT NULL CHECK (target_type IN ('course', 'visit')),
    course_id         UUID REFERENCES courses(id) ON DELETE CASCADE,
    visit_id          UUID REFERENCES visits(id) ON DELETE CASCADE,
    source            VARCHAR(8) NOT NULL DEFAULT 'manual' CHECK (source IN ('default', 'manual')),
    created_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at / updated_at,
    CHECK ((target_type = 'course') = (course_id IS NOT NULL)),
    CHECK ((target_type = 'visit')  = (visit_id  IS NOT NULL)),
    UNIQUE (trainee_staff_id, course_id),
    UNIQUE (trainee_staff_id, visit_id)
);
CREATE INDEX ix_ta_course ON trainee_accompaniments (course_id);
CREATE INDEX ix_ta_visit  ON trainee_accompaniments (visit_id);
CREATE INDEX ix_ta_trainee ON trainee_accompaniments (trainee_staff_id);
```

- **コースリンクは「生きた参照」**: 後からコースに患者が追加/移動されても自動で同行対象に
  含まれる（物質化コピーしない）。個別リンクは visit へ直接。
- `source='default'` = 週生成時の既定展開由来、`'manual'` = 画面からの手動追加。
  「固定枠に戻す」時の再展開判定に使う（§5.2）。
- 週の特定は JOIN 先（courses.iso_year/iso_week・visits の日付）から導出し、
  本テーブルには持たない（正規化・ズレ防止）。

## 5. 既定の展開（物質化）ロジック

### 5.1 展開タイミング（criticレビューM-2/M-3反映）

共通サービス関数 `expand_accompaniment_defaults(db, iso_year, iso_week)` を新設し、以下の3地点から呼ぶ。
**冪等（既存リンクはスキップ）かつ週全体を対象**とする（patient_id 部分リセットでも週全体を再走査——
defaults は新人×曜日で高々数行のため走査コストは無視できる。スコープ絞りのバグを構造的に避ける）。

1. **週生成（generate_week_only）**: `schedule.py` エンドポイントで `expander.expand_week()` 成功後・commit 前
2. **固定枠に戻す（reset_visits_to_fixed）**: `auto_allocator_v2.py` の**関数内部・return 直前**に挿入
   （schedule_v2.py の3呼び出し元 — reset_to_fixed_endpoint / sync_fixed_to_week_endpoint /
   apply_individual_proposal — すべてを1箇所でカバー）
3. **自動スタッフ割当（assign-staff-only）完了後**: コースが proposed→確定 に遷移した直後の取りこぼし回収

**展開対象コースの条件**: `courses.template_id = default.course_template_id AND weekday一致 AND
course_status != 'proposed' AND deleted_at IS NULL`（proposed コースへはリンクを張らない——
再算出で soft-delete された際に孤立リンクが蓄積するため）。
defaults 側は `course_templates.deleted_at IS NULL` の JOIN フィルタを常時適用
（テンプレ soft-delete 時の残存 defaults を無効化。S-2）。

**孤立リンクの掃除（S-1）**: `reset_visits_to_fixed` のフック内で、当該週の
soft-delete 済み visit / course を参照する `trainee_accompaniments` 行を物理削除する
（visit は soft-delete のため FK CASCADE が発火しない。読み出しは live JOIN で守られるが蓄積を防ぐ）。

### 5.2 再生成・編集との相互作用（割り切りを明文化）

| 操作 | 同行リンクへの影響 |
|---|---|
| コース/訪問の hard delete | FK CASCADE で自然消滅 |
| コースの soft delete（apply の入替等） | 読み出しが live JOIN（`deleted_at IS NULL`）のため自然に無効化 |
| 固定枠に戻す | visit 個別リンクは訪問再構築で消える（**既知の割り切り**・§12）。コース既定は再展開で復元 |
| 既定の変更 | 次に生成/リセットされる週から反映。生成済み週は手動編集で調整 |
| 手動リンク（source='manual'） | 既定の再展開で**上書きしない**（source で区別） |

## 6. API 仕様

すべて `/api/v1` 配下。RBAC は「全ロール同一表示・操作は権限どおり」原則:
**GET=全ロール / PUT系=admin・manager**（staff は閲覧のみ）。

### 6.1 `GET /trainee-accompaniments?iso_year=&iso_week=[&trainee_staff_id=]`

その週の全同行リンク＋解決済み実効情報を返す。

```jsonc
{
  "items": [
    {
      "id": "...", "trainee_staff_id": "...", "trainee_staff_name": "髙梨",
      "target_type": "course", "source": "default",
      "course": { "id": "...", "weekday": 0, "code": "A", "office_id": "...", "template_id": "..." },
      "visit": null
    },
    { "target_type": "visit", "visit": { "id": "...", "date": "...", "start": "...", "patient_name": "..." }, ... }
  ]
}
```

### 6.2 `PUT /trainee-accompaniments`（週単位の一括置換・確定操作）

既存 companion PUT（全削除→一括INSERT・1TX）の前例を踏襲。

```jsonc
// Request
{
  "trainee_staff_id": "...",
  "iso_year": 2026, "iso_week": 29,
  "course_ids": ["..."],          // 日単位コースインスタンス
  "visit_ids": ["..."],
  "defaults": [                    // 「毎週の既定にする」チェック分（任意）
    { "weekday": 0, "course_template_id": "..." }
  ]
}
```

- **バリデーション（すべてBEでも実施＝FEブロックとの二重防御）**:
  - `staff.is_trainee = true` でなければ 409（既存 companion PUT と同じ流儀）
  - 実効同行訪問集合（§1）を構築し、**時間重複があれば 422**。
    レスポンスに重複ペアの詳細（日付・時刻・患者名×2）を含めFEがそのまま表示できる形にする
  - **同住所ペアの免除（2026-07-12 PO報告で追加）**: 重複ペアのうち患者座標バケット
    （`SAME_ADDRESS_TOLERANCE`=0.001・FE `buildSameAddressKey` と同じ .3f 量子化）が
    一致するものは**重複扱いしない**。同住所×同時刻は「90分の間に2人とも回る」
    正当な運用（90分占有ルール）であり物理矛盾ではないため。座標が無い患者は
    免除せず保守的にブロック。FE/BE 双方に同一の免除を実装
  - course_ids / visit_ids の存在・週一致・soft delete チェック
- 置換範囲はその新人×その週のみ。defaults の扱い（曖昧性排除）:
  **キー省略 or null = 既定に一切触れない。配列が来た場合も「含まれた曜日の upsert のみ」**
  （`defaults: []` も「触れない」と同義）。**既定の削除は §6.3 の PUT 全置換のみが行う**。

### 6.3 `GET/PUT /trainee-accompaniment-defaults?trainee_staff_id=`

既定の一覧・置換（曜日×course_template の配列を全置換）。スタッフ詳細のサマリ（§7.5）と
同行モードの「毎週の既定にする」チェックの裏側。

### 6.4 既存APIの拡張（非破壊）

| API | 変更 |
|---|---|
| 週/日タイムラインの訪問射影（VisitV2Read 系） | 変更なし（FEが §6.1 を並行フェッチしてクライアント側JOIN。R-9 の patient_sex FE join と同じ手法） |
| `_staff_visibility_filter`（`visits.py:46`） | 同行サブクエリを OR 追加: `visit_id IN (個別リンク) OR course_id IN (コースリンク)` → **新人本人のモバイル「今日の訪問」「今週の予定」に同行訪問が出る** |
| **`GET /visits/{id}`（単体・`visits.py:306-309`）★C-1** | staff ロールの可視性 set 判定に同行リンクの OR 条件を追加（漏れると新人がカードをタップした瞬間 404） |
| **`POST /visits/{id}/checkin` / `/checkout`（`_load_visit_for_checkin`・`visits.py:676-681`）★C-1** | 同上の OR 条件追加。**新人は自分の ID でチェックイン可能**（`VisitCheckin.staff_id`=新人。設計判断: カイポケ上も正規2人目扱いのため打刻も本人が行う）。`judge_checkin`/`notify_checkin_mismatch` が「担当≠打刻者」を警告する経路が無いか実装時に確認し、同行者打刻を正当として扱う |
| `VisitRead` 射影（モバイル） | `accompaniment: { staff_id, staff_name } | null` を非破壊追加（R-9 の patient_sex 追加と同じ流儀） |
| モニター `build_monitor` | MonitorVisit に `accompaniment_staff_name` を追加（行ヘッダ/詳細パネル表示用） |
| `PATCH /courses`（担当変更） | `assigned_staff_id` が `is_trainee=true` のスタッフなら 422「新人はコース担当にできません（同行で割り当ててください）」（§8） |

## 7. UI 仕様

### 7.1 同行モード（週タイムライン `WeekTimelineBoard.tsx` 起点）

1. ツールバーに **「👥 新人同行」** ボタン（`is_trainee=true` の active スタッフが1人以上、
   かつ編集権限がある場合のみ表示）
2. 押すと同行モードへ。新人セレクタ（1人なら自動選択）＋既存リンクが選択済み状態で描画される
3. **選択操作（入り口の制限なし・混在自由）**:
   - **各曜日のコース列ヘッダ**（例: 稲毛Aセクション内の「月」列ヘッダ）をクリック →
     その曜日のそのコース全体を選択/解除（トグル）。コースセクション見出し（稲毛A全体）は選択対象にしない
   - 訪問カードをクリック → その患者1件を選択/解除（トグル）
   - コース選択済みコース内の訪問カードは個別クリック不可（コース丸ごとに含まれている旨をツールチップ表示）
   - **同行モード中は通常モードのクリック挙動（患者詳細ダイアログ・DnD・空き枠登録）をすべて抑止**（N-2）
4. **リアルタイム重複判定（クライアント側）**: 選択のたびに実効同行訪問集合を再計算し、
   同一日で時間帯（開始〜終了=開始+サービス時間）が交差するペアを検出
   - 重複カードは赤枠＋下部バーに「⚠ 時間が重複しています: 7/14(月) 10:00 山田様（稲毛A）× 10:00 佐藤様（稲毛C）— 同時には行けません」
   - **重複が1件でも残っている間は［確定］を無効化（確定ブロック）**
5. 下部固定バー: 「◯コース＋◯件選択中」／警告領域／
   「☑ コース選択を毎週の既定にする」チェック／［確定］［キャンセル］
6. ［確定］→ §6.2 PUT（422 が返った場合も同じ警告UIで表示=二重防御）
7. 日タイムライン（`TimelineDayBoard.tsx`）にも同モードを載せる（選択対象がその日に限られるだけで同一実装を共有）

### 7.2 タイムライン表示（常時・モード外）

- 同行対象の訪問カードに小バッジ **「👥◯◯」**（同行新人名・姓のみ）
- コース丸ごと同行の日はコース列ヘッダに **「同行: ◯◯（新人）」**
- 配色は機能層の既存トーン（info系）を使用。ブランドピンクは使わない
  （らく助リブランディングの「機能層は据え置き」原則）

### 7.3 モニター

- 該当訪問を含む行の担当表示に **「＋◯◯（同行）」**、詳細パネルにも同行者を明記
- コース⇔訪問担当の⚠突合（17a8f33）と同居できるよう、同行は別ラベルで表示（担当乖離警告と混同させない）

### 7.4 モバイル

- **新人本人**: 「今日の訪問」「今週の予定」に同行訪問が表示され、カードに「同行」バッジ。
  新人はコースを持たないため、これが新人の予定表そのものになる。
  **訪問詳細の閲覧・QRチェックイン/チェックアウトも新人本人ができる**（§6.4 C-1 の3経路対応が前提）
- **先輩側**: 自分の訪問カードに「同行: ◯◯」表示
- 訪問詳細にも同行者名を表示

### 7.5 スタッフ詳細（新人）

- 旧 `StaffCompanionPanel/Viewer` を撤去し、**閲覧専用サマリ**に置換:
  「毎週の既定」一覧（曜日×コース）＋「今週の同行」実効一覧。編集導線はスケジュール画面へのリンクのみ
- `is_trainee` OFF 操作時の確認ダイアログ: 「今週以降の同行リンクと毎週の既定を削除します。
  よろしいですか？」→ OK で将来週の links＋defaults を削除（過去週は実績履歴として保持）。
  旧実装（OFF→同行割付DELETE）の前例踏襲

## 8. 「新人はコースを持たない」制御

| 経路 | 対応 |
|---|---|
| 自動割当（assign-staff-only / auto_allocator） | **既存H8で除外済み**・維持（回帰テストで固定） |
| 固定枠に戻す（reset_visits_to_fixed のローテーション） | ローテ母集団の trainee 除外を実装時に確認・不足なら追加 |
| 手動（コース担当ドロップダウン） | FE候補から `is_trainee=true` を除外＋BE `PATCH /courses` で422（§6.4）。マスタ駆動なのでフラグOFFで自動復帰 |
| `is_trainee` ON 操作時 | 当該スタッフが**今週以降のコース担当に残っている場合は警告表示**（自動解除はしない・警告主義原則③。管理者が固定枠に戻す/担当変更で解消） |

## 9. カイポケ連携（Phase 3・方針）

**前提の要確認事項（着手条件）**: RPA側（別リポジトリ `PlaywrightTest1`）が
カイポケ画面の職員2欄の書き込みに対応しているか。**未対応なら先にRPA側の改修が必要**。

- **月次CSV**（`csv_builder.py:242-251`）: 職員名2 の解決順を
  `secondary_staff_id（要2名の正規2人目）→ 同行リンクの新人 → mentor(レガシー)` とする。
  要2名＋同行の3人ケースは職員名3に同行（現状も3枠対応済み）
- **週次反映（diff/apply）**: `Correction.staff2`（`engine.py:83-86`・**2枠配管は実装済み**）へ
  同行者を載せる。生成側 `local_diff.py:174-209` のスタッフ解決に同行テーブルを追加。
  **制限事項: Correction は2枠までのため、要2名＋同行の3人ケースは週次反映で3人目が落ちる**
  （月次CSVでは反映される。頻度を見て Correction の staff3 拡張を判断）
- **逆取込（inbound.py:457-471, 586-614）の整合 ★Phase 3の本丸**:
  カイポケ側 staff2 が「同行リンク済みの新人」と一致する場合は**同行由来とみなし、
  `required_staff_count=2` への昇格・`secondary_staff_id` への書き込みをしない**
  （突合しないと、送った同行が「要2名患者」として返ってくるラウンドトリップ汚染が起きる）

## 10. テスト観点

### BE
- PUT の重複422（同時刻・部分交差・コース×個別の交差・跨日境界）／trainee以外409／週不一致422
- 既定展開の冪等性（週生成2回・reset後の再展開・manual リンク不上書き）
- `_staff_visibility_filter` 拡張（新人にコースリンク経由/個別リンク経由の訪問が見える・他人には漏れない）
- `PATCH /courses` の trainee 422／H8除外の回帰固定
- 旧テーブル drop migration（downgrade 含む）・pending `staff_mentor` 残行の扱い

### FE
- 同行モードの選択トグル・混在選択・重複時の確定ボタン無効化・422表示
- バッジ表示（タイムライン/モニター/モバイル）・新人モバイルの同行訪問表示
- 既存基準値 **1116 pass / 0 fail** 維持（`pnpm vitest run --exclude "e2e/**"`）
- 注意: field系 page.test.tsx は next/navigation モック（usePathname）に敏感（R-8の教訓）

## 11. フェーズ分割

| Phase | 内容 | 備考 |
|---|---|---|
| **1（本命）** | migration（新2テーブル）＋ §5展開 ＋ §6 API ＋ §7 UI（同行モード・重複ブロック・全画面見える化） | ここまでで「設定でき・全員に見える」が完成 |
| **2** | §8手動ガード ＋ §3撤去（旧テーブル/API/UI/Layer3注入/staff_mentor） | 撤去は Phase 1 安定後（新旧並走期間を最小化） |
| **3** | §9 カイポケ職員名2反映 | RPA側の職員2欄対応確認が着手条件 |

デプロイ注意: **migration を含むため `build --no-cache`**（引継ぎ書ハマり所1）。
手順: pg_dump → pull → build(--no-cache) → alembic upgrade head → up -d → healthz 内外。

## 12. リスク・割り切り・ハマり所

1. **固定枠に戻すと visit 個別リンクが消える**（訪問再構築のため）。コース既定は自動復元される。
   現場アナウンス: 「固定枠に戻したら患者個別の同行は付け直し」
2. 同行は**容量・受け入れ枠に影響しない**（acceptance_matrix はスタッフ非依存・コース母集合も不変）。
   新人はカウント外（count_active の trainee 除外は既存仕様のまま）
3. 逆取込の突合（§9）を Phase 3 でやるまでは、**同行を載せた週の逆取込に注意**
   （staff2 差分が「スタッフ変更」として検出され得る）。Phase 3 完了までは
   同行反映はカイポケ送り（apply）側のみ運用しない＝Phase 1-2 の間はカイポケに同行は流れない
4. 旧 `staff_companion_assignments` の既存データは新モデルへ**移行しない**（向きが逆で
   意味が変わるため機械変換不能）。廃止時に PO へ現行設定の一覧を提示して手動で付け直し
5. 週単位ロック等の並行制御は初版では持たない（PUT全置換のlast-write-wins。
   assign-staff-only の409ロックのような防御は運用上の必要が出てから）
6. **「生きた参照」のUX副作用（PO周知事項）**: コース丸ごと同行の週に患者がそのコースへ
   追加/移動されると、同行対象も暗黙に変わる（仕様どおりだが現場が驚かないよう周知）
7. RPA側の職員2欄対応は**確認済み（2026-07-12・本番コンテナ実査）**:
   `auto_apply.py edit_staff()` が `select#chargeStaff2Id1` の選択・クリアに対応、
   diff_engine も staff2 差分検出済み。Phase 3 の着手条件は満たされている

## 13. 未決事項

- なし（壁打ちで全項目確定。§0の決定事項テーブルが正）
- Phase 3 着手時に要確認: RPA側の職員2欄対応（§9）／要2名＋同行の3人ケースの実発生頻度
