# 同行が登録できない件（小西彩稀 → 熊澤妙子・2026-09-01〜09-05）調査レポート

調査日: 2026-08-31 / 種別: **READ-ONLY 調査**（ソース無変更・本ファイルのみ新規作成）
対象週: ISO 2026-W36（9/1 月 〜 9/7 日）
関係設計書: `docs/plans/trainee-accompaniment-design.md` v1.1 / `docs/plans/general-accompaniment-design.md` /
`docs/plans/two-staff-pairing-design.md` / `docs/plans/kaipoke-service-content-design.md`

---

## 1. 結論

**(A) 今回の「エラー」はサーバに一度も届いていない。フロントエンドが［確定］ボタンを無効化して止めている。**

本番ログ実測（48時間・8/31 22:30 JST まで）:

- `accompan` を含むパスへの **書き込み（POST/PUT/PATCH/DELETE）はゼロ件**、4xx/5xx もゼロ件
- 通信は `GET /api/v1/accompaniments?iso_year=2026&iso_week=36` のみ
- `accompaniments` テーブルに W36 の行なし・8/31〜9/6 の visit 単位行なし・`accompaniment_defaults` は空

これはフロント側の確定ブロック仕様と完全に一致する。

`frontend/components/schedule/timeline/accompaniment/useAccompanimentController.ts:511-518`

```ts
  const canConfirm =
    active &&
    !!selectedStaffId &&
    overlap.messages.length === 0 &&
    serverOverlaps.length === 0 &&
    serverConflicts.length === 0 &&
    defaultsDuplicateMessages.length === 0 &&
    !updateMut.isPending;
```

`frontend/components/schedule/timeline/accompaniment/AccompanimentBar.tsx:150-165`

```tsx
            disabled={!canConfirm}
            data-testid="accompaniment-confirm"
            title={
              canConfirm
                ? undefined
                : warnings.length > 0
                  ? '時間の重複を解消してください'
                  : '同行するスタッフを選択してください'
            }
```

管理者が見た画面は「［確定］が押せない（グレー）＋ 赤い警告リスト」で、文言は

> ⚠ 時間が重複しています: 9月1日(月) 10:00 ◯◯様（稲毛A） × 10:00 △△様（稲毛A） — 同時には行けません

（`frontend/lib/scheduling/accompanimentOverlap.ts:116-119`）。**「エラーが出た」＝ HTTP エラーではない。**

**(B) 仮にサーバへ投げても同じ理由で 422 になる。フロントだけ緩めても解決しない。**

`backend/app/api/v1/accompaniments.py:445-455` が、NG/性別の「確認して通す」フローより**先**に、
override 不可のハードブロックとして時間重複を弾く。

```python
    effective = await load_effective_visits(db, course_ids=course_ids, visit_ids=visit_ids)
    own_duty = await load_own_duty_visits(db, target_staff_id, monday, sunday)
    conflicts = await collect_accompaniment_conflicts(db, effective=effective, own_duty=own_duty)
    if conflicts:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=accompaniment_overlap_detail(conflicts),
        )
```

**(C) 弾いているのは「小西さんの都合」ではなく「熊澤さんの持ち予定どうしの重なり」。**
判定は *選択された同行対象訪問の集合を、それ自身と総当たり* するだけで、
「その訪問が誰の担当か」を一切見ない。熊澤さん 1 人に終日つく場合でも、
熊澤さんの予定表の中に同時刻の 2 件があれば必ず衝突として検出される。

**(D) これは事故ではなく PO 決定の意図どおりの挙動。したがって緩和は「決定の改訂」を伴う。**

`docs/plans/trainee-accompaniment-design.md:30`（PO決定 2026-07-12）

> | 6 | 時間重複が残っている間は**確定ブロック**（保存不可） |

同 `:38-40`

> 補足（週1コース制約の再解釈・壁打ちで確定）: 当初の「コース丸ごとは週1コースまで」は、
> 日単位選択への変更に伴い**「時間重複する選択は不可」という物理ルールに吸収**する。
> **同一日の2コース目は終日重複で自動ブロック（=実質同一日1コース）**、別日の別コースは許可。

同 `:62-65`

> - **警告主義の例外**: 案αは「警告のみ・ブロックしない」だが、同一人物の同時刻2箇所は
>   物理的に不可能なため、時間重複のみ**確定ブロック**とする（PO確定）。

今回の PO 要望「1人のスタッフに終日つくなら、その人の予定内の重なりは通せ」は、
この 2026-07-12 決定 #6 の**部分的な撤回**にあたる。実装前に決定として記録が要る（§4-6）。

---

## 2. 同行登録の経路と検証（コード引用・エラー文言）

