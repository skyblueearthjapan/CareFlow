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

- **Phase 0 完了(2026-08-23・RPA `4c5303c`)**: `commands/probe_service_options.py` で採取。value は安定コード:
  サービス区分 `01`=訪問看護 / `02`=精神科訪問看護、基本療養費 `01`=基本療養費Ⅰ(一般)・`01`=精神科基本療養費Ⅰ(精神科)、
  職員資格 `01`=看護師等 / `03`=准看護師(`02` は一般=理学療法士等・精神科=作業療法士)。実装は **value 指定**(`resolve_medical_selects` / `_select_value_and_wait`)。
  `commands/dryrun_service_branch.py` で4パターンの選択結果を実画面で確認済み(登録なし)。
- **S3 完了(2026-08-31)**: 本番で准看 add の実機テストを実施 — ①`auto_apply` 本経路の dry-run(橘様 9/3 13:00・登録直前で停止)でダイアログが 精神科訪問看護(02)×精神科基本療養費Ⅰ(01)×**准看護師(03)**+職員1=高岡 で組み立つことをスクリーンショットで確認 → ②本番 apply(熊澤 delete + 高岡・准看 add)→ ③export の CSV 行が `職員名1=高岡 真由美 / 職種1=准看護師 / サービス内容=精神基本療養費Ⅰ・准看` を確認 → ④`/opt/carelink/.env` に `KAIPOKE_RPA_SERVICE_BRANCH_ENABLED=True` を追記し backend 再作成(送信ガード解除)→ ⑤残り 13 件(9/1〜9/5 の高岡さん分)を apply したが **15:32 以降カイポケの登録ダイアログの連動処理(区分→療養費/資格の選択肢展開・日付選択後の時刻欄有効化)が数秒で返らず、RPA の待機(`_select_value_and_wait` = 最大 5 秒 + networkidle 5 秒)を超えて失敗が連鎖**(成功 3 / 失敗 23、追加のみ再試行 1 / 10)。失敗した追加は登録されない(無害)が、最初の「削除→追加」順で削除だけ通った 2 件が欠落。経緯と実状態 = `incident-2026-08-31-kaipoke-expand-wrong-month.md` §7-b。
  **RPA 側の改修候補(未実装)**: 区分選択後は `inPopupEstimate2/3` の option 数 > 1 を最大 30 秒待つ / 日付クリック後は `inPopupStartHour` の enabled を待つ(強制有効化しない)/ どちらも満たさなければ登録せず failed にする(現状も登録は起きないが時間を浪費する)。
  **未テスト**: 「基本療養費Ⅰ・正看/准看」(一般=精神以外)の add。現場に該当患者の add が出た時点で 1 件目を目視確認すること(門は共通なので送信はされる)。
- 登録後検証: 保存後の一覧行のサービス内容が `service_type` と一致するか確認し、不一致は failed(削除検証と同じ作法)。

### 3-1. edit ではサービス内容を修正できない(= 届くのは add だけ)

カイポケの編集ダイアログはサービス内容を触れない。差分側もこれに合わせてあり、
`correction_before_after()` は **before/after 双方に同じ `service_type` を入れる**
(`local_diff.py`)。つまり:

- サービス内容だけが違う行は、edit では表現できず **必ず delete + add** として出る
  (S2 レビュー C1 で差分エンジンの一致判定を「双方向の前方一致」に直し、
  「基本療養費Ⅰ・正看」⊂「精神基本療養費Ⅰ・正看」の偽一致で edit に化ける穴を塞いだ)。
- したがって **RPA が新しいサービス内容を実際に書きに行く経路は add のみ**。
  delete / edit / date_change は既存行を動かすだけなので影響を受けない。

### 3-2. S3 完了まで送信ガード(実装済み・S2 と同時)

S2 でらく助は 4 通りのサービス内容を出すようになったが、RPA は §3 の分岐が入るまで
固定値(精神科訪問看護 × 看護師等)でしか登録できない。この状態で准看/一般の add を
送ると **画面上は成功したまま、カイポケには誤ったサービス内容が入る**(突合しても
差分が消えず、請求も狂う)。そこで S3 が終わるまでは送らない:

