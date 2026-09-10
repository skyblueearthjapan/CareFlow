# セッション引き継ぎ 2026-09-10（患者ステータス×予定の連動: Phase 1 デプロイ → Phase 2 入口ガード → Phase 3 表示の保険・突合）

**次のエージェントへ: まずこのファイル → `patient-status-schedule-design-2026-09-09.md` §6-C（PO 確定事項）・§7（実装計画・「実装済み」の囲み）を読むこと。** 前セッション総括 = `session-2026-09-09-HANDOFF.md`（Phase 0 一掃・設計確定・Phase 1 実装）。

## ★ 最初の 5 分
0. **本番稼働 HEAD `5c10906`（2026-09-10 朝・migration 0082 適用済み）**。Phase 1〜3 + PO フィードバック是正（バッジはステータス変更日以降の予定だけ）が全部本番に入っている。作業ツリーはクリーン（未追跡は従来の docs/reports 等のみ）。
1. **コミット**: `462a8d8`（Phase 1・前セッション）→ `4f7082e`（Phase 2 入口ガード）→ `14d53d5`（Phase 3 表示・突合）→ `5c10906`（PO フィードバック: 「入院中」バッジは `status_changed_at` の JST 日付以降の planned だけ・未記録は今日以降・DTO に `patient_status_since`・現場ボード/モニターは表示日で判定）。各 Phase は BE/FE 2 レーン並行 → code-reviewer（Opus）2 本 → 是正 → 独立確認（pytest 対象スイート緑・tsc 緑・vitest 2335/2336 = 失敗 1 は既知 middleware）→ コミット。
2. **前セッションの「落ちた」の正体**: Claude Code のセッション自体が 9/10 00:02 JST に終了。最終報告まで書き終えた区切りで止まっており作業損失なし。本番サーバーは無事（再起動・OOM なし）。
3. **デプロイの教訓（新規）**: この手順では **migration が自動適用されない**。`up -d` 後に `docker compose ... run --rm backend alembic upgrade head` → `alembic heads` 単一確認が必須（9/10 に 0082 が未適用のまま healthz 200 だった）。memory `careflow-deploy` に追記済み。
4. **残 = 実機確認（§4）と PO 判断（§5）**。共通パスワードはリポジトリに無いので認証付きの実機確認は PO 側で。
5. PO が「見えない」と言ったら PWA キャッシュ（Ctrl+Shift+R）。

## 1. 各 Phase でできたもの（正典 = 設計書 §7 の囲み + 各コミットメッセージ）

### Phase 2 入口ガード（4f7082e）
- BE: `ensure_patient_schedulable`（422 `patient_not_active` + `can_override:true`）を place-and-fix / fix-or-pattern / POST visits / apply-individual / visit-move-week-only / update-fixed-time-master / apply-swap / propose-slots(existing_patient_id) / sync-fixed-to-week / ⭐ period・place・restore・marks・displace に配線。**PUT fixed-visits と apply-individual は型のみ許可**（pattern_and_week は 422 + `allowed_scope:"pattern_only"`）。一括系（pool-overview / bulk-simulate / bulk-apply）は除外して `excluded_patients[]`。⭐ プール/カレンダーは除外せず `patient_status` を載せる（Q⭐「残す」）。取込 preview は非稼働 add 行を `inactive_patient` で自動選択外 + `summary.inactive_patient`、apply/replace は `reason/code="inactive_patient"` で skip。PATCH /visits は非稼働への付替え・`status_cancel` の planned 復活を 422（manual_cancel は既存の戻し経路を維持）。correction-items PATCH の MissingGreenlet 500 を修正。
- FE: `PatientNotActiveGateProvider`（app/providers.tsx）+ `useGuardedMutation` を全配置経路に配線。422 → 「予定に入れられません」→「稼働中にして続ける」→ `PatientStatusChangeDialog(to='active')` → 復帰トースト → 元操作を 1 回だけ再実行。同一患者の並行呼び出しは相乗り、別患者は後着を元エラーで拒否。⭐チケット / PatientCard / 期間ダイアログの非稼働バッジ。ピッカー除外（SuggestSheet / PlacementSheet / PatientCombobox / ＋訪問）。固定訪問パネルは「型のみ編集」バナー + scope 固定（三重ロック）。取込 preview チップ「非稼働患者 N」+ 行バッジ。pool-overview の除外トースト。