### 2-1. UI の入口は事実上 1 つだけ

| 入口 | 実体 | 備考 |
|---|---|---|
| **週盤面ツールバー「👥 同行」** | `frontend/components/schedule/v2/CourseDayTablePanel.tsx:5600-5621` | **今回管理者が使ったのはこれ**。押すと週/日タブを強制的にタイムライン表示へ切替 |
| ↳ コース(曜日)単位モード | `useAccompanimentController.ts:284-...` `toggleCourse()` | コース列ヘッダ（例: 稲毛A の「月」）クリックでその日のコース丸ごと |
| ↳ 患者単位モード | 同 `toggleVisit()` | 訪問カードクリックで 1 件ずつ |
| スタッフ詳細の同行サマリ | `frontend/app/(app)/staff/[id]/_components/AccompanimentSummary.tsx` | **閲覧＋週リンクの個別解除のみ**。既定の編集 UI は無い |
| 毎週の既定 (`PUT /accompaniment-defaults`) | フック `useUpdateTraineeAccompanimentDefaults` は `frontend/lib/queries/trainee_accompaniments.ts:184` に存在するが、**呼び出している画面がゼロ**（grep 済み） | 既定は同行バーの「☑ コース選択を毎週の既定にする」経由でしか設定できない ＝ **同じ確定ブロックの中にいる** |
| VisitActionMenu 等の訪問メニュー | 同行専用のダイアログは**無い** | 訪問単位リンクも上の「患者単位モード」で作る |

> 補足: 「👥 同行」ボタンは新人限定ではない。`CourseDayTablePanel.tsx:4866-4875` が
> `status === 'active'` の全スタッフを候補にし、`is_trainee` の人を先頭に寄せるだけ
> （一般化 §4 セレクタ一般化）。小西さんは `is_trainee=true` なので先頭に出る。

### 2-2. API と検証順序

`backend/app/api/v1/accompaniments.py` docstring `:9-11`

```
PUT  /api/v1/accompaniments
     -> 週単位の一括置換 (1 TX)。対象適格 409 / 時間重複 422 / NG・性別 422 確認
        (RBAC: admin)
```

`put_accompaniments` の検証順（`:379-466`）:

1. スタッフ 404 / 非 active 409 — `_require_accompaniment_eligible`（`:103-115`）
   `detail=f"Staff {staff.id} is not active (status={staff.status!r})"`
2. `course_ids` の存在・soft-delete・週一致 422（`:394-420`）
3. `visit_ids` の存在・soft-delete・週一致 422（`:422-443`）
4. **時間重複 422（ハードブロック・override 不可）** ← 今回の該当箇所
5. NG スタッフ / 性別制限 422（`code='constraint_confirmation_required'`・`acknowledge_constraint_warnings:true` 再送で通過）
6. defaults の曜日重複 / テンプレ存在 422

コード上のコメントが「先に置く」意図を明記している（`:446-448`）:

```python
    # ----- 時間重複判定 (確定ブロック 422・決定#1) -----
    # 同行どうし + 本人担当 (primary/secondary/mentor/VSA) を同じ土俵で検査する。
    # override 不可のハードブロックなので、acknowledge で通せる NG/性別確認より**先**。
```

### 2-3. 422 の本文（サーバ側の文言）

`backend/app/services/accompaniment.py:676-683`

```python
def accompaniment_overlap_detail(conflicts: list[AccompanimentConflict]) -> dict:
    """422 の detail body (一般化 決定#1) を組む — 全経路で同形."""
    return {
        "code": ACCOMPANIMENT_OVERLAP_CODE,        # = "accompaniment_overlap"
        "message": "時間が重複するため同行を登録できません（同時には行けません）",
        "conflicts": [c.to_detail() for c in conflicts],
    }
```

`reason` は 2 値（`backend/app/services/accompaniment.py:645-648`）:

```python
# conflicts[].reason — 'own_duty' = 本人担当と衝突 / 'accompaniment' = 同行選択どうし。
CONFLICT_REASON_OWN_DUTY = "own_duty"
CONFLICT_REASON_ACCOMPANIMENT = "accompaniment"
```

FE 側の日本語化（`frontend/lib/schemas/trainee_accompaniment.ts:254-270`）:

```ts
  if (c.reason === 'own_duty') {
    const paren = course ? `（${course}・ご自身の担当）` : '（ご自身の担当）';
    return `${when} ${span} は ${patient}様${paren}と重なるため登録できません`;
  }
  if (c.reason === 'accompaniment') {
    const paren = course ? `（${course}）` : '';
    return `${when} ${span} は ${patient}様${paren}の別の同行と重なるため登録できません`;
  }
```

