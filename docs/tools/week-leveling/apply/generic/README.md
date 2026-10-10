# ならし案を本番へ反映する手順（週を選べる版・2026-10-09 週43/44 で実績あり）

週42（`../` の `*_w42*`）を一般化したもの。週43・44 で 114/113 操作・失敗 0・照合 150/150・147/147。
**本番に書く前に PO の了承・pg_dump・別担当レビューが必須**（docs/plans/session-2026-10-09-HANDOFF.md §5）。

## 前提
- 週はすでに作られている（「週を作る」のコピー or 生成）。担当は全部空（画面の「一斉スタッフ未割当」）が前提。
  `build_ops.py` は「案に無い予定の訪問が残っていない」ことを assert する。
- ならし案は `docs/tools/week-leveling/level.py --week <月曜> --off S008 --add-special --seconds 90 --out docs/reports/<名前>`（1 本ずつ・本番は読むだけ）。
  新人（is_trainee）は道具が見ないので `--off <職員コード>` で外す。

## 手順（手元 → サーバー）
1. 操作の一覧を作る（本番に繋がない）
   `python build_ops.py <level の出力フォルダ> <出力フォルダ>` → `ops.json`・`expect.txt`
   - 入らない訪問は step1 で保留プールへ（DELETE /visits。「今週だけ取消」はプールに出ないので使わない）
   - 移動 = visit-move-week-only、特別訪問週間の○ = /special-visit-marks/{id}/place、コースの担当 = PATCH /courses（月〜土）
   - 職員→コースは「今のコースに残る訪問が最多」。**宇田川さんは月〜金だけ都賀 A**（土曜に効かせると都賀に無い土曜コースへ移してしまう・レビュー指摘）
2. 手元で「流す順に追って」移動の元が 1 件に決まるかを確かめる（週43/44 では 0 件 NG）。別担当にレビューを頼む。
3. 戻し用 SQL: `make_restore_TEMPLATE.sql` / `verify_TEMPLATE.sql` は週42 の日付入り。対象週に置き換える:
   `sed -e "s/2026-10-12/<月曜>/g; s/2026-10-18/<日曜>/g; s/iso_week=42/iso_week=<週>/g; s/週42/週<週>/g"`
   サーバーで `psql -q -v ON_ERROR_STOP=1 < make_restore.sql > restore.sql`（**-q を付ける**。付けないと表示の行が混ざる）
   → `sed 's/^COMMIT;$/ROLLBACK;/' restore.sql | psql` で空打ち。
4. 反映前の確認: 案の時点から週が変わっていないか（extract して week.json 比較）・`manual_staff_override` が 0 件か。
5. `pg_dump` → 管理者の鍵をサーバー内で発行して `W_TOKEN=... python3 apply.py ops.json log.jsonl`（止まったら `--from N`。その 1 手が入ったか確かめてから）。
6. 照合: `psql < verify.sql | python check.py expect.txt`（一致件数・小西さんの担当 0・担当なし 0・手の上書き 0）。

鍵の発行: `docker exec carelink-backend python -c "from app.core.security import create_access_token as c; print(c(subject='<user id>', role='admin', staff_id='<staff id>', ttl_seconds=3600))"`（今泉さんの管理者: user c3889e7b-6571-4347-8bc6-7c286b464031 / staff 9361eb66-ea2a-4dbc-9557-a984c21c60d8）。鍵はサーバーの外に出さない。

## 注意
- TPL（コースのテンプレート ID）は稲毛 A〜D・M・都賀 A の決め打ち。臨時・M2 へは移さない。
- `check.py` は月〜土の全予定を照合する（週42 版の土曜除外は外してある）。
