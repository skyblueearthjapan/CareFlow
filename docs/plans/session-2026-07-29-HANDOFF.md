# セッション引き継ぎ書 (2026-07-27〜29) — 次のエージェントはまずこれを読む

本番: https://carelink.kaipoke-api.net（VPS root@72.60.211.213 の /opt/carelink・Cloudflare Tunnel）
本番HEAD **38bd656** / DB migration **0064** / RPA別リポジトリ /root/PlaywrightTest1（変更なし）

---

## 2. どのようなアプリケーションか（30秒版）

訪問介護スケジューリングアプリ **「らく助」(CareFlow)**。
- backend/ = FastAPI + SQLAlchemy 2.0 async + Alembic + pytest（uv。`uv run --no-project pytest`）
- frontend/ = Next.js 15 + TanStack Query + zod + Tailwind(トークン) + vitest
- 1アプリ内3UI: PC管理画面 / 現場ボード / モバイル（QR打刻）
- 中核概念: **患者の恒久パターン = patient_fixed_visits (PFV) が正** → 週生成(Layer1)が
  週の Course/Visit へ展開 → Layer3が自動スタッフ割当 → カイポケ(国保請求ソフト)と
  RPA連携（送る=④反映 / 取り込む=smart-inbound）。プール=未配置患者の置き場+配置提案。
- 設計原則: 「PFVのコースが正・不足は警告(隠さない・除外せず警告+降格)・マスタ駆動」
- 全体像は memory/MEMORY.md と docs/plans/ の各設計書・HANDOFF 群（本書 §5 参照）

## 1. 前回（このセッション）何をやったか — 全6テーマ・すべて本番稼働済み

### 1-1. smart-inbound = カイポケ取り込みの日単位ハイブリッド自動判別 (c21d0d3)
差分/置換のモード選択を廃止。打刻(visit_checkins)のある日=差分（行と打刻紐付け保存）、
ない日=置換、をシステムが日単位で自動判別。❶取得→❷統合プレビュー→❸取り込む の3操作。
export 1回を差分と置換で共用。正典= session-2026-07-26-HANDOFF.md §6-b。

### 1-2. 取り込み対象週の週送りナビ (f976f28)
過去週は**無制限**に遡れる（◀▶ナビ・BEゲートは元々週開始<=今日を許可）。未来は来週まで
（MAX_FUTURE_WEEKS=1・それ以遠はゲートが開かないため）。

### 1-3. 提案エンジンのイベント考慮 = 2段階自動フォールバック (348d8ff・mig 0063)
調査で「イベント(staff_events)を見るのはLayer3だけ。診断・提案系は完全に不認識」と判明
→ PO確定方式で実装: パスA=全イベント占有(±15分バッファ)でクリーン枠→1件でもあれば
それだけ / ゼロ件のときだけパスB=🔒blocking以外を無視して再走査し
warning='event_conflict'+詳細つきで提案（「配置後にイベントを手動調整」を促す）。
🔒 = staff_events.blocking (mig 0063・イベント編集の「この時間は絶対に空けておく」トグル・
カイポケ再取込でも保持)。適用= propose-slots/pool-overview(警告+-60降格)・改善move(2段階)・
詰まり解消退避/範囲最適化(クリーンのみ)・swap対象外(時刻交換のみ)・Layer3不変・**診断は未対応**。
正典= session-2026-07-26-HANDOFF.md §6-c + memory `careflow-event-aware-proposals`。

### 1-4. 提案内の日別スケジュール表示にイベント(緑カード)を統合 (4a58d0c)
生成元は2系統だけと全数調査で確定: ①mini_schedule(_build_mini_schedule に is_event行注入
→プール候補/効率代替/定員超過/採用前後比較/2名相方/モバイルFieldSheets)
②コーススナップショット(snapshot_course_bucket に events付与→改善提案src/dst・
範囲最適化step・詰まり解消before/after)。FE描画= CourseMoveTimeline `TimelineRow.isEvent`+
`eventTimelineRows` / PoolCandidateList MiniRow / FieldSheets MiniSlot（--sched-event-*トークン）。

### 1-5. 週ビュートグル改称 (4c11c36)
タイムライン/リスト/スタッフ別 → タイムライン/**コース別**/スタッフ別（名称のみ）。

### 1-6. ★特別訪問週間 = 上乗せ型 (a3adad6〜38bd656・mig 0064) — 本セッションの本丸
**正典 = `docs/plans/special-visit-week-design.md`（PO確定仕様全文）+ handoff§6-d 相当の要約は
memory `careflow-special-visit-week`。**
- 患者ごとに期間(任意起点・1〜4週/1〜2ヶ月チップ)+目標「週N回以上」(既定5)。
  **固定訪問はそのまま**、大型カレンダーの曜日セルに○(追加枠)→週ごとにプールチケット化
  →提案or手動で都度配置。**恒久パターン(PFV)には一切書き込まない**。期間終了で自然に戻る。
  既存の置換型 special_weekly_pattern/special_week_active/PFV mode='special' は**据え置き不使用**