### 2-4. 既定の展開（バリデーション無しの裏口）

`PUT /accompaniment-defaults`（`accompaniments.py:606-681`）には **時間重複検査が一切無い**。
検証は「非 active 409 / 曜日重複 422 / テンプレ不存在 422」のみ。
展開は `expand_accompaniment_defaults`（`services/accompaniment.py:116-207`）が行い、
呼び出し地点は 3 つだけ:

- `backend/app/api/v1/schedule.py:1754` — `POST /generate-week-only`（週生成）
- `backend/app/api/v1/schedule.py:1939` — `POST /assign-staff-only`（自動割当の事後回収）
- `backend/app/services/scheduling/auto_allocator_v2.py:10293-10295` — 固定枠に戻す等

展開側にも重複検査は無い（冪等な INSERT のみ）。これが §6 の回避策の根拠になる。

---

## 3. なぜ熊澤さんへの同行が弾かれるか（重なりの定義・同住所ペア等の扱い）

### 3-1. 「重なり」の定義（BE）

`backend/app/services/accompaniment.py:600-614`

```python
def _overlaps(a: Visit, b: Visit) -> bool:
    if a.visit_date != b.visit_date:
        return False
    if not (a.start_time < b.end_time and b.start_time < a.end_time):
        return False
    ka = _same_address_key(a)
    if ka is not None and ka == _same_address_key(b):
        return False
    return True
```

- 半開区間の厳密な交差のみ。**移動時間バッファは考慮しない**（隣接 `a.end == b.start` は OK）。
- 免除は **同住所ペアだけ**。同住所キーは患者座標の `.3f` 量子化（≒100m）。
  `_same_address_key`（`:582-598`）は `patient.lat / lng` のどちらかが `None` なら `None` を返し、
  **座標なし患者は免除されない（保守的にブロック）**。
- `patient_same_address_links`（blocked/preferred/required の明示紐付けテーブル）は**参照していない**。
  免除は座標のみで判定される。

### 3-2. 何と何を突き合わせているか

`collect_accompaniment_conflicts`（`:703-766`）は 2 系統:

```python
    # 1) 同行選択どうし (従来の検査・reason='accompaniment')。
    for a, b in find_time_overlaps(effective):
        ...
    # 2) 同行選択 × 本人担当 (決定#1・reason='own_duty')。
    for e in effective:
        for own in by_date.get(e.visit_date, []):
            if _overlaps(e, own):
```

- `effective` = 選択したコースの planned 訪問 ∪ 個別選択訪問（`load_effective_visits:497-536`。
  soft-delete と `status='cancelled'` を除外）
- `own_duty` = **同行する側（小西さん）** の週内担当訪問（`load_own_duty_visits:540-579`。
  `primary/secondary/mentor_staff_id` または VSA 行）

**重要:** 系統1は「対象訪問がどのスタッフのものか」を**一切見ない**。
`find_time_overlaps`（`:616-643`）は日付でバケットして総当たりするだけ。
つまり **熊澤さん 1 人の予定内の重なりが、そのまま同行の拒否理由になる**。

一方、小西さんは新人（`is_trainee=true`）なので設計上コースを持たない
（`trainee-accompaniment-design.md:30` 決定#2「新人フラグON中はコースを持たない」・
エンジン側も除外済み `:328`）。**よって `own_duty` 系統は空のはずで、今回の拒否は系統1が単独原因**。

### 3-3. FE 側の判定（実際にブロックしている方）

`frontend/lib/scheduling/accompanimentOverlap.ts:100-121`

```ts
        if (!overlaps(a, b)) continue;
        // 同住所ペア (90分占有ルール) は物理矛盾ではないため重複扱いしない。
        // 両方に座標キーがあり一致する場合のみ免除 (キー欠落時は保守的にブロック)。
        if (a.sameAddressKey != null && a.sameAddressKey === b.sameAddressKey) continue;
```

FE は BE 系統1 のみを実装（`own_duty` は見ない）。入力は
`CourseDayTablePanel.tsx:4837-4855` の `accompanimentWeekVisits`。

### 3-4. FE / BE の非対称（3 点・調査で新たに判明）

| # | 項目 | FE | BE | 影響 |
|---|---|---|---|---|
| 1 | **キャンセル訪問** | `overviewVisits`（`CourseDayTablePanel.tsx:1392-1472`）に `status` フィルタが無く、`status='cancelled'` の訪問も同行判定へ流れる | `load_effective_visits` が `Visit.status != VISIT_STATUS_CANCELLED` で除外 | **FE だけで偽の重なりが出る**。「今週だけ取消(manual_cancel) + 急休代替」の組み合わせは、取消済み訪問と代替訪問が同時刻に並ぶため確実に踏む |
| 2 | 本人担当との衝突 | 見ない | `own_duty` 422 | FE を通っても BE で 422 になり得る（今回は小西さんが無担当なので無関係） |
| 3 | 終了時刻欠落 | `endMin = parseHM(end_time) ?? startMin + 35`（`:4840`） | `v.end_time` をそのまま使用 | 端数ケースで判定がズレ得る |

