# PC スタッフ別 休み管理・月確定 + モバイル出勤カレンダー（設計）

作成: 2026-08-18。休み申請機能の第2弾（第1弾 = `mobile-leave-request-design.md` の
モバイル送信側・本番稼働済み `5db2a00`）。ユーザー要望:

1. PC でスタッフごとに選択 → その人の休みをカレンダー + 箇条書きで表示 →
   管理者が**増やす/減らす**操作をして**確定**できる
2. 申請済みの休みが管理者に取り消された場合の**スタッフへの通知**
3. モバイルに**出勤カレンダー**（1か月の出勤日/休み）を追加し、
   **月確定の段階でスタッフへ通知** + カレンダーに確定が反映される

## 0. 調査で確定した土台

- 通知は `notifications`（**user 単位**・title/body 表示のみで FE は type 未使用 →
  **新種別に FE 変更不要**）。staff 宛ては `User.staff_id`（生存ユーザーで 1 staff = 1 user
  を部分 UNIQUE が保証）。ただし**アカウント未紐付け staff がありうるので no-op ガード必須**。
- 冪等ヘルパー `_create_idempotent`（`services/checkin/notify.py:101`）は
  reference_id 非 NULL 前提。**None を渡すと以後永久沈黙**する既知の罠 →
  毎回通知したいもの（取消・確定）は dedup を通さず素直に add する。
- 月間出勤の素材は既存 GET 2本で足りる（**BE 追加不要**・本人閲覧可）:
  `GET /staff/{id}/shifts`（7曜日・欠損は is_on=true バックフィル）+
  `GET /staff/{id}/overrides?from&to`（ISO週粒度フィルタ → **月境界の混入は FE で月一致フィルタ**）。
  `useMyShifts` は空スタブなので使わない。
- 「月確定」に相当する状態は**存在しない** → 新テーブルが必要。
- PC 部品は既存で全て揃う: `StaffCombobox` / OverridesCard 一式
  (`OverrideAddDialog`/`OverrideEditDialog`/`OverrideForm`) / `/m/leave` の
  Calendar 月グリッド3状態塗り分け（リポジトリ唯一の月表示実装）。
- `/admin/pending-requests` は PendingRequest 専用モデルのため**別ページ推奨**
  （承認フックは同居可能 — PendingRequestPanel 前例あり）。

## 1. データ層（migration 0073）

新テーブル `staff_shift_confirmations` = 「このスタッフのこの月の出勤カレンダーを
確定として本人に通知した」記録。

| 列 | 型 | 備考 |
|---|---|---|
| id | UUID PK | |
| staff_id | UUID FK staff ON DELETE CASCADE NOT NULL | |
| month | DATE NOT NULL | **月初日** (YYYY-MM-01)。CHECK は貼らず API で day==1 を検証 |
| confirmed_by | UUID FK users ON DELETE SET NULL NULL | 最終確定者 |
| confirmed_at | timestamptz NOT NULL | 最終確定時刻（再確定で更新） |
| created_at / updated_at | TimestampMixin | |

UNIQUE `(staff_id, month)` — **再確定 = 同一行の UPDATE + 再通知**（確定後に
変更があったら押し直して再周知する運用。履歴は持たない = シンプル優先）。

## 2. BE API

### 2-a. 確定 API（新規 `api/v1/staff_shift_confirmations.py`・prefix `/staff`）
- `GET /staff/{staff_id}/shift-confirmations?from=YYYY-MM-01&to=YYYY-MM-01`
  → `ShiftConfirmationRead[]`。RBAC = admin or 本人（staff_overrides と同じ
  `_check_read_access` 流儀）。モバイルが確定バッジ表示に使う。
- `POST /staff/{staff_id}/shift-confirmations` body `{month: "YYYY-MM-01"}`
  → upsert（既存行あれば confirmed_at/confirmed_by 更新）+ **本人へ通知**（§3）
  + 201 で Read 返却。RBAC = `require_role("admin")`。

### 2-b. 通知 3 種（新規 `services/leave_notify.py`・全て commit しない）
| type | 宛先 | 冪等 | トリガ |
|---|---|---|---|
| `leave_rejected` | `request.requester_user_id`（SET NULL されない最安全経路） | reference=(pending_request, request.id) で冪等 | reject エンドポイント（staff_off のみ）。同一 TX |
| `leave_cancelled` | `User.staff_id == override.staff_id` | **毎回通知**（削除→再作成→再削除で沈黙しないよう dedup なし・reference なし） | `DELETE /staff/{id}/overrides/{oid}`（全 override_type。日付は ISO 三つ組から `date.fromisocalendar` で逆算し文面へ） |
| `shift_confirmed` | 同上 | 毎回通知（再確定 = 再周知が目的） | 確定 POST |