- DB: mig 0064 = special_visit_periods + special_visit_marks(kind=extra/displaced・
  ○は部分ユニークで1セル1個・displaced_snapshot で復元)
- 退避=**日単位**トグル(「固定どおり」⇄「この日はプールへ退避」)。生成済み週=visit soft-delete
  +snapshot / 未生成週=マークのみ(Layer1 `_displaced_weekdays()` が展開skip)。
  restore は配置済みなら force=true 必須(409)
- 週合計 = 固定残+○(未配置含む)+退避チケット(退避しても不変・配置済み○のvisitは
  fixed_visitsから除外=二重計上防止)。目標未達週は赤表示
- API 10本 `api/v1/special_visits.py`。place は course_id or (office_id+course_code)。
  枠長正典=`_resolve_service_minutes`(PFV→weekly_pattern→30分)・poolチケットにも同梱
  （提案と配置の枠長一致）。○作成は期間範囲外422
- FE: `SpecialVisitWeekDialog`(大型カレンダー・入り口=患者マスタ編集+スケジュール患者詳細
  ⭐強調ボタン・開始ボタンは期間チップ右隣) / プール最上段⭐セクション
  (`SpecialTicketPlacePanel.tsx` 内 SpecialVisitPoolSection)。
  **UI統一済み(fc370e7)**: チケットカード=通常プール患者と同じ視覚言語、クリックで通常患者と
  同じ `PatientScheduleDetailDialog`→`PoolCandidateList` ポップアップ。
  `PoolCandidateList` の **specialTicket prop** = 特別モード（週・曜日固定/
  「この週のみ・固定化しません」/確定=place API/💡前回配置ヒント/**未指定時は完全不変**）
- 実装体制: ディレクター(本エージェント)がOpus 5サブエージェント3体に分担発注→レビュー。
  レビュー補強3件（期間範囲422/place契約拡張/枠長同梱）はディレクターが直接実装

## 3. 残タスク・気になる点

### コード側（次のエージェント向け）
1. **2名体制患者の週合計**: 特別訪問カレンダーで同日2枚=2カウント（PO未確認の割り切り。
   「1回」と数えるべきとPOが言ったら calendar API の集計を要修正）
2. **手動盤面配置と○の自動リンクなし**（設計§7スコープ外）。手動で入れたら○は手で消す運用
3. **特別チケットの候補ゼロ時の文言**は通常プールと同じ除外理由内訳表示（専用文言に戻すのは
   すぐできる・PO確認待ち）
4. **スケジュール診断(schedule-health)はイベント未対応**（イベント重複の検出項目は将来課題）
5. 特別モードの propose は `include_efficiency_alternatives: false`（曜日固定のため。
   「他曜日でも見たい」運用が出たら要相談）
6. 全拠点表示時の特別チケット propose は患者主担当拠点へフォールバック（拠点跨ぎ配置は未対応）
7. restore で行が物理削除されていた場合の再作成は source='auto'（実害なし想定・認識のみ）

### テスト・環境の既知事象（ハマり防止）
- BE フルスイートの既知ベースライン失敗 **16件+1エラー**（audit/auth/patients_v2/
  kaipoke統合など環境起因。stash検証済み・一覧は本セッションログ）。変更に関係する
  スイートの成否で判断する
- FE `pnpm vitest run`（引数なし）は e2e/*.spec.ts を誤収集して9ファイル落ちる（既知）。
  ディレクトリ指定で回す。BulkPoolInsertDialog に稀なフレーク1件
- **PowerShellのGet-Content/Set-Contentで日本語ファイルを触らない**（文字化け事故歴3回。
  Edit/Writeツールのみ）。PowerShellの `&&` 不可・bash併用可
- staff_events の timestamptz は「naive壁時計をUTCラベルで保存」しており psql 表示は+9hズレて
  見えるのが正常（比較は全コードが tz を剥がして行う）
- デプロイ: pg_dump→push→pull→build（**migrationや新規ファイルありは --no-cache**）→
  alembic upgrade head→up -d --force-recreate→healthz内外。FE変更後は現場ハードリロード案内

### PO側の残り（コード作業なし・以前からの持ち越し）
- 髙梨さん新人フラグの判断 / 高尾幸子さん患者登録→置換再実行 / 7/20週再置換の突合
- Cloudflare Session Duration延長・Service Token化（session-2026-07-10-HANDOFF §6-1/6-2）
- 特別訪問週間の実機確認（PO確認中 2026-07-29〜: カード/モーダルUI統一まで反映済み）

## 5. 正典・参照

| 内容 | 場所 |
|---|---|
| 特別訪問週間の仕様全文 | docs/plans/special-visit-week-design.md |
| カイポケ連携（イベント取込/置換/smart-inbound） | docs/plans/session-2026-07-26-HANDOFF.md（§6-b/6-c/6-d）+ kaipoke-event-inbound-design.md |
| イベント考慮2段階提案の実装 | proposal_solver.py（EventWindow）+ memory `careflow-event-aware-proposals` |
| メモリ索引 | ~/.claude/projects/...(このプロジェクト)/memory/MEMORY.md |
| デプロイ手順 | memory `careflow-deploy` + docs/deployment/runbook.md |