### Phase 3 表示の保険・突合（14d53d5）
- BE: `VisitRead.patient_status`、`BoardVisit` / `MonitorVisit` に `source` + `patient_status`。突合レポートに「非稼働患者」カテゴリ = **今日以降・カイポケのみ・患者非稼働** の行だけ（削除候補）。4 構造キーは不変で内数、専用セクション。`_read_correction_items` は delete 行にも `inactive_patient`（inbound=らく助側の残骸取消 / outbound=削除候補）。未送信サマリに `inactive_groups[{patient_id,patient_name,status,status_label,count,sendable_count}]` + `inactive_residue`（今日以降 planned・⭐配置除外・拠点スコープ・突合不能時は 0）。pool_bulk_inserter は `BulkUnplaced(reason="patient_not_active")`。
- **設計判断（レビューで確定・設計書 §3-4/§5 より優先）**: csv_builder と 提案/実現性/health/代替 の占有計算は **非稼働患者の planned を除外しない**。理由 = ⭐「残す」配置と過去の実績を守る・二重予約を防ぐ・残骸は隠さず `inactive_residue` で報告する。除外するのは「候補としての非稼働患者」（Phase 2 ガード）だけ。
- FE: `lib/schedule/visitVisibility.ts`（`classifyVisitDisplay` 単一ソース）。トグル「非稼働を表示」（`useUIStore.showInactiveVisits` 永続化・盤面ツールバー右端）。status_cancel は既定非表示、表示時は打ち消し + 「取消（連動）」でメニュー/DnD 不可。非稼働 planned は「入院中」バッジ + 薄表示（完了/不在/取消には付けない）。型へ反映ダイアログと件数集計はトグル非依存。現場ボード / モニター / モバイルは常時非表示 + バッジ。SyncBar は行種別タグを残して非稼働患者バッジを追加（add=取り込まない / delete=削除候補）。未送信「◯◯様 入院中の取消 N 件（うち過去 M 件は送信対象外）」+ 残骸アラート。突合レポートボタンに非稼働患者件数。

## 2. 進め方（このセッションで機能した型）
- コーディネータ（私）が契約（422 の形・追加フィールド名）を先に固定 → BE/FE executor（Opus）を並行投入 → 完了ごとに code-reviewer（Opus）で **契約突合** → 是正指示 → 独立確認 → コミット。
- レビューは実際に BLOCKER を捕まえた（Phase 3 BE: csv_builder が過去実績まで落として削除候補に載せる）。**レビュー無しでは本番に出ていた**。
- **禁止事項の再確認**: 並行レーンで `git stash` 禁止（2 レーンが「stash で確認」と報告 → 実害なしを確認したが指示に明記が必要）。`cp` での退避も共有ファイルでは危険。

## 3. 教訓
0. 訪問の「完了」は QR 退室打刻でしか付かない（日付経過では変わらない・no_show も planned 据置）。「planned のまま残った過去日」は実績である可能性が高く、現在ステータスで塗ってはいけない。
1. migration は自動適用されない（§0-3）。
2. 「非稼働患者の予定を除外」は文脈で意味が逆転する: **候補**からは除外、**占有**からは除外しない。設計書の一文（§3-4「提案系は対象外に」）はこの区別を欠いていた。
3. `inactive_patient` のような真偽フラグは方向（add/delete）で意味が反転する。FE は必ず action で分岐する。
4. モニターの DTO は `status` を持たない（`phase`）。共有ヘルパを使い回すときは DTO の語彙を確認する。

## 4. 実機確認（本番・admin・テスト患者で・設計書 §7-7 相当）
(a) テスト患者に型を作り週生成 → 入院中へ → ダイアログ件数 = 盤面の件数 → 確定 → 盤面/タイムライン/職員スケジュール/現場ボード/モニター/モバイルから消える → トグル「非稼働を表示」で打ち消し表示 → 「稼働中」に戻す → 週別件数 → 予定が戻る。
(b) 特別訪問週間ありのテスト患者で keep/end 両方。keep 時: ⭐ プールに「入院中」バッジ・配置は 422 → 「稼働中にして続ける」で復帰 → 再実行される。
(c) 入院中の患者を ＋訪問 / 空き枠登録 / プールから配置しようとする → 案内 → 「稼働中にして続ける」→ 復帰トースト → 元操作完了。「やめる」→ 従来のエラートースト 1 回だけ。
(d) 入院中の患者の固定訪問パネル → バナー「型のみ編集できます」・週反映の選択が固定。
(e) 現場シート（/m）からのステータス変更でもダイアログ。
(f) 突合（🔄）→ 非稼働患者の未来のカイポケ行が「非稼働患者（削除候補）」。過去の実績行は一致のまま。未送信サマリに「◯◯様 入院中の取消 N 件」。残骸 0 なら残骸アラートは出ない。
(g) 取込 preview → 非稼働患者の add 行は未選択 + 「非稼働患者 N」チップ。
(h) 通知（admin 全員・申請者）。

## 5. PO 判断・バックログ
- `PATCH /special-visit-periods` は未ガード（非稼働患者の期間を延長できる）。Q⭐「残す」に隣接するので PO 判断。
- 復帰後の再実行が別エラー（409 など）で落ちるケース: 復帰トースト + 元経路のエラートースト 1 回。文言改善は将来。
- カイポケ週間パターン停止は人手（通知文に記載）。RPA 化は将来。
- 効果日（入院予定日）の事前指定（設計 §6-3）は将来。
- 前々セッション残（`session-2026-09-08-HANDOFF.md` §6）: 実機確認 (a)〜(h)・PO 判断 5 件・BE 開講日バリデーション・入替 1 TX。
- 既知失敗: backend 29 件（RBAC drift 13・audit 3・patients_v2 3・reset ペア整列 2・diff engine float.replace 2・misc）+ フレーク 3（test_visits delete 204 / qr_open_checkin / admin_users）。frontend 1（middleware manager）。

## 6. 参照
- 設計: `docs/plans/patient-status-schedule-design-2026-09-09.md`
- 前セッション: `docs/plans/session-2026-09-09-HANDOFF.md`
- デプロイ: memory `careflow-deploy`（migration 手動適用を追記済み）/ `docs/deployment/runbook.md`
