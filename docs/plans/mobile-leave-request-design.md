# モバイル休み申請（カレンダー選択）設計書

作成: 2026-08-18。発端 = ユーザー要望「スマホから楽に休み申請 → PC で確認 →
スタッフマスターへ自動反映」。**本ターンのスコープ = モバイルで申請を出すところまで**。
PC 管理者側の運用改良は次ターン（§6 に持ち越し事項を列挙）。

## 0. 調査で確定した土台（3並行調査の結論）

- **申請基盤は AI 撤去後も存続しており、`staff_off` 経路がまさに要望の設計**:
  `POST /pending-requests`（staff 本人可・`payload.staff_id` はサーバが強制上書きで
  なりすまし不可）→ admin が承認 → `_apply_staff_off` が
  **`staff_weekly_overrides` へ INSERT**（`pending_request_applier.py:235`）。
- `staff_weekly_overrides.override_type='off'` は**スケジューリングエンジンのハード制約**
  （Layer3/週生成/プール提案すべてが曜日除外。`layer3_assignment.py:3241` 他）。
  = 「スタッフマスターへの反映」はこのテーブルで完結する。
- PC の承認 UI（`/admin/pending-requests`「モバイル申請履歴」）と現場ボード
  ApprovePanel は既存。**欠けているのはモバイルの送信 UI だけ**。
- UI 資産: shadcn `Calendar`（react-day-picker v8・`mode="multiple"` 対応・
  選択色は既にブランドピンク）、マスコット `rakusuke-pose-calendar.png`、
  `MobileSection` / 下部タブ4つ / ホームのリンクカード導線。
- 既存「シフト希望提出」（`/m/mypage`・月単位フリーテキスト・`shift_requests`）は
  **別系統で業務反映なし**。本機能とは併存させ、統合判断は PO 持ち（§6）。

## 1. 確定仕様（本ターン）

### 1-a. 申請の粒度 = 「1日 = 1申請」
複数日選択は日付ごとに `pending_requests` を 1 行ずつ作る（applier が
`payload.date` 単数しか読まない既存契約に合わせる）。
- 利点: applier 無改修 / PC 側で**日単位に承認・却下できる**（部分承認が自然にできる）/
  「あとから1日追加」= 追加 POST 1 本で (d) の要望を満たす。
- スタッフ視点の「1つの申請が更新されていく」体験は、モバイル画面側で
  「申請中の休み」を日付一覧として束ねて見せることで実現する。

### 1-b. payload 契約（`_apply_staff_off` の実装に厳密一致）
```json
{
  "request_type": "staff_off",
  "target_staff_id": "<staffId>",
  "target_date": "YYYY-MM-DD",
  "payload": { "staff_id": "<staffId>", "date": "YYYY-MM-DD",
               "override_type": "off", "note": "<任意・全日共通の備考>" }
}
```
- `override_type` は **DB 正典の `"off"`** を送る（applier は raw 文字列を
  そのまま `StaffWeeklyOverride.override_type` に入れるため、日本語ラベル
  `"休み"` を送ってはならない — staff_overrides API とは正規化層が違う）。
- 終日休みのみ。午前休/午後休は**エンジンが未対応**（`am_off`/`pm_off` は
  登録しても全箇所で無視され終日出勤扱い）のため本ターンでは出さない（§6）。

### 1-c. BE 追加 2 点（migration なし）
1. **重複ガード**（`POST /pending-requests` 内・`staff_off` のみ）:
   - 同一 `target_staff_id` × `target_date` の **pending な staff_off が既にある** → 409
     「この日は既に申請中です」
   - その日付の ISO 週三つ組に **`staff_weekly_overrides` 行が既にある** → 409
     「この日は既に休み・時間変更が登録されています」
   - 理由: applier は upsert せず INSERT のみのため、放置すると**承認時に**
     IntegrityError→422 になる。作成時に前倒しで弾いて申請者に即返す。
     date が特定できない場合はガードをスキップ（既存クライアント互換）。
2. **取り下げ** `DELETE /pending-requests/{id}` (204):
   - staff = **自分が申請した** pending のみ / admin = 任意の pending。
   - `status != 'pending'` または `applied_at` 有り → 409。他人の行は 404。
   - 行ロック（SELECT FOR UPDATE）で approve との競合を防ぐ。ハード削除
     （却下と違い業務判断が発生していないため履歴に残さない）。
   - ポチポチ誤操作の自己修正手段。承認後の取消は管理者経路のみ（従来どおり）。

### 1-d. モバイル UI（新ページ `/m/leave`）
- `frontend/app/(mobile)/m/leave/page.tsx`。`<MobileSection pose="calendar"
  title="休み申請">`。下部タブには**載せない**（密度維持）。導線 =
  `/m/home` のリンクカード + `/m/mypage` のリンク行。
