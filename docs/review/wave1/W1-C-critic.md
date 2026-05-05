# W1-C 拠点マスタ + Cities seed — Critic Review

**Reviewer**: oh-my-claudecode:critic (Opus, ADVERSARIAL モード)
**Commit**: `67ce00b feat: W1-C office master CRUD + Japan cities seed`
**Date**: 2026-05-05

## VERDICT: REVISE

## Critical Findings

### C1. Frontend が送る `prefecture` / `allowed_cities` / `code` を backend が黙殺する (サイレントデータ消失)
- **Confidence**: HIGH
- **Evidence**:
  - `backend/app/schemas/office.py:11-37` の `OfficeBase` には `name / address / lat / lng / note` のみ。`prefecture`, `code`, `allowed_cities` の定義なし
  - `backend/app/api/v1/offices.py:85` `Office(**payload.model_dump())` は Pydantic V2 のデフォルト `extra="ignore"` のため **422 すら返らない**
  - frontend `OfficeForm.tsx:59-68` は `code`, `prefecture`, `allowed_cities` を送信
  - DB 側 M2M は `OfficeCity` 経由だが API では一切書かれない
- **Why this matters**: 「拠点コード」「都道府県」「担当エリア」を入力して保存しても、サーバーは 201 を返すが値はどこにも保存されない
- **Fix**:
  1. `OfficeBase` に `prefecture: str | None`, `code: str | None`, `OfficeRead/Create/Update` に `allowed_cities: list[UUID] = []` 追加
  2. `models/office.py` に `prefecture`, `code` カラム追加 + Alembic 新リビジョン
  3. `create_office` / `update_office` で `payload.allowed_cities` を `OfficeCity` 経由で M2M 書込
  4. `selectinload(Office.cities)` で eager 取得、`OfficeRead.allowed_cities = [oc.city_id for oc in office.cities]`
  5. `extra="forbid"` 化で同種の沈黙バグを遮断

## Major Findings

### M1. `OfficeForm.tsx:142` `cities.slice(0, 200)` が選択漏れを誘発
- **Evidence**: useCities で 393 件返るが top 200 のみ表示、201 番目以降の選択済み city が編集モードで視認不可
- **Fix**: 「選択済み市」を別セクションで先頭表示 + 未選択候補の最初の 200 件

### M2. `seed_cities.py` の dry-run が「DB 空でも skipped が rows 数」になる可能性
- **Evidence**: `seed_cities.py:66` で jis_code が None の行をスキップカウントしてしまう
- **Fix**: `(prefecture, name)` の正規化キーで重複判定する補助セット

### M3. `OfficeBase` に duplicate name/code に対する 409 制約がない
- **Evidence**: `models/office.py:33-40` に UNIQUE なし、IntegrityError 経路が空打ち
- **Fix**: `code` UNIQUE、`name` は active のみ UNIQUE、または UI 側で重複警告

### M4. `useCities` の limit=500 ハードコード、`useOffices` も `params.search` を server に未送信
- **Evidence**: client-side filter のみ、Phase 5 で 1700 件投入時に破綻
- **Fix**: backend に `q` クエリ追加、limit 上限を 2000 に

## Minor Findings

- `OfficeForm.tsx:30-31` lat/lng の `isNaN` チェック無し (実害は `<Input type="number">` が大半弾く)
- `offices/[id]/page.tsx:18` で詳細表示毎に 393 件取得は冗長
- `offices/page.tsx:21` クライアントページング (M4 と同根)
- `seed_cities.py:99` の `asyncio.run` ネスト呼び出し時の脆弱性
- `schemas/office.ts:9` の TODO コメントが「Phase 3-14 で expose」と書くが、実際は Create/Update 段階で破棄されている事実を反映していない

## What's Missing

- **Migration**: `prefecture` / `code` カラム追加の Alembic ファイル
- **テスト**: `tests/api/test_offices.py` で「allowed_cities が round-trip する」テスト
- **rollback 戦略**: M2M 同時編集の楽観ロック / If-Match
- **国際化**: 「世田谷」/「世田ヶ谷」表記揺れ未対応
- **権限**: Sidebar に「拠点」追加されたが staff role への露出判定未実装

## Verdict Justification

C1 が単独で REJECT 相当だが、修正方針が明確 (5ファイル 改修) で frontend は概ね整合的なので REVISE。
ACCEPT 昇格条件:
1. `OfficeBase` に `prefecture/code/allowed_cities` 追加 + 往復テスト
2. M2M Create/Update で書込 + Alembic
3. `extra="forbid"` 化
4. M1 (選択済み city 固定表示) UI 修正

## Open Questions

- `prefecture` 自由記述 vs cities テーブル候補のみ許容
- `numericLike` の Decimal シリアライズ確認
- Sidebar 権限ゲートの将来計画
