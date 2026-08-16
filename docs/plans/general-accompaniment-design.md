# 同行割付の一般化（新人以外のスタッフの同行）— 設計書

作成: 2026-08-17（設計確定・未実装）
ステータス: **ユーザー確定 8 論点済み（2026-08-17）・実装前**

前提となる既存設計: `trainee-accompaniment-design.md`（新人同行=本機能の基盤・visits に書かない原則）、
`two-staff-pairing-design.md`（2名体制との境界）、`patient-ng-staff-design.md`（422確認フローの型）。
調査レポート: 本設計の §1〜§2 は 2026-08-17 のコード全数調査（参照8ヘルパー・呼出約15箇所の file:line 特定済み）に基づく。

---

## 0. 要求（ユーザー原文旨・2026-08-17）

- 新人同行と同じ仕組みで、**新人以外の一般スタッフも**裏側で作業者主導の同行割付をしたい。
- 紐付け単位は「**そのコース全部**」または「**患者様ごと**」を選べること。
- 背景: カイポケ取込で「カイポケ上は2名同行・楽スケ上は1人」の実例あり（職員名2）。

## 1. 確定事項（2026-08-17 ユーザー決定 8 論点）

| # | 論点 | 決定 |
|---|---|---|
| 1 | 本人担当との時間重複 | **同行登録時はハード422**（同住所は免除）。ただし**拒否理由を明確に表示**する — 「◯月◯日(◯) HH:MM は ◯◯様（◯◯コース・ご自身の担当）と重なるため登録できません」の粒度。**逆順**（同行登録後にエンジン/手動がコースを割付）は**警告+管理者通知**（エンジンのハード対応は別案件） |
| 2 | コース単位の意味 | **曜日単位（「何曜日の何コース」）のみ。週一括ボタンは作らない**。患者様1名ずつ（訪問単位）の紐付けも可。両者の混在は従来どおり自由 |
| 3 | 期間指定 | **不要**。現行踏襲（毎週の既定=無期限+週ごとの個別編集） |
| 4 | NG/性別制約 | **422確認フローで適用**（抵触時「それでも登録しますか?」→ack再送で通過→管理者通知。`patient-ng-staff-design.md` §7-2 準拠）。新人同行にも同様に適用 |
| 5 | 複数同行者 | **1訪問に2名以上を認める**。表示（バッジ/モバイル/モニター/詳細）を複数名対応へ改修（現行の「代表1名 last-wins」不具合も同時解消） |
| 6 | カイポケ月次CSV | **一般同行も職員名2に出力**（新人と同格）。職員名2/3 の割当は決定的順序（secondary → 同行[kind問わず staff名昇順] → mentor・staff_idデデュープ維持） |
| 7 | 2人目充足 | **充足に数える**（新人と同格・複数名対応フラグON患者の②カードが同行で消える）。※介護報酬上の扱いは PO へ事後確認を推奨 |
| 8 | 稼働カウント | **当面は現行どおり無影響**（同行しても受入枠・自動割付の余力から控除しない）。実運用で問題が見えたら控除対応を別案件で検討 |

## 2. データモデル（migration 0072）

方針 = **既存 `trainee_accompaniments` の汎化（改名+種別列）**。参照が `services/trainee_accompaniment.py` の
8ヘルパーに完全に閉じていること（ORM直接参照ゼロ）を確認済みのため、影響は実質4ファイル+FEスキーマ。

```sql
-- 0072_generalize_accompaniments
ALTER TABLE trainee_accompaniments RENAME TO accompaniments;
ALTER TABLE accompaniments RENAME COLUMN trainee_staff_id TO accompanying_staff_id;
ALTER TABLE accompaniments ADD COLUMN kind VARCHAR(8) NOT NULL DEFAULT 'trainee'
  CHECK (kind IN ('trainee','support'));   -- 既存行は trainee のまま

ALTER TABLE trainee_accompaniment_defaults RENAME TO accompaniment_defaults;
ALTER TABLE accompaniment_defaults RENAME COLUMN trainee_staff_id TO accompanying_staff_id;
ALTER TABLE accompaniment_defaults ADD COLUMN kind ...同上;
-- 制約/インデックス名も *_acc_* へ rename
```

- **既定（defaults）はコース単位のままでよい**（決定#2: 週一括不要・期間不要のため患者単位の既定は作らない。
  患者ごとの紐付けは週の実効リンク `target_type='visit'` で表現=現行モデルそのまま）。
- `kind` は保存時にサーバが自動判定（`staff.is_trainee` → trainee / それ以外 → support）。
  API入力では受け取らない（詐称防止・判定一元化）。
- 既存 UNIQUE（(staff, course) / (staff, visit)）は不変。複数同行=別スタッフの複数行（決定#5・現行モデルで可能）。

## 3. BE 変更

1. **ゲート緩和**: `api/v1/trainee_accompaniments.py` の `_require_is_trainee`(82-88) →
   `_require_accompaniment_eligible`（active かつ未削除なら可・kind自動判定）。