文面は BE で完成させる（FE は title/body をそのまま出す）:
- 却下:「休み申請が却下されました」/ 本文 = 日付 + 却下理由
- 取消:「登録済みの休みが取り消されました」/ 本文 = 日付 + 種別ラベル +（管理者へ確認を促す一文）
- 確定:「M月の出勤カレンダーが確定しました」/ 本文 = アプリの出勤カレンダー確認を促す

宛先ユーザー不在（staff 未紐付け）は警告なしの no-op（既存流儀）。

## 3. PC「スタッフ休み・月確定」

**改訂 (2026-08-18 ユーザー指示)**: 独立ページ `/admin/staff-leave` は廃止し、
**申請履歴ページ `/admin/pending-requests` の右カラム**へサブ配置
(`_components/StaffLeavePanel.tsx`・400px・xl で sticky + 内部スクロール、
狭い画面ではリストの下に回り込み)。パネルでスタッフを選ぶと左の申請リストも
同じスタッフで絞り込まれ、タブが「スタッフ予定」へ切り替わる連動つき。
機能内容 (下記) は独立ページ時代と同一。

構成（上から）:
1. **StaffCombobox**（氏名/コード検索）+ 月ナビ（◀ 今月 ▶ — WeekSelector の型）
2. **月カレンダー**（`/m/leave` の Calendar 実装を PC 向けに流用・修飾4状態）
   - 休み登録済み（off/am/pm）= ピンク塗り / 時間変更 = 青系 / **申請中** = ピンク輪郭 /
     勤務外曜日（is_on=false）= グレー
   - **日クリックで増減**: 空き日 → confirm →「休み」override を即作成
     （既存 admin `POST /staff/{id}/overrides`）。休み日 → confirm → override 削除
     （= 自動で取消通知）。申請中の日 → 下の箇条書きで承認/却下（クリックは no-op）。
3. **箇条書き 2 群**
   - 「申請中の休み」= pending staff_off（`usePendingRequests` を
     `target_date_from/to` で月絞り）+ 行アクション **承認** / **却下**（理由必須
     ダイアログ・既存フック）
   - 「登録済みの休み・時間変更」= overrides（月一致フィルタ）+ **取消**ボタン
4. **確定バー**:「この月を確定して本人に通知」ボタン + 確定状態表示
   （未確定 / ✓ 8/18 14:00 確定済み・再確定可）。未処理申請が残っていれば
   確認ダイアログで警告してから確定。
5. 導線: Sidebar（adminOnly）+ `/admin/pending-requests` ヘッダからのリンク。

## 4. モバイル新ページ `/m/shifts`「出勤カレンダー」

- `MobileSection pose="calendar"`。月ナビ（前月〜+2ヶ月程度は自由に移動可）。
- **読み取り専用**の月カレンダー: 出勤日 = 通常 / 休み = ピンク塗り+打消し /
  時間変更・午前午後休 = 中間色 / 勤務外曜日 = グレー / 申請中 = 輪郭（本人の
  pending staff_off を重畳）。凡例 + 集計（出勤 N 日・休み N 日）。
- **確定バッジ**: `GET shift-confirmations` にその月の行があれば
  「✓ M/d 確定」を表示。未確定なら「調整中（確定すると通知が届きます）」。
- データは既存 GET（shifts / overrides / pending）+ 新 GET（confirmations）。
  組み立ては FE（月の各日 → weekday → is_on ベース → override 上書き）。
- 導線: ホームのリンクカード +「休み申請」ページとの相互リンク。

## 5. テスト計画

- BE: 確定 API（本人 GET 可 / staff POST 403 / admin POST 201→再確定 upsert /
  month が月初日以外 422 / 通知行の生成・宛先・再確定で 2 通目）。
  却下通知（staff_off reject → requester に leave_rejected 1 行・staff_off 以外は出ない）。
  取消通知（override DELETE → 紐付けユーザーに 1 行・未紐付けは 0 行で削除成功・
  再作成→再削除で 2 通目が出る）。
- FE: `/m/shifts` ページテスト（出勤/休みの塗り分け・確定バッジ）。
  `/admin/staff-leave` は smoke（スタッフ選択→セクション表示・確定ボタン活性）。

## 6. スコープ外（次候補・持ち越し）

- `PATCH /overrides`（日付変更）時の通知 — 今回は削除のみ。編集は実質
  取消+再登録なので運用でカバー。
- 確定履歴（誰がいつ何回確定したかの監査ログ） — 単一行 upsert のみ。
- 確定後に盤面（visits）へ自動反映する導線 — 従来どおり週生成が読む。
- mobile-leave-request-design.md §6 の残項目（applier upsert 化 / am_off・pm_off の
  エンジン対応 / 管理者への新着申請通知 / CHECK 制約不一致の棚卸し）。
