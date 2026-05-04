# CareLink 既存システム再調査 統合報告書（マネージャー総括）

**作成日**: 2026-05-05
**作成者**: ディレクター・マネージャー（Claude）
**入力資料**:
- 5調査レポート（INV-1〜INV-5）
- 10クロスレビュー（critic A × 5 + verifier B × 5）
- Codex コードレビュー（allocation_engine + diff_engine 詳細）

**ファイル位置**: `C:\Users\imaizumi.LINEWORKS-NET\Documents\CareLink\docs\audit\`

---

## 0. エグゼクティブサマリ

5ドメイン並列調査と16名（10レビュアー + Codex + 5実装調査）のクロスチェックを通じ、**現行システム（GAS + Python/Playwright + VPS）のコード品質と前提条件に複数のクリティカル問題**が確認された。

### 最重要発見（5件）

1. **AllocationEngine の状態管理に致命的な不整合** — `_unregister_assignment()` が `pid_date_staff`/`last_assigned_by_patient` を戻さない、`_fix_cross_patient_overlaps()` と `_final_overlap_sweep()` が staff_id を消すだけで unregister しない → **stale index で空き枠を誤って塞ぐ**（Codex 発見）

2. **必須指定/同じ人希望経路で制約迂回** — 性別・soft_cap・同一患者同日回避を **`_is_staff_available()` のみ**で判定して通常の制約チェックを bypass する（Codex 発見）

3. **VPS の前提と実態に深刻な乖離** — `kaipoke-net` 不在、cloudflared は systemd ネイティブ（コンテナでもDashboardでもない）、VPS は 2 vCPU、RPA は2ヶ月停止状態

4. **データ移行戦略の列定義に欠落多数** — 監査リスト 20列は **5-6列欠落**（住所/性別/保険区分/訪問頻度/訪問週/備考）、~18 の active sheets が scope 外、`住所` 列欠落で **geocoding 完全に崩壊**

5. **`diff_engine` の substring matching に致命バグ** — `"" in service_type` が真になるため**サービス名空欄時に誤マッチ**、`target_week_start/end` が `int(entry.date)` のため**月跨ぎで落ちる**（Codex 発見）

### 全体判定

**現状のままでは CareLink 着手不可。**
- Phase 0（事前調査スパイク + 契約凍結）に**追加2週間**が必要
- 既存コードのバグ修正・テスト整備を先行 or 並行で進めない限り、CareLink への部分移植（案B）は **本番投入時に既存バグも一緒に持ち越す**リスク

---

## 1. 5調査結果サマリ

### INV-1: GAS App コード監査（careflow-scheduler）
- 行番号引用38箇所が**全て実コードと一致**（信頼性高）
- スプレッドシート構造、API 14エンドポイント、Python ペイロード10種類を完全把握
- **問題**: AuditExpected.js / AuditJudge.js（業務ルール）の分析欠落、認証不在の安全性評価未対応、D4計画とのクロスリファレンス不足
- レビュー判定: **ACCEPT-WITH-RESERVATIONS**（critic）/ **REQUEST_CHANGES**（verifier）
- verifier 指摘 ブロッカー: 旧VPS API↔CareLink新API対応表欠落、受入基準セクション不在

### INV-2: Python/Playwright API 監査
- supervisord構成、Flask 全エンドポイント、Playwright 自動操作、noVNC を網羅
- **問題**: line range 数件誤り（203-1080→実際203-1408）、`edit_schedule_santei()` 等の存在しない関数記載、x11vnc -nopw のセキュリティリスク評価なし、CORS `*` 危険性未指摘
- レビュー判定: **ACCEPT-WITH-RESERVATIONS**（critic）/ **REQUEST_CHANGES**（verifier）
- verifier 指摘 ブロッカー: CSP frame-ancestors 未追加、CareLink Backend 未着手で結合検証不可

### INV-3: 自動算出ロジック深掘り
- 8段階パイプラインの記述ベース、10入力モデル、3パスマッチング解説
- **問題（critic指摘）**: §4.5 の `areas` コードが**捏造**（実コードにない）、5 ordering patterns の3つが誤記、「8段階」だが実は 13段階、Step 0.1-0.3 preprocessing が pipeline 表に未記載
- **問題（verifier指摘）**: pytest 0件（テスト皆無）、areas バグ実コードで再現確認、JSON スキーマファイル不在
- レビュー判定: **REVISE**（両方）

### INV-4: VPS 実稼働検証
- SSH で実測値を取得（CPU 2 vCPU、Memory 7.8GB、Disk 100GB の 68% 使用、Uptime 60日、RPA 2ヶ月停止状態）
- **kaipoke-net 不在を発見**、cloudflared は systemd ネイティブと判明、Build cache 40.7GB 回収可
- **問題（critic指摘）**: CPU「同居不可」結論は**過度に断定的**（Docker cpus は CFS soft limit、ハードリザベーションではない）、D5 計画との衝突分析が不完全
- **問題（verifier指摘）**: Go/No-Go ゲート定義なし、プラン UPGRADE vs 別 VPS の判断基準未数値化、RPA 復活チェックリスト不在
- レビュー判定: **ACCEPT-WITH-RESERVATIONS**（critic）/ **REQUEST_CHANGES**（verifier）

### INV-5: データ活用・反映戦略
- 4案（CSV/Sheets API/Excel/GAS Push）比較、A+B（CSV初期+Sheets継続）推奨
- **問題（critic指摘）**: 患者列が **20列ではなく実際 25-26列**、住所列など重要列5-6個欠落、CareLink 16列も実際 22列、~18 active sheets が migration scope から完全に除外、weekly_pattern JSONB 変換仕様 0、Sheets API quota 不正確
- **問題（verifier指摘）**: weekly_pattern 変換仕様欠如、Phase 0/1/2 ゲート定量基準なし、CSV import のトランザクション境界未定義
- レビュー判定: **REVISE**（両方）

---

## 2. クリティカル発見の優先順位（全レビュー横断）

### Tier 1: 既存コードの致命バグ（Codex + INV-3 verifier）

| ID | 内容 | 影響 | 優先度 |
|---|---|---|---|
| C-1 | `_unregister_assignment()` が pid_date_staff / last_assigned_by_patient を戻さない | stale index で空き枠誤塞ぎ | **CRITICAL** |
| C-2 | `_fix_cross_patient_overlaps()` / `_final_overlap_sweep()` が staff_id を消すだけで unregister しない | 同上 | **CRITICAL** |
| C-3 | 必須指定/同じ人希望経路で性別・soft_cap・同一患者同日チェックを迂回 | 制約違反による誤割当 | **CRITICAL** |
| C-4 | 多試行 best 復元時に `assign_count/staff_day_visits/pid_date_staff` を復元しない | best 結果の整合性破壊 | **CRITICAL** |
| C-5 | `_sync_coupled_times()` 同期失敗時に warning だけで時刻不一致のまま残す | 2名体制 hard constraint が弱い | **HIGH** |
| C-6 | mentor_pair が事後追加のみ（勤務曜日/休み/partial block/max_per_day 未確認） | 同行訪問の不正配置 | **HIGH** |
| C-7 | `Staff.areas` フィールド未使用（拠点制限が完全に無効化） | 拠点跨ぎの誤割当 | **HIGH** |
| C-8 | `_add_missing_coupled_entries()` 定義のみ呼び出しなし（dead code） | コード理解阻害 | LOW |
| C-9 | diff_engine `"" in service_type` 危険な substring matching | サービス名空時の誤マッチ | **HIGH** |
| C-10 | diff_engine `target_week_start/end` 月跨ぎで `int(entry.date)` が落ちる | 月跨ぎ週の差分検出失敗 | **HIGH** |
| C-11 | diff_engine 側に異体字正規化なし（auto_apply 側のみ） | 正規化不一致による誤マッチ | **MEDIUM** |
| C-12 | EjectionChain の固定時刻 direct insert がシフト境界・部分休を再確認しない | シフト外への誤割当 | **MEDIUM** |
| C-13 | pytest 0件（テストスイート完全皆無） | 回帰検出不能 | **CRITICAL** |

### Tier 2: VPS 前提条件の崩壊

| ID | 内容 | 影響 |
|---|---|---|
| V-1 | `kaipoke-net` 名前付きネットワーク不在（実際は default bridge） | D5 docker-compose の `external: true` 失敗 |
| V-2 | cloudflared は systemd ネイティブ（コンテナでもDashboardでもない） | D5 Phase 3 の手順全て無効 |
| V-3 | VPS 2 vCPU、CareLink 要求 2.2 コア（ソフトリミット） | プランUPGRADE or limits 調整が必要 |
| V-4 | Swap 0B、別ディスク不在、定期バックアップなし | OOM・データ消失リスク |
| V-5 | RPA 2ヶ月停止状態（state.json 57日前更新） | CareLink 結合テスト前に再起動検証必要 |
| V-6 | D1: Caddy / D5: nginx / 既存: 直接公開なし（cloudflared 一本）の3者矛盾 | リバプロ統一決定が必要 |
| V-7 | x11vnc -nopw（VNC password 保護ゼロ）+ CORS `*` の組合せ | port 6080 到達で Flask token 完全バイパス |

### Tier 3: 移行設計の致命的欠落

| ID | 内容 | 影響 |
|---|---|---|
| M-1 | INV-5 患者列リスト 5-6列欠落（**住所**、性別、保険区分、訪問頻度、訪問週、備考） | CSV import で **geocoding 完全崩壊** |
| M-2 | INV-5 スタッフ列リスト 2列欠落（拠点住所、スキル） | スタッフ住所→緯度経度の自動取得 break |
| M-3 | ~18 の active sheets が migration scope から完全除外 | Phase 1 並行運用での owner 不明 |
| M-4 | weekly_pattern JSONB 変換仕様が皆無 | 実装者が独自解釈、データ不整合 |
| M-5 | CSV import のトランザクション境界未定義 | 部分挿入失敗時の DB 不整合 |
| M-6 | Sheets API 競合検出メカニズム不在（updated_at 列なし） | 双方向同期の競合解決不能 |

---

## 3. CareLink 着手前の Go/No-Go ゲート

レビュー総合により、以下5ゲートを**通過しないと着手不可**：

### G-1: 既存コードの Tier 1 バグ修正（最低限）
- C-1, C-2, C-3, C-4 の修正（状態管理 + 制約迂回）
- C-7 の修正（areas フィルタ実装）
- C-9, C-10 の修正（diff_engine 安全化）
- 修正後に **pytest スイート整備**（最低 30 ケース）
- 想定工数: **5〜8 営業日**

### G-2: VPS 実態調査の確定
- VPS スペック upgrade 判断（プラン or 別 VPS）
- kaipoke-net / cloudflared 形態を踏まえた docker-compose 再設計
- Swap 4GB 設定 + 定期バックアップ整備
- RPA 復活確認（実際の expand/export/apply で artifacts 生成）
- 想定工数: **2〜3 営業日**

### G-3: 契約凍結会議
- API prefix（`/api/v1/*` で統一）
- ロール（admin/manager/staff の3値）+ 認可マトリックス
- DB マイグレーション所管（D1 が全責任）
- VNC token 方式（既存ランダム token 中継、JWT廃止）
- リバプロ（nginx で統一、Caddy 廃止）
- 想定工数: **1 日**

### G-4: スプレッドシート列の完全棚卸し
- 実 production シートの全列を `UnifiedCode.js` headerMapping から再列挙
- ~18 active sheets の Phase 0/1/2 owner 表作成
- weekly_pattern JSONB 変換マッピング表作成
- 旧VPS API↔CareLink新API対応表作成
- 想定工数: **2 日**

### G-5: 既存資産保護の最終確認
- VNC token を既存ランダム方式中継に変更（D4 計画修正）
- CSP frame-ancestors に CareLink ドメイン追加
- Bearer token 漏洩検出を CI に組込（grep step）
- 異体字マッチングを diff_engine 側にも追加
- 想定工数: **1 日**

### 合計
**Phase 0 工数 = 11〜15 営業日（約 2〜3 週間）**

---

## 4. データ反映方法の最終決定

INV-5 critic レビューによる修正を踏まえ、推奨案を更新：

### 採用: **案A（CSV import）+ 案B（Sheets sync）の組合せ**

ただし以下の追補が必須：

| 項目 | 修正前 | 修正後（必須） |
|---|---|---|
| 患者列マッピング | 20列 | **25-26列**（住所/性別/保険区分含む） |
| スタッフ列マッピング | 11列 | **13列**（拠点住所/スキル含む） |
| シート対応範囲 | 患者・スタッフのみ | **全 ~18 シートの owner 表** |
| weekly_pattern 変換 | 「高難度」記載のみ | **詳細マッピング表** |
| トランザクション | 未定義 | **全件 atomic（失敗時 rollback）** |
| 競合解決 | updated_at 比較 | **新たに updated_at 列を spreadsheet に追加** or `Drive API modifiedTime` 利用 |
| 工数 | 9日 | **9日 + 追加 3日（mapping 監査）= 12日** |

### Phase 0/1/2 ゲート（定量化）

| ゲート | 条件 |
|---|---|
| Phase 0→1 | CSV import 全件成功 + 検証クエリで件数一致 + 緯度経度自動取得 100% |
| Phase 1→2 | Sheets sync 4週連続エラーゼロ + 不一致件数 < 5件/週 |
| Phase 2 安定 | 2週間連続で本番運用、RPA 経由で カイポケ反映成功 |

---

## 5. 移行戦略の推奨

### 割当ロジックの移植戦略（Codex 推奨）

**案B 部分移植**を採用。優先順序：

1. **golden test を固定**（現行 API 入出力スナップショット）
2. `TrialState` 導入（`results/assign_count/staff_day_visits/pid_date_staff` を trial-local に隔離）
3. 制約判定を**純関数化**（NG/性別/勤務日/休み/shift/max/2名体制）
4. **2名体制と mentor_pair を hard constraint として先に検証**
5. diff 側に名前正規化 + key index 導入
6. CareLink adapter は最後に薄く作る

### 検証必須エッジケース10件（Codex リスト）

1. `固定` だが `earliest_min=None`
2. `時間帯` だが earliest/latest 欠落
3. 必須指定スタッフが性別制限に反する
4. 必須指定スタッフが soft cap 超過
5. 同じ人希望が NG staff に入っている
6. 2名体制の片方だけ空き、同期時刻が取れない
7. FinalSweep 後の再挿入で stale overlap が残らないこと
8. mentor trainee が休み/勤務日外/partial block
9. CSV利用者名が異体字違い
10. service_type 空文字または包含関係だけの別サービス

これらを **pytest parametrize で先に固定**し、リファクタ後の同等動作を保証。

---

## 6. 工数再ベースライン（最終）

| フェーズ | 内容 | 工数 |
|---|---|---|
| **Phase 0a** | G-1 既存バグ修正 + テスト整備 | 5〜8d |
| **Phase 0b** | G-2 VPS 実態確定（並行） | 2〜3d |
| **Phase 0c** | G-3 契約凍結会議 | 1d |
| **Phase 0d** | G-4 スプレッドシート棚卸し（並行） | 2d |
| **Phase 0e** | G-5 既存資産保護最終確認 | 1d |
| **Phase 1** | CareLink 基盤構築（D1/D2/D5 並列） | 14d |
| **Phase 2** | 機能実装（D3/D4） | 21d |
| **Phase 3** | 中核機能（週ビュー DnD・連携センター・AI入力） | 14d |
| **Phase 4** | 統合・QA・本番投入 | 10d |
| **合計** | | **約 70〜75 人日（4〜5 週、3名体制）** |

前回 MASTER-PLAN.md（66人日 → 71.5〜76人日）からさらに **Phase 0 で +11〜15 日が確定**。

---

## 7. ディレクターからの推奨アクション

### 即実行（今週中）
1. **G-1 既存コードバグ修正タスクを起動**（最重要）
   - `_unregister_assignment` 修正、`_fix_cross_patient_overlaps` の unregister 追加
   - 必須指定/同じ人希望経路の制約チェック追加
   - areas フィルタ実装
   - diff_engine substring matching 修正
   - pytest スイート整備（最低 30 ケース、Codex 提示エッジケース10件 含む）

2. **G-2 VPS upgrade 判断**
   - Hostinger プランアップグレード見積もり取得
   - 別 VPS（Hetzner CX22 等）との比較
   - 1 週間以内に決定

### 今週末（金曜）目標
3. **契約凍結会議**を 1 日で実施（決定事項を 1 枚に）
4. **スプレッドシート列棚卸し**を完了（実 production との照合）

### 来週以降
5. **改訂版 5 計画書を確定**し、3名体制で Phase 1 着手
6. **Codex 推奨の golden test** を最優先で固定（現行 API 入出力をスナップショット）

---

## 8. 結論

**現状のまま CareLink 開発に着手すると、既存コードのバグを CareLink にも持ち込み、本番障害リスクが極めて高い。**

調査の結果、以下が明確になった：

- 既存コードは **2521行の AllocationEngine** + **943行の diff_engine** に複数の致命的バグが存在（Codex 発見）
- VPS は 2 vCPU 制約があり、追加リソースが必要
- データ移行戦略には **重要な列定義の欠落**があり、特に `住所` 列なしでは geocoding が崩壊
- テストスイートが完全皆無、移植検証の根拠が成立していない

**Phase 0 を 2〜3 週間集中実施**し、既存コードのクリーンアップ + テスト整備 + VPS 確定 + 契約凍結 + スプレッドシート棚卸しを完了させてから Phase 1 着手が、唯一安全な進行方法。

---

## 付録: 全成果物パス

```
docs/audit/
├── INV-1-gas-app-audit.md            (GAS App コード監査)
├── INV-2-python-api-audit.md         (Python/Playwright API 監査)
├── INV-3-allocation-logic-audit.md   (自動算出ロジック深掘り)
├── INV-4-vps-live-state-audit.md     (VPS 実稼働検証)
├── INV-5-data-strategy-audit.md      (データ活用戦略)
├── MASTER-AUDIT-REPORT.md            (本報告書)
└── reviews/
    ├── INV-1-review-A-critic.md
    ├── INV-1-review-B-verifier.md
    ├── INV-2-review-A-critic.md
    ├── INV-2-review-B-verifier.md
    ├── INV-3-review-A-critic.md
    ├── INV-3-review-B-verifier.md
    ├── INV-4-review-A-critic.md
    ├── INV-4-review-B-verifier.md
    ├── INV-5-review-A-critic.md
    ├── INV-5-review-B-verifier.md
    └── Z-codex-allocation-code-review.md  (Codex 詳細コードレビュー)
```

---

## あなた（マネージャー上位）への次アクション選択

- **A. Phase 0（11〜15日のスパイク + バグ修正）を実施する判断**（私の強い推奨）
- **B. 既存コードバグは無視して CareLink 着手、後日リファクタで対応**（リスク高）
- **C. 別 VPS を調達し、既存と完全分離して CareLink 構築**（コスト増）
- **D. 既存システムを使い続け、CareLink を中止**

判断後、即座に対応エージェントを編成して動きます。
