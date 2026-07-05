# K-1 設計書：CareFlow DB → カイポケ18列CSV 生成（差分適用の心臓部）

作成 2026-07-05 / 前提: `docs/plans/kaipoke-rpa-revival-survey.md`（K-0/K-0b 完了・ジョブセンター本番稼働）
根拠データ: 2026-07 実カイポケCSV 577行（事業所「訪問看護ステーションよりより」= 精神科訪問看護）

---

## 0. 結論 — ギャップは想定より遥かに小さい

実CSVの分析で、この事業所の18列CSVは**極めて均質**と判明した:
- **サービス内容 = 「精神基本療養費Ⅰ・正看」の1値のみ**（577行全部。主担当が准看護師の115行でも同一 →
  実資格と連動しない実質定数）
- **業務種別 = 「医療保険」のみ**
- **職種 = 看護師 / 准看護師 の2値のみ**
- **事業所名 = 本店 / 都賀支店 の2つ**
- 備考は全行空・提供時間は35分中心・2名体制は103行（うち同行○=65）

→ 当初想定の「サービス内容マスタ」「介護職種マスタ」は**不要**。以下の**最小追加**で18列を生成できる。

---

## 1. 18列マッピング（実データ根拠・列ごとに源泉と対応）

| # | 列 | 実値 | CareFlow 源泉 | 状態 |
|---|---|---|---|---|
| 1 | 職員名1 | 宇田川　優莉 | `visit.primary_staff → staff.name` | ✅（表記整形のみ） |
| 2 | 職種1 | 看護師/准看護師 | **staff.qualification（新規）** | 🔨 |
| 3 | 職員名2 | | `visit.secondary_staff → staff.name` | ✅ |
| 4 | 職種2 | 看護師 | staff.qualification | 🔨 |
| 5 | 同行2 | ○ | `required_staff_count==2 / visit_group / mentor` から導出 | ✅ |
| 6-8 | 職員名3/職種3/同行3 | （3人目はこの月なし） | 同上（mentor 等） | ✅ |
| 9 | 事業所名 | 訪問看護ステーションよりより / (ST)…都賀支店 | **office.kaipoke_name（新規）** | 🔨 |
| 10 | 日付 | 1 | `visit.visit_date.day` | ✅ |
| 11 | 曜日 | 水 | visit_date → 曜日 | ✅ |
| 12 | 利用者 | 朝倉　美夢 | `visit.patient → patient.name` | ✅（表記整形） |
| 13 | 業務種別 | 医療保険 | `patient.insurance`（medical→医療保険） | ✅（導出） |
| 14 | サービス内容 | 精神基本療養費Ⅰ・正看 | **patient.kaipoke_service_content（新規・既定=定数）** | 🔨 |
| 15 | 開始時間 | 10:00 | `visit.start_time` | ✅ |
| 16 | 終了時間 | 10:35 | `visit.end_time` | ✅ |
| 17 | 提供時間（分） | 35 | end - start | ✅ |
| 18 | 備考 | （空） | 未使用（`visit.note` を将来使うなら） | ✅ |

**新規に必要なのは3カラムのみ** + 氏名正規化ユーティリティ + 生成サービス本体。

---

## 2. 追加するデータ（最小）

### 2-1. `staff.qualification`（職種）— migration
- 型: `String(16)` nullable / 値: `看護師` / `准看護師`（将来 `理学療法士` `作業療法士` `言語聴覚士` 拡張）
- 用途: 職種1/2/3 列。**サービス内容とは独立**（サービス内容は実データ上、実資格と連動しない定数のため）
- 初期投入: 実CSVの「職員名1×職種1」対応から逆引きして seed（下記 §4 名寄せで突合）

### 2-2. `office.kaipoke_name`（正式事業所名）— migration
- 型: `String(120)` nullable / 値: INAGE→「訪問看護ステーションよりより」, TSUGA→「(ST)訪問看護ステーションよりより　都賀支店」
- 現状 office.name は短縮名「稲毛」「都賀」。CSV は正式名が必須
- 代替案: config の code→正式名マップでも可（DBを汚さない）。ただし拠点増減に追従するなら DB カラムが素直

### 2-3. `patient.kaipoke_service_content`（サービス内容）— migration
- 型: `String(64)` nullable / 既定（NULL時のフォールバック）= 事業所既定「精神基本療養費Ⅰ・正看」
- **設計判断（PO確認）**: サービス内容は実データ上ほぼ定数。以下A/Bから選ぶ:
  - **A案（推奨・薄い）**: patient 単位の文字列として保持。既定は事業所定数。介護保険/別療養費の患者だけ個別設定。
    診療報酬計算（療養費区分Ⅰ/Ⅱ/Ⅲ の同一建物同一日判定）は**当面やらない**（実データに Ⅱ/Ⅲ 皆無）
  - **B案（厚い）**: 精神フラグ＋療養費区分カラムを patient に足し、サービス内容を合成関数で算出。将来 Ⅱ/Ⅲ や
    複数事業所形態に耐えるが、今は過剰
