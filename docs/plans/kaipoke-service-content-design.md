# カイポケ「サービス内容」自動判定 — マスタ項目と出力分岐の設計 (2026-08-23 案)

調査 = `kaipoke-service-content-investigation.md` / お客様向け資料 = `docs/mockups/kaipoke-service-type-proposal.html`。
前提: PO 確認(対応要否・マスタ運用の可否)後に着手。**規則 = 患者の区分 × 基本療養費Ⅰ × 職員1の資格**。

## 1. マスタ項目

### 1-1. 患者: 訪問看護区分 `patients.visit_category`(新設・mig 0077)
| 値 | 表示 | カイポケのサービス区分 | サービス内容のベース |
|---|---|---|---|
| `psychiatric`(既定) | 精神科 | 精神科訪問看護 | 精神基本療養費Ⅰ |
| `general` | 一般 | 訪問看護 | 基本療養費Ⅰ |

- `String(16) NOT NULL DEFAULT 'psychiatric'`。既存 101 名は既定値で移行し、兼行様・近藤様だけ `general` に(移行後に管理画面から設定・SQL でも可)。
- 既存 `kaipoke_service_content`(String(64)・全員 NULL)は **上書き用(例外時の完全文字列)** として残す。非 NULL ならそのまま出力し、分岐を無視する(将来「Ⅱ」「Ⅲ」等の例外に備える)。UI には「詳細設定」として折りたたみで露出。
- `insurance`(medical/care)は業務種別(医療保険/介護保険)用で現状どおり(全件 医療保険)。

### 1-2. スタッフ: 資格 `staff.qualification`(既存列・UI 新設)
- 値は既存 `QualificationV2` = 看護師 / 准看護師 / 理学療法士 / 作業療法士 / 言語聴覚士。
- **スタッフ編集・新規画面に「資格」セレクトを追加**(現在 FE の v2 スキーマ・フォームに存在しない = 入力手段が無い)。一覧にもバッジ表示。
- 准看護師 → 「・准看」、それ以外(看護師/PT/OT/ST/未設定) → 「・正看」。PT/OT/ST はカイポケ上は別のサービス内容(リハ)になるはずだが現場に該当者が居ないため **今回は対象外・警告のみ**(§4)。
- 未設定は警告(マスタ突合 👥 に「資格未設定 N 名」を追加)。

## 2. 出力分岐(らく助 → カイポケ CSV / 送信差分)

`csv_builder.py` の `service_content` 決定を関数化:
```
def resolve_service_content(patient, primary_staff) -> str:
    if patient.kaipoke_service_content: return patient.kaipoke_service_content   # 上書き
    base = "基本療養費Ⅰ" if patient.visit_category == "general" else "精神基本療養費Ⅰ"
    grade = "准看" if (primary_staff and primary_staff.qualification == "准看護師") else "正看"
    return f"{base}・{grade}"
```
- 職員1 = `visits.primary_staff_id`(未割当 '-' の行は患者ベース + 正看)。同行(職員2)は影響しない(カイポケの実態どおり)。
- 既存テスト `test_kaipoke_csv_builder` に 4 象限 + 上書き + 未割当 を追加。
- 突合(diff engine)はキーにサービス内容を含むため、この修正だけで正看/准看の偽差分が消える(例外 3 件は本当の差分として残る = 正しい)。
- 取込側(inbound)は service_type を保持して visits に入れていないため変更不要。

## 3. RPA(auto_apply.py・新規追加 `fill_medical_insurance_fields`)

`Correction.service_type` を解釈して選択を切り替える:
| service_type | `#inPopupEstimate1`(サービス区分) | `#inPopupEstimate3`(職員資格) |
|---|---|---|
| 精神…・正看 | 「精神科訪問看護」(現状) | 「看護師等」(現状) |
| 精神…・准看 | 「精神科訪問看護」 | **准看護師の option**(文言未確認) |
| 基本療養費Ⅰ・正看 | **一般の訪問看護 option**(文言未確認) | 「看護師等」 |
| 基本療養費Ⅰ・准看 | 同上 | 准看護師 |

- **Phase 0(必須)**: headed dry-run(本番登録なし)で `#inPopupEstimate1/2/3` の option 文言を採取しログ保存。採取後に上表の文言を確定。
- 変更(edit)は現状どおりサービス内容を触らない。必要なら「サービス内容が違う」を edit ではなく delete+add として扱う現行の突合挙動で吸収。
- 登録後検証: 保存後の一覧行のサービス内容が `service_type` と一致するか確認し、不一致は failed(削除検証と同じ作法)。

## 4. 警告・運用
- 患者の区分が既定(精神科)のまま「一般」の実態がある場合は突合で差分として現れる → 差分カードに「患者の訪問看護区分を確認してください」のヒント。
- スタッフ資格未設定 / PT・OT・ST の訪問がある場合は送信前に警告(送信は止めない)。
- 例外 3 件(8/21 唐鎌・峯﨑、8/26 植田)はカイポケ側の修正を PO に依頼。

## 5. 実装 Phase
| Phase | 内容 | 規模 |
|---|---|---|
| S1 | mig 0077 `patients.visit_category` + BE schema + 患者編集 UI(区分セレクト・詳細で上書き文字列) + スタッフ編集/新規 UI に資格セレクト + 一覧バッジ | BE 小・FE 中 |
| S2 | `resolve_service_content` + csv_builder 結線 + テスト(4 象限/上書き/未割当/同行) → デプロイ → 再突合で偽差分ゼロ(例外 3 件のみ)を確認 | BE 小 |
| S3 | RPA Phase 0(option 採取・headed dry-run) → 分岐実装 → 1 件テスト(准看 1 件・一般 1 件) → 運用解禁 | RPA 中 |

S1+S2 は RPA と独立に進められ、突合の精度改善が先に得られる。
