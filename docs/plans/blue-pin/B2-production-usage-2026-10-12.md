# B2 青ピン（visits.week_pinned）と「今週だけ」（source='manual_week'）の本番での使われ方

- 調べた日: 2026-10-12（本番 9111655・alembic 0090）
- 方法: 本番 DB を読むだけ（`begin transaction read only; … rollback;`）。書き込みなし。氏名は載せない
- audit_logs の範囲: 2026-05-17 〜 2026-10-12（67,144 行）。応答本文は記録されないため、422 の理由は request_body と盤面から推定
- 画面の「青」の判定（コード）: `week_pinned OR source='manual_week'`（`schedule_v2.py::_visit_is_week_pinned`）

## 結論（先に）

1. **青ピンを手で使ったのは 8/8〜8/11 の 4 日間だけ・admin 1 名・週 32/33 のみ**。以後 2 か月、掛け外しの操作は 0 件。
2. 今ある `week_pinned=true` は **126 行**。生きているのは **週 32 の 119 行（もう過去の週）** だけで、週 33 以降に青ピンは 1 件もない。
3. それでも画面の「青」は多い。`manual_week` が青に数えられるため、**週 36〜42 は 10〜25%、週 43・44 は 99%（コピーで作った週）が青に見える**。青の大半は「人が付けたピン」ではなく「出所の印」。
4. 青ピンが守るはずの「週の生成」で手の変更が消えた事故は **見つからない**（生成のたびに消えたのは `auto` だけ）。一方で **カイポケ取込（smart）は manual_week を消す**: 10/12 に週 42 の `manual_week` 102 行が取込で置き換わった（取込が正という仕様どおり。青ピンも取込は止めない）。
5. 青ピンで操作が止められた（422）記録は **0 件**と見てよい（該当パスの 422 は 9 月の 1 件だけで、その週に青ピンは無い）。

## 1. week_pinned=true の行（現在）

| 週 | 状態 | 削除済み | 行数 |
|---|---|---|---|
| 2026-W27 | planned | 削除済み | 1 |
| 2026-W28 | planned | 生存 | 2 |
| 2026-W28 | planned | 削除済み | 1 |
| 2026-W30 | planned | 削除済み | 3 |
| 2026-W32 | planned | 生存 | 119 |
| 計 | | | **126** |

- 出所の内訳: `import` 114 / `manual_week` 7 / `auto` 5
- 取消（cancelled）・完了（completed）に付いた青ピン: **0**（全部 planned のまま。W32 は過去週なのに planned で残っている）
- 2 名訪問: `required_staff_count>=2` に付いたもの **12**、`visit_group_id` を持つもの 0
- W27〜W30 の分は migration 0066（2026-08-09 決定）で `manual_week` から写したもの（API の記録なし）
- 週 32 の生存訪問 119 行 = **週 32 の全訪問が青**（一括固定の結果）。週 33 は一括固定→一括解除で 0

### 掛け外しの操作（audit_logs）

| 日（JST） | 操作 | 内容 | 応答 | 件数 |
|---|---|---|---|---|
| 08-08 | 1 件ずつ（PATCH …/week-pin） | pinned=true | 200 | 1 |
| 08-08 | 1 件ずつ | pinned=false | 200 | 2 |
| 08-08 | 1 件ずつ | pinned=true | 401 | 1 |
| 08-08 | 今週全件（bulk） 週32 | true 実行 / true 試算 / false 実行 / false 試算 | 200 | 3 / 4 / 1 / 1 |
| 08-08 | bulk 週32 | true 試算 | 401 | 1 |
| 08-09 | bulk 週32 | true 実行 / true 試算 / false 実行 / false 試算 | 200 | 2 / 3 / 2 / 2 |
| 08-10 | bulk 週33 | true 実行 / true 試算 | 200 | 1 / 1 |
| 08-11 | bulk 週33 | false 実行 / false 試算 / true 試算 | 200 | 1 / 1 / 1 |