- ※業務種別（医療保険/介護保険）は `patient.insurance` から導出。新規不要

### 2-4. カイポケ側 ID 紐付け — 当面**不要**（名寄せで代替）
- staff/patient に kaipoke_id を足す案もあるが、実運用は**氏名一致で突合**していた（旧GAS も名前→IDマップ方式）。
  まず §4 の正規化名寄せで対応し、突合不能の少数だけ後から override カラム（`kaipoke_name`）を検討

---

## 3. CSV生成サービス（新規本体）

`backend/app/services/kaipoke/csv_builder.py`（新規）:
- 入力: 対象月・対象拠点（＋任意で対象患者/週）
- 処理: 該当 `visits` を取得 → 患者/スタッフ/拠点を解決 → 18列 dataclass（`diff/engine.py` の `ScheduleEntry`
  を再利用/整合）へマップ → cp932 で1行ずつ
- 2名体制: `visit_group_id` で束ね、主担当=職員名1、副=職員名2（同行○）、mentor=職員名3
- 出力: 差分エンジン（`diff/engine.py`）の「最適化CSV」入力として渡せる形（＝現状 kaipoke-api 側が持つ
  最適化CSVを CareFlow 由来に置換）。これで**差分の正が CareFlow visits に一本化**される
- エンコーディング/フォーマットは `diff/engine.py` の read/parse を鏡として実装（列順・cp932）

**これが「差分適用の心臓部」**: 現状 `/api/diff` は kaipoke-api が持つ最適化CSV（旧GAS由来）と突合していたが、
K-2 でこの生成CSVを供給することで、CareFlow が確定したスケジュールをカイポケへ転記する一方向フローが完成する。

---

## 4. 氏名正規化・名寄せ（新規ユーティリティ）

`backend/app/services/kaipoke/name_match.py`（新規）:
- `normalize_name(s)`: NFKC（全半角統一・既存 geocoding/hash.py に前例）＋**漢字異体字マップ**（髙→高・栁→柳・
  﨑→崎 等。旧 PlaywrightTest1 `normalize_name` から移植）＋空白畳み込み
- 突合: CareFlow staff/patient を normalize したキーで辞書化 → カイポケ氏名を normalize して照合
- 実CSVの氏名（例: 宇田川　優莉・髙梨　桂子）を正解として、seed 時に staff.qualification / patient の
  対応を確定
- 突合不能な少数は override（将来 `kaipoke_name` カラム or 対応表 JSON）

---

## 5. 実装 Wave 案（K-1 → K-2）

| Wave | 内容 | 種別 |
|---|---|---|
| **K-1a**（済） | 中継契約修正＋ジョブセンター＋モニタリング | ✅ 本番稼働 |
| **K-1b** | migration: staff.qualification / office.kaipoke_name / patient.kaipoke_service_content（A案） | 基盤 |
| **K-1c** | name_match ユーティリティ＋実CSVからの職種/サービス内容 seed（読み取り専用で検算） | 基盤 |
| **K-1d** | csv_builder（visits→18列）＋単体テスト（実CSVとの一致検算＝ゴールデンテスト） | 心臓部 |
| **K-2** | `/api/diff` の最適化CSV入力を csv_builder 出力へ切替 → 差分プレビューが CareFlow 由来に | 統合 |
| **K-3 Step2** | モニターの iframe 埋込（CF 同一オリジンパス＋Access） | UI（別途） |

**検算の妙手**: 今日エクスポートした実CSV 577行は「カイポケの現況」。csv_builder が CareFlow visits から
生成した同月CSVと `diff/engine.py` で突合し、**差分が最小（＝両者一致）になれば生成ロジックが正しい**と
実データで裏付けできる（ゴールデンテスト）。

---

## 6. PO 確認事項（実装着手の前に）

1. **サービス内容の持ち方**: A案（患者単位の文字列・既定は定数・診療報酬計算しない）で進めてよいか。
   将来 療養費区分Ⅰ/Ⅱ/Ⅲ の自動判定が要るか（現状データには Ⅱ/Ⅲ 皆無）
2. **旧GAS運用の現況**: 4/18 以降 allocate も停止。完全移行済みか（並行稼働なら生成CSVの形式一致を
   より厳密に検証する必要）。カイポケへの apply（書込）を CareFlow から解禁する時期
3. **職種の初期投入**: 実CSVからの逆引き seed でよいか（スタッフ名簿に看護師/准看護師を機械投入し、
   PO が例外だけ補正）

---

## 7. 思想の正典との整合

カイポケ転記は「第4の役割＝事務代行」で患者予定を動かさない（余白の原則と非衝突）。本設計は
CareFlow で確定済みの visits を外部へ写すのみ。apply（書込）解禁時は既存の「プレビュー→明示確認→
実行→適用後検証」パターンと Ctrl+Z 対象外明示を踏襲する（K-1a のジョブセンターに実装済みの型を使う）。