| 場所 | ふるまい |
|---|---|
| `POST /integrations/apply` | `action='add'` かつ `after.service_type` が「精神基本療養費Ⅰ・正看」以外の item を送信対象から除外。`job.result_summary.skipped_rpa_unsupported` に件数、`skipped_rpa_unsupported_reason` に理由「RPA が准看/一般の登録に未対応(S3)」。全件が対象外なら 422 |
| `POST /integrations/unsent-summary` | 各 item に `rpa_unsupported` を立て、`rpa_unsupported_count`(過去日とは二重に数えない)を返す。`sendable_count` からも除外 |
| FE `SyncBar` | 「送れる N・RPA未対応 M」と表示。当該行は select で disabled + 注記「（RPAが准看/一般の登録に未対応=カイポケで直接登録）」 |

- 判定の正典は `app/services/kaipoke/rpa_capability.py`(BE 単一ソース)。FE は
  サービス内容の文字列を自前で判定しない — S3 で門を開けたときに片側だけ古い
  ルールで止め続ける事故を防ぐため。
- 門の開閉は設定 `KAIPOKE_RPA_SERVICE_BRANCH_ENABLED`(既定 `False`)。
  **S3 の実機テストが通ったら `True` にする**のが解禁手順。
- `service_type` が空の行(イベント等)は除外しない(運用を丸ごと止めないため)。

## 3-3. PO 確定ルール（2026-08-31 夜・請求区分と職員1の決め方）— **未実装・要対応**
| ケース | 職員1（メイン） | サービス内容 |
|---|---|---|
| 正看 1 名 ＋ 准看 1 名が同じ患者に行く（同行／2 名体制） | **正看** | **正看対応**（精神基本療養費Ⅰ・正看 など） |
| 准看 1 名だけで訪問 | 准看 | 准看対応 |
| 正看 1 名だけ | 正看 | 正看対応 |

現行実装（§2・`resolve_service_content`）は **「職員1の資格」だけ**で正看/准看を決め、同行（職員2/3）は影響しない。
したがって「准看がコース担当（職員1）で、正看が同行」の訪問は 准看対応 で出力されてしまい、ルール 1 に反する。
**必要な変更（案）**: `csv_builder` の行生成で、職員1 が准看護師かつ 同行/secondary に看護師（正看）が居る場合は
**正看を職員名1 へ昇格（准看は職員名2）し、grade を正看にする**。`local_diff` の Correction（staff1/staff2）も同じ配分関数を
通るので週次反映にも効く。テスト = 上表 3 ケース＋「准看×准看」＋「上書きあり」。UI 側は当面「准看コースに正看同行」
を警告表示（止めない）。8 月実績合わせ（`session-2026-08-31-HANDOFF.md` §3）の修正でもこの表で判定すること。

## 4. 警告・運用
- 患者の区分が既定(精神科)のまま「一般」の実態がある場合は突合で差分として現れる → 差分カードに「患者の訪問看護区分を確認してください」のヒント。
- スタッフ資格未設定 / PT・OT・ST の訪問がある場合は送信前に警告(送信は止めない)。
- 例外 3 件(8/21 唐鎌・峯﨑、8/26 植田)はカイポケ側の修正を PO に依頼。

## 5. 実装 Phase
| Phase | 内容 | 規模 |
|---|---|---|
| S1 | mig 0077 `patients.visit_category` + BE schema + 患者編集 UI(区分セレクト・詳細で上書き文字列) + スタッフ編集/新規 UI に資格セレクト + 一覧バッジ | BE 小・FE 中 |
| S2 | `resolve_service_content` + csv_builder 結線 + テスト(4 象限/上書き/未割当/同行) → デプロイ → 再突合で偽差分ゼロ(例外 3 件のみ)を確認 | BE 小 |
| S3 | RPA Phase 0(option 採取・headed dry-run) → 分岐実装 → 1 件テスト(准看 1 件・一般 1 件) → `KAIPOKE_RPA_SERVICE_BRANCH_ENABLED=True` で送信ガード解除(§3-2) | RPA 中 |

S1+S2 は RPA と独立に進められ、突合の精度改善が先に得られる。S3 が終わるまでは
§3-2 の送信ガードが准看/一般の add を止めるので、S2 だけ先に本番へ出しても
カイポケが誤った値で汚れることはない(該当分はカイポケ側で直接登録する運用)。
