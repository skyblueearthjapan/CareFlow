# INV-4 VPS稼働検証 クロスレビュー B（受入観点）

## Verdict
**Status**: INCOMPLETE
**Confidence**: high
**Blockers**: 4
**Recommendation**: REQUEST_CHANGES

## 受入基準評価

| # | 観点 | Status |
|---|---|---|
| 1 | CareLink着手前の必須解決事項（Go/No-Goゲート）が明示 | PARTIAL |
| 2 | プランアップグレード or 別VPSの判断基準が定量化 | MISSING |
| 3 | cloudflared ingress追加手順の検証可能性 | PARTIAL |
| 4 | RPA復活確認のチェックリスト（手順レベル） | MISSING |
| 5 | Swap未設定のリスクと対処の定量評価 | PARTIAL |
| 6 | Docker disk回収の実行可能性 | PARTIAL |
| 7 | line-webhook(Exited) コンテナへの対処判断 | MISSING |

## Gaps

**Gap 1: Go/No-Go ゲートの未定義（HIGH）**
- §7 に7項目あるが「未完了なら着手禁止」の明示分類なし
- CPU超過 (2.2 > 2.0 vCPU) は明らかに Hard Blocker だが明記されていない
- 提案: Hard Blocker / Pre-condition / Recommended の3層分類

**Gap 2: VPS判断基準の欠落（HIGH）**
- 「Hostinger アップグレード or 別VPS」のみ記載
- コスト比較、契約条件、SLA、移行コストの意思決定ツリー皆無
- 提案: KVM2→KVM4 のコスト差、別VPS（Hetzner CX22等）との比較表

**Gap 3: cloudflared 手順の不在（MEDIUM）**
- config.yml パスと現行3エントリは特定済
- ingress 行追加→reload→動作確認の具体コマンド列なし
- 提案: YAMLスニペット例 + `sudo systemctl reload cloudflared` + `cloudflared tunnel info`

**Gap 4: RPA復活チェックリスト不在（HIGH）**
- §4 で停止状態は証明されている、復活手順は §7-7 の1行のみ
- state.json 修正、kaipoke-api テスト投入、artifacts/ 新規 PNG 確認等が皆無
- 提案: `POST /api/run-rpa` 等のエンドポイント呼び出し + 3ステップチェックリスト

**Gap 5: Swap OOM シナリオ未評価（MEDIUM）**
- 「OOM リスク高、swapfile 推奨」のみ
- ピーク時メモリスパイク（Playwright起動時等）の定量評価なし
- 提案: `/proc/meminfo` MemLow + `docker stats --no-stream` 計測、4GB swapfile で十分か確認

## 受入チェックリスト

| # | 項目 | 必須度 |
|---|---|---|
| 1 | CPU 超過を Hard Blocker と明記、判定可能 | MUST |
| 2 | Hostinger プラン or 別VPSの定量比較表 | MUST |
| 3 | cloudflared YAMLスニペット + reload コマンド明示 | MUST |
| 4 | RPA復活の3ステップチェックリスト（API投入→artifacts確認→state.json検証） | MUST |
| 5 | Swap OOM シナリオの定量評価とswapfile サイズ算出 | MUST |
| 6 | Docker GC の安全確認手順（稼働コンテナ依存チェック） | SHOULD |
| 7 | line-webhook 削除可否判断 + nginx 設定整理 | SHOULD |
| 8 | dev 環境並走の現実性（CPU 2vCPU 制約下） | SHOULD |
| 9 | 定期バックアップ戦略（pg_dump + 別ストレージ転送） | MUST |
| 10 | kaipoke-net 不在を踏まえた Docker network 再設計 | MUST |