- **カレンダー**: `<Calendar mode="multiple">`
  - day セルを `h-11 w-11` に拡大（タップターゲット 44px）
  - 選択可能範囲: **今日 〜 +90日**（PC スタッフマスターの表示ホライズンと一致）
  - `modifiers` で状態を3色表示: 選択中（ピンク塗り=既定）/
    **申請中**（ピンク輪郭・タップ不可）/ **登録済みの休み等**（グレー塗り・タップ不可。
    自分の `GET /staff/{id}/overrides` から。GET は本人可）
  - 凡例チップを直下に表示
- **備考**（任意・1フィールド・選択全日に共通コピー）
- **送信**: 選択日サマリ（「8/24（月）・8/25（火）…」）+ フル幅ボタン
  「この内容で申請する（N日）」→ 日付ごとに逐次 POST。
  **部分失敗時は成功分だけ選択から外し、失敗日はエラー内容つきで選択に残す**
  （D-1 一括登録と同じ再送安全パターン）。全成功で toast + マスコット clap。
- **申請中の休み一覧**: pending の staff_off を日付順に表示、各行に
  「取り下げ」（確認ダイアログ→ DELETE）。直近の承認/却下も
  「最近の結果」として表示（却下理由つき）。
- staffId 未紐付けアカウントは既存 mypage と同一の destructive Alert。

### 1-e. FE クエリ層
- `lib/queries/pending_requests.ts` に `useWithdrawPendingRequest()` を**追加**
  （既存関数のシグネチャ変更禁止のファイル所有ルールに従い追加のみ）。
- 作成は既存 `useCreatePendingRequest()` をそのまま使用。

## 2. RBAC / 安全性（既存機構に乗る）
- なりすまし不可: `_STAFF_AXIS_REQUEST_TYPES` により payload.staff_id を強制上書き。
- staff の GET は「自分の申請 + 自分宛」に自動絞り込み済み。
- `/m/*` は middleware でログイン必須。ページ側は staffId 欠落 Alert + `enabled` ガード。

## 3. テスト計画
- BE（`tests/test_pending_requests.py` に追加）: 重複ガード 409 ×2 種 + 別日 OK /
  取り下げ: 本人 pending 204・他人 404・approved 409・admin 204。
- FE: `/m/leave` ページテスト（next-auth/クエリ層 vi.mock・タイトル表示 /
  staffId 無し Alert / 未選択時ボタン disabled / 申請中一覧表示）。

## 4. デプロイ
migration なし。通常デプロイ（backup → pull → build → recreate → healthz）。
BE 先行でも FE 先行でも壊れない（新規エンドポイント追加のみ・既存契約不変）。

## 5. 画面イメージ（テキスト）
```
◄ 休み申請  [らく助 calendar]
「おやすみしたい日を えらんでね」
┌────────────────────┐
│   ◄  2026年8月  ►         │
│  月 火 水 木 金 土 日        │
│  … カレンダー (44px セル) …  │
│  ●選択中 ◍申請中 ◌登録済み   │
└────────────────────┘
備考(任意): [____________]
選んだ日: 8/24（月）・8/25（火）
[ この内容で申請する（2日） ]
── 申請中の休み ──
 8/20（木）「通院のため」   [取り下げ]
 8/24（月）               [取り下げ]
── 最近の結果 ──
 8/18（火） 承認されました ✓
```

## 6. 次ターン以降への持ち越し（PC 管理者側 ほか）
1. **PC 申請履歴の運用性改良**（本命・次ターン）: staff_off を日付でグルーピング表示 /
   一括承認 / 承認時のスタッフマスターへの反映結果表示。
2. **applier の upsert 化**: `_apply_staff_off` は INSERT のみ → 同日既存 override で
   承認 422。作成時ガード（本ターン）で大半は防げるが、根治は upsert。
3. **午前休/午後休のエンジン対応**: `am_off`/`pm_off` が
   `layer3_assignment.py:3241` / `auto_allocator_v2.py:3444,9939` /
   `propose_slots_service.py:394` で無視される既知ギャップ。対応後にモバイル UI へ
   種別選択を追加。
4. **管理者への新着申請通知**（ベル/バッジ）: 現状は PC ページを開かないと気づけない。
5. **request_type CHECK 制約の不一致**（別件バグ疑い）: migration 0013 の CHECK は
   旧9種のままで、`staff_status_update` / `patient_status_update` /
   `patient_visit_add` の INSERT は本番 Postgres で CHECK 違反になるはず
   （SQLite テストでは未検出）。staff_off は9種内で本機能に影響なし。要棚卸し。
6. **shift_requests（月単位フリーテキスト）との統合判断**: 併存で開始し、
   現場の使われ方を見て PO 判断。
7. 承認後の**既生成週への再割付導線**（承認しても既存盤面は自動では動かない）。