2. **重複検査の拡張（決定#1・最重要）**: `services/trainee_accompaniment.py` に
   `load_own_duty_visits(staff_id, 週範囲)`（primary/secondary/VSA を含む本人担当）を新設し、
   PUT の検査入力へ合流。**422 detail は構造化**（date/weekday/time/patient_name/course_label/
   reason='own_duty_overlap'）し、FE が「◯◯様（◯◯コース・ご自身の担当）と重なるため」を表示。
   同住所免除（`_same_address_key`）は維持。
3. **NG/性別の422確認フロー（決定#4）**: PUT に `constraint_override_notify.py` の芯で検査を追加。
   `code=constraint_confirmation_required` → FE確認 → `acknowledge_constraint_warnings:true` 再送で
   通過+管理者通知（既存の面展開パターンと完全同型・`useConstraintConfirmRetry` 再利用）。
4. **逆方向の警告（決定#1後段）**: `api/v1/courses.py` PATCH（新人422の直後）と
   `api/v1/schedule.py` apply-staff-review に「同日に同行リンクあり」の warning を追加+管理者通知。
   Layer3 本体は触らない（`assign-staff-only` 完了後の展開直後に衝突検出→通知のみ）。
5. **カイポケ（決定#6）**: `kaipoke/inbound.py` / `replace_inbound.py` の staff2 3段階判定で、
   判定①（既存同行リンク一致→取り込まない）の対象集合を「同行リンク保持者全員」へ汎化
   （`trainee_ids` 集合 → リンクベース。ヘルパーは既に membership 方式のため実質改名）。
   **判定②（リンク無しの自動同行化）は is_trainee 限定を維持**（一般スタッフの staff2 は従来どおり
   ③=2名体制へ。一般の自動同行化は誤爆リスクが高いため意図的に見送り）。
   `csv_builder.py` は職員名2/3 の決定的順序化（決定#6）。
6. **複数名の解決（決定#5）**: `resolve_accompaniment_by_visit` の last-wins を全件返却
   （`list`）へ。`VisitRead.accompaniment` は後方互換のため単数を残しつつ `accompaniments[]` を追加
   （FE移行後に単数を deprecate）。
7. **ライフサイクル**: `DELETE /future` のトリガを is_trainee OFF に加えて **status 非active化/退職**
   にも拡張。`course-guard` は kind='trainee' 時のみ。
8. **API パスは `/accompaniments` へ**（旧 `/trainee-accompaniments` は互換エイリアスとして残し
   FE移行後に削除）。

## 4. FE 変更

- **スタッフセレクタ一般化**: `CourseDayTablePanel.tsx:3161` の is_trainee フィルタを撤廃し
  active 全スタッフ。新人は先頭グルーピング+「新人」バッジ。モード名は「👥同行」へ。
- **二択の入力補助**: 下部バー（`AccompanimentBar`）に「コース(曜日)単位/患者単位」の
  対象フィルタ（armed target の絞り込み。既存リンクの混在は破壊しない=決定#2）。
- **重複422の理由表示**（決定#1）: 構造化detailから「なぜできないか」を明示するダイアログ/トースト。
- **NG/性別の確認フロー**（決定#4）: `useConstraintConfirmRetry` + `ConstraintOverrideConfirmDialog` を配線。
- **複数名表示**（決定#5）: `types.ts` の visitBadgeName/courseBadgeName を配列化。
  `MobileVisitCard` / モニター(`accompaniment_staff_name`) / スタッフ詳細サマリも複数名対応。
- **解除導線**: スタッフ詳細サマリ（表示条件を「同行リンクがあれば」へ変更）に週リンクの個別解除。
- **プール充足**: `accompanimentFulfillment.ts` は kind を見ず全リンク充足（決定#7・現行ロジック維持）。

## 5. 触らないもの（意図的スコープ外）

- **エンジン本体**（Layer3/auto_allocator が同行を制約・稼働控除に載せる対応）— 決定#1後段/#8 により
  警告+通知で出荷し、実運用の衝突頻度を見て別案件化。
- 患者単位の「毎週の既定」（決定#2により不要）。期間指定（決定#3）。
- カイポケ逆取込 判定②の一般開放（§3-5 のとおり意図的見送り）。

## 6. 既知の割り切り・注意（現場アナウンス事項）

- 「固定枠に戻す」で visit 個別リンクは消える（既存仕様）。患者単位利用が増えると遭遇頻度が上がる。
- 週次カイポケ反映は2枠まで（2名体制+同行の3人目は週次Correctionに載らない・既存制限）。
- 介護報酬上の「同行=2人目充足」の妥当性は PO へ確認（決定#7の注記）。

## 7. 実装フェーズ

- **Phase A**: mig 0072 + モデル/サービス/API/スキーマ改名 + ゲート緩和 + kind自動判定
- **Phase B**: 重複検査拡張（own_duty+構造化422）+ NG/性別422確認フロー + 逆方向警告+通知
- **Phase C**: FE一式（セレクタ/二択フィルタ/理由表示/確認フロー/複数名バッジ/解除導線）
- **Phase D**: カイポケ（判定①汎化・CSV決定的順序・replace追随）+ 複数名解決のBE/FE貫通
- **Phase E**: 統合検証+最終レビュー（デプロイは mig 込み手順: build --no-cache + alembic手動）