- 月別: **2026-08 のみ**（PATCH 4・bulk 24）。9 月・10 月は 0
- 実行した人: **admin 1 名**（成功した操作すべて）。staff ロールの操作は 0
- 「今週全件固定/解除」(bulk) の実行: 固定 6 回・解除 4 回（試算 13 回）。8/8〜8/11 のみ。リリース直後の動作確認と見られる

## 2. 週ごとの出所と「青に見える」割合（取消・削除を除く生きた訪問）

| 週 | 訪問 | auto | reset_v2 | manual | manual_week | import | week_pinned | 青に見える | 割合 |
|---|---|---|---|---|---|---|---|---|---|
| W36 | 139 | 5 | 109 | 0 | 25 | 0 | 0 | 25 | 18.0% |
| W37 | 136 | 3 | 0 | 0 | 14 | 119 | 0 | 14 | 10.3% |
| W38 | 137 | 7 | 0 | 0 | 16 | 114 | 0 | 16 | 11.7% |
| W39 | 134 | 0 | 0 | 1 | 29 | 104 | 0 | 29 | 21.6% |
| W40 | 141 | 0 | 0 | 0 | 35 | 106 | 0 | 35 | 24.8% |
| W41 | 146 | 0 | 0 | 1 | 20 | 125 | 0 | 20 | 13.7% |
| W42 | 149 | 1 | 0 | 0 | 24 | 124 | 0 | 24 | 16.1% |
| W43 | 149 | 2 | 0 | 0 | **147** | 0 | 0 | 147 | **98.7%** |
| W44 | 146 | 1 | 0 | 0 | **145** | 0 | 0 | 145 | **99.3%** |
| W45・W46 | 0 | | | | | | | | （まだ無い） |

- `auto_alloc_v2w` などその他の出所はこの範囲に 0
- W43・W44 = 10/9 の「週をコピー」（copy_count 143）で作った週。コピーは `manual_week` で作るため、**ほぼ全部が青**に見える
- W37〜W42 はカイポケ取込（import）が主。import は青にならないが、週生成では manual_week と同じく日ごと保護される（`auto_allocator_v2.py` の manual_week_day_keys）
- 取消の内訳（参考）: W39 import 35・W40 import 10・W41 import 9・W36/W42 manual_cancel 12/9 ほか少数

## 3. 青ピンが守る相手の操作の回数（audit_logs、月別、成功数/全件）

| 操作 | 05 | 06 | 07 | 08 | 09 | 10 |
|---|---|---|---|---|---|---|
| 型に戻す（reset-to-fixed） | 56/62 | 8/8 | 7/7 | 0 | 0 | 0 |
| 週を生成（generate-week-only） | 0 | 0 | 4/6 | 7/7 | 5/5 | 2/2 |
| 月の展開（integrations/expand） | 0 | 0 | 2/2 | 1/2 | 0 | 0 |
| 週をコピー（copy-week 実行） | 0 | 0 | 0 | 0 | 0 | 2/5 |
| 取込 置換（replace-inbound） | 0 | 0 | 9/9 | 0 | 0 | 0 |
| 取込 差分（diff-inbound） | 0 | 0 | 2/2 | 10/10 | 0 | 0 |
| 取込 apply（apply-inbound） | 0 | 0 | 1/1 | 1/2 | 0 | 0 |
| 取込 smart 実行（smart-inbound-apply） | 0 | 0 | 0 | 3/3 | 14/24 | 10/13 |
| 取り込み前に戻す（inbound-snapshots/…/restore） | 0 | 0 | 0 | 0 | 0 | 0 |
| 盤面の元に戻す（op-log/undo・redo） | 0 | 0 | 2/2 | 8/9・1/1 | 0 | 0 |

- 週の生成の対象週: 7 月 W27〜W30・W42、8/7 に W32〜W36、9/3 に W37〜W40、10/2・10/4 に W42
- `generate-week`（担当まで割る版）・`sync-fixed-to-week`（schedule/v2）は記録なし
- 「型に戻す」は 7/27 が最後。現在の主な上書き経路は **smart 取込（月 10〜14 回）**
- 取込前スナップショット: smart 21（08-17〜10-12）・diff 1・copy_week 2。**復元は一度も使われていない**
- schedule_op_log: move_visit_week_only 410・patch_course_staff 166・set_visit_staff 152・cancel_visit 44・delete_visit 25・place_and_fix 19（undo 済み 2）