いずれにも **`week_pinned` / `movability` / `visit_group_id`（2名体制）の特別扱いは無い**。
2名体制ペアは「同一患者 = 同一座標」なので同住所免除に自然に乗るが、
**その患者に座標が無ければ免除されず、2名体制がそのままブロック理由になる**。

### 3-5. 熊澤さんのケースで想定される原因（優先度順）

実データは参照できないため、構造から絞り込んだ候補:

1. **同一曜日に 2 コース**（管理者は臨時コース「臨」を持つことがある）。
   コース列ヘッダを 2 つ選ぶと終日重複 → 設計 `:38-40` の「実質同一日1コース」に当たり必ずブロック。
   このとき「毎週の既定にする」も同時に警告を出す（`useAccompanimentController.ts:505-509`）:
   > `毎週の既定は1曜日につき1コースまでです（月曜に2コース選択中）。チェックを外すか、コース選択を1つにしてください`
2. **キャンセル済み訪問の残留**（§3-4 #1）。FE 限定の偽陽性。ここが真因なら **BE は同じ入力を通す**。
3. **同住所ペアなのに座標欠落**。片方の患者に `lat/lng` が無ければ免除されない。
4. **2名体制（`requires_multiple_staff`）の 2 枠**が同時刻に立っており、座標欠落で免除外れ。
5. コース内に純粋に重なる 2 訪問がある（取込・手編集由来）。

→ **切り分け手順**: 同行モードに入り熊澤さんのコースを月〜金で選び、赤い警告バーの文言
（`⚠ 時間が重複しています: 日付 時刻 患者名（コース） × 時刻 患者名（コース）`）を全文控える。
そこに出る患者名・時刻が上のどれに当たるかで、コード変更が要るか運用で済むかが決まる。

---

## 4. 緩和の設計（同一スタッフへの終日同行は重なりを許可・別スタッフ混在は拒否）

### 4-1. 採用案: 案R「持ち主（owner）が同じ重なりは免除」

**ルール**

- 衝突ペア (a, b) について、それぞれの**持ち主スタッフ** owner(v) を解決する。
  - 第一候補 = `courses.assigned_staff_id`（PO 6箇条「PFV正・コース担当が表示の正典」/
    `careflow-staff-assignment-source` の原則。`primary_staff_id` はミラー）
  - フォールバック = `visits.primary_staff_id`（course_id が NULL の臨時/予定外訪問）
- `owner(a) is not None and owner(a) == owner(b)` のときのみ **系統1（accompaniment×accompaniment）の衝突を無視する**。
- owner が解決できない（未割当）ときは **従来どおりブロック**（保守的・同住所免除と同じ思想）。
- **系統2（`own_duty`）は無条件で 422 のまま**。同行者本人の担当と重なるのは物理的に不可能で、PO 要望の対象外。
- 別スタッフ混在で重なる場合も **422 のまま**（PO 要望どおり）。

**なぜこれが正しいか**: 同行者はそのスタッフに「くっついて動く」だけなので、
そのスタッフが元々こなす予定の重なりは同行者にとって新しい物理矛盾を生まない。
既存の同住所ペア免除（「同じ玄関に居られる」）と完全に同じ理屈の一般化になる。

### 4-2. 変更点（ファイル / 関数）

**バックエンド（規模 S）**

| ファイル | 変更 |
|---|---|
| `backend/app/services/accompaniment.py` | 新設 `_resolve_owner_staff(db, visits) -> dict[UUID, UUID \| None]`（`Course.assigned_staff_id` を 1 クエリでバッチ解決 → 無ければ `visit.primary_staff_id`） |
| 同 `collect_accompaniment_conflicts:703-766` | 系統1のループで `owner_a == owner_b and owner_a is not None` ならスキップ。**系統2は触らない** |
| 同 `_overlaps:600-614` / `find_time_overlaps:616-643` | **変更しない**（`collect_accompaniment_duty_warnings:959` が同じ `_overlaps` を使うため、ここを触ると逆方向警告の意味まで変わる）。免除は collect 層で行う |
| `backend/app/api/v1/accompaniments.py:445-455` | 変更不要（`collect_accompaniment_conflicts` の戻りをそのまま使うため） |

**フロントエンド（規模 M）**

| ファイル | 変更 |
|---|---|
| `frontend/lib/scheduling/accompanimentOverlap.ts` | `AccompanimentOverlapEntry` に `ownerStaffId?: string \| null` を追加。`:112` の同住所免除の直後に同 owner 免除を追加（コメントで BE と同一ルールである旨を明記） |
| `frontend/components/schedule/timeline/accompaniment/types.ts:20-31` | `AccompanimentWeekVisit` に `ownerStaffId: string \| null` |
| `.../useAccompanimentController.ts:236-253` | エントリ生成時に `ownerStaffId: v.ownerStaffId` を渡す |
| `frontend/components/schedule/v2/CourseDayTablePanel.tsx:4837-4855` | `ownerStaffId` を `courseById.get(courseId)?.assigned_staff_id ?? v.primary_staff_id` で埋める（`primary_staff_id` は `overviewVisits:1470` で既に運ばれている）。**同時にキャンセル除外 `if (v.status === 'cancelled') continue;` を入れて §3-4 #1 を解消** |
| `.../AccompanimentBar.tsx:202-214` | 免除した重なりを「エラー赤」ではなく info トーンの補足行で出す（任意・PO が可視化を望む場合）: 例「同一スタッフ内の重なり 3 件は同行できます（同じ現場に付くため）」 |

### 4-3. 却下した代替案

| 案 | 内容 | 判断 |
|---|---|---|
| A: acknowledge フラグ | NG/性別と同じく `code='accompaniment_overlap'` を確認ダイアログ化し `acknowledge_overlap_warnings:true` で通す | PO 要望は「そもそも止めるな」。毎回ダイアログは終日同行の常用に耐えない。かつ FE 側で「同一スタッフか」を説明するには結局 owner 解決が要る。**案R の上位互換にならない** |
| B: リンクに「終日シャドー」モードを持たせる | `accompaniments` に列追加 | `target_type='course'` が既に「そのコースに付いて回る」意味を持つ（モデル docstring「コースリンクは生きた参照」）。列追加は mig と UI を増やすだけで情報量が増えない |
| C: 免除を無条件（重なり検査の全廃） | — | `own_duty` 衝突（同行者自身が担当を持つケース＝一般スタッフ同行で普通に起きる）まで通ってしまう。一般化 決定#1 の根幹を壊す |

### 4-4. 追加すべきテスト

**BE（`backend/tests/test_accompaniments_general.py` に追記。既存 `test_put_own_duty_overlap_422_structured:282` / `test_put_own_duty_same_address_exempt_200:368` が手本）**

1. `test_put_same_owner_overlap_allowed_200` — 同一 `assigned_staff_id` のコース内に同時刻 2 訪問 → 200 でリンク生成
2. `test_put_different_owner_overlap_422` — 別スタッフのコース 2 本を同時刻で選択 → 422 `reason='accompaniment'`
3. `test_put_own_duty_overlap_still_422_even_same_owner` — 同行者自身が担当する訪問と重なる → 422 `reason='own_duty'`（免除しない）
4. `test_put_unassigned_course_overlap_still_422` — `assigned_staff_id IS NULL` の重なり → 422（保守的ブロック）
5. `test_put_visit_level_link_owner_from_primary_staff` — `course_id IS NULL` の臨時訪問は `primary_staff_id` で owner 解決

**FE（`frontend/lib/scheduling/__tests__/accompanimentOverlap.test.ts` に追記）**

6. 「同一 ownerStaffId の重なりは検出しない」
7. 「owner が異なれば従来どおり検出」
8. 「owner 片方 null なら免除しない」
9. `useAccompanimentController` テスト: 同 owner の重なりがあっても `canConfirm === true`
10. `CourseDayTablePanel` 側: `status='cancelled'` の訪問が同行判定に入らない（§3-4 #1 の回帰固定）

### 4-5. 規模とリスク

**規模: M**（BE 実装 S ≒ 半日 / FE 実装 M ≒ 1日 / テスト 0.5日 / migration 不要）