## 4. 手の変更が消えた事故の有無

### 週の生成・型に戻す

- 生成・型に戻すの日に消えた行は `auto`/`reset_v2` だけ（例: 8/7 auto 354・10/2 auto 112・10/4 auto 132）。**manual_week・import・青ピンの削除は同時刻に無い** → 保護は効いている
- 例外: 7/26 の取込置換（replace-inbound、W30）で import 342・manual_week 2・reset_v2 223 が削除（取込の置換としての仕様動作）

### manual_week がまとまって消えた時（分単位、同時刻 ±2〜3 分の API）

| 日時（JST） | 消えた manual_week | 週 | 同時刻の操作 | 見立て |
|---|---|---|---|---|
| 09-11 08:49 | 15 | W37 | smart-inbound-apply（W37） | 取込が正で置換 |
| 09-16 15:07 | 20 | W38 | API 記録なし | スクリプト/裏処理（取込の根治作業の日）と推定 |
| 09-17 09:57〜59 | 3 | W39・40 | API 記録なし | 同上 |
| 10-02 13:19 | 1 | W40 | smart-inbound-apply | 取込 |
| 10-05 11:20 | 1 | W41 | smart-inbound-apply | 取込 |
| 10-07 10:39 | 3 | W42 | 手の移動・削除 | 通常操作 |
| 10-09 15:58 | 6 | W43・44 | 手の移動・削除 | 通常操作 |
| **10-12 11:25** | **102** | **W42** | **smart-inbound-apply（W42）** | **10/7 のならし案（manual_week）を取込が置換** |

- 青ピン（week_pinned）の行が消えたのは 5 行（W27〜W30、0066 以前の旧分）のみ
- 解釈: **「週の生成で手の変更が消えた」事故は記録上なし**。手の変更（manual_week）を実際に消しているのは **カイポケ取込**で、これは「カイポケが正」の仕様どおり。青ピンは取込を止めない（取込のコードは week_pinned を見ない。見るのはスナップショット復元のみ）

## 5. 青ピンで操作が止められた回数（422）

- 青ピンの 422 を出す口: 今週だけ移動（visit-move-week-only）・担当の今週変更（visit-assign-staff-week）・職員の今週休み（staff-off-week）・コースの曜日移動（course-move-weekday-week-only）
- これらの 409/422（全期間）: **visit-assign-staff-week の 422 が 1 件（2026-09）だけ**
- その 9 月の週に青ピンは存在しない（青ピンの操作は 8/11 が最後・生存は W32 のみ）→ **青ピンによる阻止は 0 件**
- visit-move-week-only の 422 で、今の青ピン訪問に当たるもの: 0

## 解釈（短く）

- 青ピンの「意図して付ける」使い方は定着しなかった（導入直後の 4 日間・1 名・2 週のみ、以後 0）
- 一方で画面の青は `manual_week` 由来で常に 10〜25%、コピー週では ~99%。**「青＝人が守ると決めたもの」になっていない**
- 青ピンが防ぐ相手（週の生成・型に戻す）はすでに `manual_week`/`import` を日ごとに保護しており、実害の記録もない。実際に手の変更を上書きしているのはカイポケ取込で、青ピンはそこに効かない
- W32 の 119 行は過去週の残り（planned のまま）。表示・集計に影響するなら掃除の候補

## 付録: 使った SQL（すべて `begin transaction read only; … rollback;` の中で実行）