| リスク | 内容 | 対処 |
|---|---|---|
| カイポケ職員名2 の二重出力 | 同時刻 2 訪問の**両方**に小西さんが職員名2 として載る（§5）。カイポケ側では「同じ人が同時刻に2件」の登録になる | PO 確認事項。ただし同住所ペアでは既に同じ状態が発生しており、前例としては既存挙動 |
| 週次 Correction の 2 枠制限 | 2名体制（secondary あり）の訪問では小西さんが**職員名3** に落ち、週次反映に載らない（§5-2） | 該当訪問はカイポケ手入力。事前に件数を洗い出す |
| 2人目充足（決定#7） | 同行は「2人目充足」に数える（`accompanimentFulfillment.ts`）。終日同行を張ると複数名対応患者の②カードがプールから一斉に消える | 仕様どおりだが現場に周知。同行を外せば復活する |
| 受入枠・自動割当の稼働 | 一般化 決定#8「同行しても受入枠・自動割付の余力から控除しない」＝**変更なし**。終日同行でも枠計算は動かない | 変更しない |
| 逆方向警告 | `collect_accompaniment_duty_warnings:959` は `_overlaps` をそのまま使う。今回 collect 層のみ変更すれば影響なし | `_overlaps` を触らない設計にした理由 |
| モバイル / モニター表示 | 小西さんの「今日の訪問」に同時刻カードが 2 枚並ぶ | 同住所ペアで既に発生済み。表示は耐える |

### 4-6. 設計書側で必要な改訂（実装前に PO 記録が要る）

- `docs/plans/trainee-accompaniment-design.md:30` 決定 #6「時間重複が残っている間は確定ブロック」
  → **「ただし重なりの両方が同一スタッフの持ち予定である場合は除く」を追記**
- 同 `:38-40`「同一日の2コース目は終日重複で自動ブロック（=実質同一日1コース）」
  → **同一担当者の 2 コースは選択可**へ改訂（別担当の 2 コースは従来どおりブロック）
- 同 `:62-65`「警告主義の例外」の理由付け（同一人物の同時刻2箇所は物理的に不可能）
  → 同行者は同一スタッフに随行するため物理矛盾にならない、と補足
- `docs/plans/general-accompaniment-design.md:26` 決定 #1
  → 「本人担当との重複はハード422」は**維持**、「同行選択どうし」に同一 owner 例外を追加

---

## 5. カイポケ側（職員名2）への反映可否

### 5-1. 月次CSV は出る（決定#6 実装済み）

`backend/app/services/kaipoke/csv_builder.py:363-395`

```python
        # 同行者 (設計決定事項#4 / 一般化 決定#6): 新人同行も一般スタッフの同行も
        # 職員名2 へ「正規スタッフとして」載せる (kind で区別しない)。
        ...
        # 職員名2/3 の解決順 (決定#6): secondary → 同行(決定的順序・複数可) →
        # mentor(レガシー)。同一人物が重複して載らないよう staff_id でデデュープ
        # (順序保持)。primary と同一人物も除外 (職員名1 との二重掲載防止)。
        # デデュープ後の先頭 2 名を職員名2/職員名3 に載せる。
```

同 `:381-386`

> **3 人目以降は落ちる (現行踏襲・一般化 §6 の既存制限)**: カイポケCSV の枠が
> 職員名3 までのため、secondary + 同行2名 のような 4 人目相当は転記されない。
> さらに週次反映 (diff/apply) の Correction は 2 枠 (staff1/staff2) までなので、
> 職員名3 は月次CSV にしか載らない。

同行者は `resolve_accompaniment_by_visit`（`services/accompaniment.py:310`）で全件解決され、
コースリンク（`target_type='course'`）は「生きた参照」なので、
**熊澤さんのコース 5 本にリンクを張れば、そのコース内の全訪問が自動的に職員名2 を持つ**。

### 5-2. 週次差分は「staff2 だけの変更」を検出して edit を出す

`backend/app/services/diff/engine.py:99`

```python
    def has_staff_change(self) -> bool:
        return self.staff1_from != self.staff1_to or self.staff2_from != self.staff2_to
```

`backend/app/services/kaipoke/local_diff.py:14-18`

> ``Correction.staff1/staff2`` は最適化CSV の職員名1/2 をそのまま写した値であり、
> **誰が staff2 になるかは csv_builder の配分順序 1 箇所だけで決まる**
> （…）別人をカイポケへ押すことはない。**週次は 2 枠 (staff1/staff2) までのため職員名3 相当は**（落ちる）

→ 同行リンクを張った後に 9/1週の差分を取ると、対象訪問に
`action='edit'` / `staff2_from=''` / `staff2_to='小西 彩稀'` の Correction が並ぶ。

### 5-3. RPA apply は職員2 欄に対応済み

- RPA ゲート `backend/app/services/kaipoke/rpa_capability.py:63-76` は
  `if action != "add": return False` — **edit は素通し**（サービス内容ゲートの対象外）。
  docstring `:26-31`「delete / edit / date_change は既存行を動かすだけなので素通しでよい」
- RPA 実体は別リポジトリだが、実査記録あり
  `docs/plans/trainee-accompaniment-design.md:416-418`

  > RPA側の職員2欄対応は**確認済み（2026-07-12・本番コンテナ実査）**:
  > `auto_apply.py edit_staff()` が `select#chargeStaff2Id1` の選択・クリアに対応、
  > diff_engine も staff2 差分検出済み。

### 5-4. 制限のまとめ

| 条件 | 職員名2 に載るか | 週次反映されるか |
|---|---|---|
| 通常訪問（secondary なし）+ 同行1名 | ✅ 職員名2 | ✅ edit で反映 |
| 2名体制（secondary あり）+ 同行1名 | 職員名2=secondary / **職員名3=同行** | ❌ **週次は 2 枠まで → 落ちる**。月次CSV のみ |
| 同行 2 名以上 | support優先 → 名前昇順 → id の順で先頭 2 名 | 3 人目相当は落ちる |
| カイポケ側から staff2 を取り込む向き | 既存リンク一致は取り込まない（`inbound.py` 判定①・ラウンドトリップ汚染防止） | — |

---

## 6. 今週（9/1〜9/5）の暫定回避策（安全な順）

### 回避策 0（最優先・所要 5 分）— まず真因を特定する

同行モードで熊澤さんのコースを月〜金選び、**赤い警告バーの文言を全文控える**。
そこに出てくるのが

- **キャンセル済み訪問**なら → §3-4 #1 の FE 限定バグ。**サーバは同じ入力を受け付ける**ので、
  回避策 1（API 直叩き）だけで今週は解決し、恒久対応は FE の 1 行修正で済む。
- **同一曜日の 2 コース**なら → 熊澤さんが本当に 2 コースを持っている。案R の実装が要る。
- **同住所の別患者どうし**なら → 患者の緯度経度欠落を疑う（患者編集で住所を再ジオコーディングすれば免除が効く）。

### 回避策 1（推奨・コード変更なし）— `accompaniments` へ直接 INSERT

FE の確定ブロックも `PUT /accompaniments` の 422 も通らずに済む唯一の正規に近い手段。
リンクは「唯一の正典」であり、読み出しは全経路 live JOIN なので、
**入れた瞬間に盤面バッジ・モバイル・モニター・カイポケ CSV すべてに反映される**
（`models/accompaniment.py` docstring「同行リンクは本テーブルが唯一の正典」）。

```sql
-- 熊澤さんが担当する W36 の月〜金コースへ、小西さんの同行リンクを張る
INSERT INTO accompaniments
  (id, accompanying_staff_id, target_type, course_id, visit_id, source, kind, created_by, created_at, updated_at)
SELECT gen_random_uuid(),
       (SELECT id FROM staff WHERE name = '小西 彩稀' AND deleted_at IS NULL),
       'course', c.id, NULL, 'manual', 'trainee', NULL, now(), now()
  FROM courses c
 WHERE c.iso_year = 2026 AND c.iso_week = 36
   AND c.weekday BETWEEN 0 AND 4
   AND c.deleted_at IS NULL
   AND c.course_status <> 'proposed'
   AND c.assigned_staff_id = (SELECT id FROM staff WHERE name = '熊澤 妙子' AND deleted_at IS NULL)
ON CONFLICT ON CONSTRAINT uq_acc_staff_course DO NOTHING;
```

- **冪等**: `UNIQUE (accompanying_staff_id, course_id)` があるので二度流しても増えない。
- 事前に `SELECT` で対象コースを目視（5 件になるはず。氏名は表記ゆれに注意）。
- `kind='trainee'` は `staff.is_trainee=true` に一致させる（サーバ自動判定と同じ値）。

**注意点**

1. この後 W36 に対して **週生成 / 固定枠に戻す** を実行するとコースが soft-delete され、
   `_cleanup_orphan_links`（`services/accompaniment.py:65-113`）がリンクを物理削除する。今週は再生成しないこと。
2. 管理者が同行モードに入って小西さんを選ぶと、既存リンクが選択済みで描画され、
   重なりのせいで［確定］は押せない。**［キャンセル］で抜ければ何も起きない**（PUT は確定時のみ）。
   誤って別スタッフで確定しても、置換対象は「そのスタッフ×その週」だけなので小西さんの行は無事
   （`accompaniments.py:490-506`）。
3. 挿入後は必ず `GET /api/v1/accompaniments?iso_year=2026&iso_week=36` か盤面の 👥 バッジで確認。

### 回避策 2（今週向きではない）— `PUT /accompaniment-defaults` を API 直叩き

このエンドポイントには重複検査が無い（§2-4）ので通る。しかし

- 既定は **1 曜日 1 コース**（`uq_accd_staff_weekday`）で、コース**テンプレート**指定。
- 既定が週リンクになるのは `generate-week-only` / `assign-staff-only` / 固定枠に戻す の実行時だけ。
  **W36 は既に組み上がっており、再生成は盤面を壊す**。