```sql
-- 出所の全体分布
select source, count(*) from visits group by 1 order by 2 desc;

-- 1. 青ピンの行
select to_char(visit_date,'IYYY-"W"IW'), status, (deleted_at is not null) del, count(*)
from visits where week_pinned group by 1,2,3 order by 1;
select count(*) filter (where week_pinned and visit_group_id is not null),
       count(*) filter (where week_pinned and required_staff_count>=2),
       count(*) filter (where week_pinned) from visits;
select source, count(*) from visits where week_pinned group by 1;
select count(*) filter (where deleted_at is not null), count(*) filter (where status='cancelled') from visits where week_pinned;

-- 1. 掛け外しの操作
select to_char(a.created_at at time zone 'Asia/Tokyo','YYYY-MM-DD'), a.method,
       regexp_replace(a.path,'[0-9a-f-]{36}','<id>','g'), coalesce(a.role,u.role), a.status_code,
       left(a.request_body::text,200), count(*)
from audit_logs a left join users u on u.id=a.actor_user_id
where a.path like '%week-pin%' group by 1,2,3,4,5,6 order by 1;
select count(distinct actor_user_id), string_agg(distinct coalesce(role,'?'),',')
from audit_logs where path like '%week-pin%' and status_code=200;

-- 2. 週ごとの出所と青の割合
select to_char(visit_date,'IYYY-"W"IW') w, count(*),
  count(*) filter (where source='auto'), count(*) filter (where source='reset_v2'),
  count(*) filter (where source='auto_alloc_v2w'), count(*) filter (where source='manual'),
  count(*) filter (where source='manual_week'), count(*) filter (where source='import'),
  count(*) filter (where source not in ('auto','reset_v2','auto_alloc_v2w','manual','manual_week','import')),
  count(*) filter (where week_pinned), count(*) filter (where week_pinned or source='manual_week'),
  round(100.0*count(*) filter (where week_pinned or source='manual_week')/nullif(count(*),0),1)
from visits where deleted_at is null and status<>'cancelled'
  and visit_date between '2026-08-31' and '2026-11-15' group by 1 order by 1;

-- 3. 操作の回数（月別）
select to_char(created_at at time zone 'Asia/Tokyo','YYYY-MM'), regexp_replace(path,'^/api/v1',''),
       count(*), count(*) filter (where status_code between 200 and 299)
from audit_logs
where path ~ '(reset-to-fixed|generate-week-only|copy-week|smart-inbound-apply|smart-inbound-preview|diff-inbound|replace-inbound|apply-inbound|integrations/expand|op-log/undo|op-log/redo|snapshot|restore|sync-fixed-to-week|/generate)'
group by 1,2 order by 2,1;
select kind, count(*), min(created_at)::date, max(created_at)::date from inbound_snapshots group by 1;
select op_kind, count(*), sum(undone::int) from schedule_op_log group by 1 order by 2 desc;

-- 4. 削除のまとまり
select (deleted_at at time zone 'Asia/Tokyo')::date, source, count(*)
from visits where deleted_at is not null group by 1,2 having count(*)>=5 order by 1,2;
with d as (select date_trunc('minute', deleted_at) t, source, count(*) n from visits
           where deleted_at is not null and (source='manual_week' or week_pinned) and deleted_at>'2026-07-01' group by 1,2)
select (d.t at time zone 'Asia/Tokyo')::timestamp(0), d.source, d.n,
  (select string_agg(distinct regexp_replace(a.path,'^/api/v1|[0-9a-f-]{36}','','g')||':'||a.status_code,' ; ')
   from audit_logs a where a.created_at between d.t - interval '3 min' and d.t + interval '2 min' and a.method<>'GET')
from d order by d.t;

-- 5. 422
select regexp_replace(path,'^/api/v1',''), status_code, count(*) from audit_logs
where status_code in (409,422)
  and path ~ '(visit-move-week-only|visit-assign-staff-week|staff-off-week|course-move-weekday|visit-cancel-week|week-pin|/visits/|op-log)'
group by 1,2;
select count(*) from audit_logs a where a.status_code=422 and a.path like '%visit-move-week-only'
  and exists (select 1 from visits v where v.week_pinned and v.deleted_at is null
    and v.patient_id::text=a.request_body->>'patient_id'
    and to_char(v.visit_date,'IYYY')::int=(a.request_body->>'iso_year')::int
    and to_char(v.visit_date,'IW')::int=(a.request_body->>'iso_week')::int
    and v.start_time::text like (a.request_body->>'old_start_time')||'%');
```