→ **今週には使えない**。ただし **9/8週以降**を毎週自動で同行させたいなら、
週生成の**前に**この既定を入れておくのが正しい運用（恒久設定）。UI が無いため
Swagger / curl で `PUT /api/v1/accompaniment-defaults`（admin JWT）を叩く:

```json
{ "staff_id": "<小西さんのUUID>",
  "items": [ {"weekday":0,"course_template_id":"<熊澤さんの月コーステンプレ>"},
             {"weekday":1,"course_template_id":"..."},
             {"weekday":2,"course_template_id":"..."},
             {"weekday":3,"course_template_id":"..."},
             {"weekday":4,"course_template_id":"..."} ] }
```

（全置換 PUT。既存の既定は消える。現在 `accompaniment_defaults` は空なので影響なし）

### 回避策 3（最も安全だが不完全）— 重ならない訪問だけ患者単位で選ぶ

同行モードの「患者単位」で、重なっていない訪問だけをクリックして確定する。
コード変更もDB操作も不要だが、**重なっている時間帯だけ同行が付かない**＝
カイポケの職員名2 もその訪問だけ抜ける。PO 要望（終日随行）を満たさない。

### 回避策 4（非推奨）— `PUT /accompaniments` を curl で直接叩く

**効かない。** BE も同じ検査を持つため 422 になる（§1-B）。FE を回避しても意味がない。

### 6-5. カイポケ 9/1〜9/5 の反映手順（回避策 1 の後）

1. リンク投入 → 盤面で 👥小西 バッジが月〜金に出ることを確認
2. 連携ページで **9/1週の差分（週次反映）** を実行
3. Correction に `action='edit'` / `staff2_to='小西 彩稀'` が並ぶことを目視
   （`local_diff.py:326-327` が `staff2_from` / `staff2_to` を出す）
4. RPA apply（`edit` は `rpa_capability` のサービス内容ゲート対象外なので素通し）
5. **例外**: 2名体制（secondary あり）の訪問では小西さんが職員名3 に落ち、週次に載らない。
   該当があればカイポケ画面で手入力する。事前に「熊澤さんの W36 訪問のうち `secondary_staff_id IS NOT NULL`
   または `visit_group_id IS NOT NULL` の件数」を確認しておくとよい。

---

## 付録: 主要な参照位置一覧

| 内容 | 位置 |
|---|---|
| FE 確定ブロック | `frontend/components/schedule/timeline/accompaniment/useAccompanimentController.ts:511-518` |
| FE ボタン無効化・ツールチップ | `frontend/components/schedule/timeline/accompaniment/AccompanimentBar.tsx:150-165` |
| FE 重なり判定 | `frontend/lib/scheduling/accompanimentOverlap.ts:63-125` |
| FE 判定入力（キャンセル未除外） | `frontend/components/schedule/v2/CourseDayTablePanel.tsx:1392-1472, 4837-4855` |
| FE 入口ボタン | `frontend/components/schedule/v2/CourseDayTablePanel.tsx:5600-5621` |
| FE 422 文言 | `frontend/lib/schemas/trainee_accompaniment.ts:254-270` |
| BE PUT 検証順 | `backend/app/api/v1/accompaniments.py:379-466` |
| BE 重なり定義 | `backend/app/services/accompaniment.py:582-643` |
| BE 衝突収集（2系統） | `backend/app/services/accompaniment.py:703-766` |
| BE 422 detail | `backend/app/services/accompaniment.py:645-683` |
| BE 既定展開（検査なし） | `backend/app/services/accompaniment.py:116-207` / `api/v1/schedule.py:1754, 1939` |
| モデル・UNIQUE 制約 | `backend/app/models/accompaniment.py:108-194` |
| カイポケ職員名2 配分 | `backend/app/services/kaipoke/csv_builder.py:295-395` |
| 週次 2 枠制限 | `backend/app/services/kaipoke/local_diff.py:14-18` |
| staff2 差分検出 | `backend/app/services/diff/engine.py:99` |
| RPA ゲート（edit 素通し） | `backend/app/services/kaipoke/rpa_capability.py:26-31, 63-76` |
| PO 決定（確定ブロック） | `docs/plans/trainee-accompaniment-design.md:30, 38-40, 62-65` |
| PO 決定（一般化 #1/#5/#6/#7/#8） | `docs/plans/general-accompaniment-design.md:24-32` |
| 既存テスト | `backend/tests/test_accompaniments_general.py:282, 336, 368, 403` / `frontend/lib/scheduling/__tests__/accompanimentOverlap.test.ts` |
