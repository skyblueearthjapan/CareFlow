# Phase G-21 canary deploy runbook

Phase G-21 (新スケジューリングアルゴリズム + pinned PFV + 同住所 link) を **3 phase canary**
で段階投入する手順. 旧経路は ``office_feature_flag`` で完全保持し、 異常時は flag OFF + DB
rollback で即時撤退できる構成にする.

---

## 用語と前提

- **feature_key**: ``g21_new_algorithm`` (固定文字列, ``app/services/scheduling/auto_allocator_v2.py``
  の ``G21_NEW_ALGORITHM_FEATURE_KEY``).
- **flag テーブル**: ``office_feature_flags`` (``alembic/versions/0037_*`` で追加).
- 1 拠点単位で ON / OFF 切替可能 (= bucket 越え影響なし).
- ``enabled_at IS NULL`` のとき OFF (= 旧経路). ``NOT NULL`` で ON.

---

## Phase α — shadow (= 全 office で OFF / 旧経路 100% 維持)

**目的**: ロジック / migration の安全性を本番データで確認する. 旧経路を一切壊さない.

### 手順

1. release branch から develop → main 反映 + `0037_g21_*` migration を 1 拠点 staging に適用.
2. 全拠点で ``flag OFF`` の状態で 1 週運用する.
3. **KPI 収集 SQL** (DB read only):

   ```sql
   -- 旧経路の visit 配置統計 (= baseline)
   SELECT v.visit_date,
          COUNT(*)                                                   AS total_visits,
          SUM(CASE WHEN v.source = 'auto'        THEN 1 ELSE 0 END)  AS auto_count,
          SUM(CASE WHEN v.source = 'auto_alloc_v2w' THEN 1 ELSE 0 END) AS v2w_count,
          SUM(CASE WHEN v.deleted_at IS NOT NULL THEN 1 ELSE 0 END)  AS soft_deleted
   FROM   visits v
   WHERE  v.visit_date >= CURRENT_DATE - INTERVAL '7 days'
   GROUP  BY v.visit_date
   ORDER  BY v.visit_date;

   -- pinned PFV 件数 (= G-21 で増えるべきでない)
   SELECT COUNT(*) AS pinned_pfv_count
   FROM   patient_fixed_visits
   WHERE  is_pinned = TRUE;

   -- pair link 件数 (= 同住所 blocked / required の link 行)
   SELECT pair_mode, COUNT(*)
   FROM   patient_same_address_links
   GROUP  BY pair_mode;
   ```

4. **合格条件**:
   - ``v2w_count`` (新経路で書かれた visit 数) **= 0** (= 旧経路のみ動いていることの保証).
   - migration エラー / FK 違反ゼロ.
   - 既存 G-21 テスト 90+ 件 pass.

---

## Phase β — 1 拠点 canary (= 特定 office を ON)

**目的**: 本番データで 1 拠点だけ新経路を 1 週稼働させ、 期待動作を確認する.

### 手順

1. 対象 office を 1 拠点選ぶ (= 規模小〜中. 同住所患者 ≤ 3 名程度).
2. admin 権限で ``POST /api/v1/office-feature-flags`` を叩いて ON.

   ```bash
   curl -X POST $BASE/api/v1/office-feature-flags \
        -H "Authorization: Bearer $ADMIN_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
          "office_id": "<office-uuid>",
          "feature_key": "g21_new_algorithm",
          "enabled": true,
          "note": "G-21 canary phase β: 1 拠点投入"
        }'
   ```

3. 1 週間モニタする. 毎日 KPI 確認:

   ```sql
   -- 新経路で書かれた visit 件数 (= 増えるべき)
   SELECT date_trunc('day', v.created_at) AS day,
          COUNT(*) AS v2w_inserts
   FROM   visits v
   JOIN   patients p ON p.id = v.patient_id
   WHERE  v.source = 'auto_alloc_v2w'
     AND  v.created_at >= CURRENT_DATE - INTERVAL '7 days'
     AND  p.primary_office_id = '<office-uuid>'
   GROUP  BY 1
   ORDER  BY 1;

   -- pinned PFV 違反疑い (= apply 経路で動かそうとして 422 になった件数)
   -- audit_logs.action='middleware_request' で status=422 を集計
   SELECT date_trunc('day', a.created_at) AS day,
          COUNT(*) FILTER (WHERE a.path LIKE '%apply-week-only%') AS pinned_blocked_count
   FROM   audit_logs a
   WHERE  a.status_code = 422
     AND  a.created_at >= CURRENT_DATE - INTERVAL '7 days'
   GROUP  BY 1;
   ```

4. **reject 条件 (= 即時 phase α へ戻す)**:
   - **Invariant 違反**: pinned PFV が物理削除された / start_time が変化した.
   - **拠点単位**: visit_plans に対する unassigned 率が前週比 +20% 以上.
   - **週単位**: 同住所 3 名以上の bucket で別 set 移動失敗 (warning ≥ 5 件).

5. 合格なら Phase γ へ.

---

## Phase γ — 全拠点投入

**目的**: 全 office で新経路に切替え. 旧経路は緊急 rollback 用に保持.

### 手順

1. 全拠点を 1 つずつ ON にする (一括 ON は禁止. 拠点ごとに 1 日空けて確認).

   ```bash
   # 全拠点を順番に ON にするスクリプト例
   for office_id in $(psql -tAc "SELECT id FROM offices WHERE deleted_at IS NULL"); do
     curl -X POST $BASE/api/v1/office-feature-flags \
          -H "Authorization: Bearer $ADMIN_TOKEN" \
          -H "Content-Type: application/json" \
          -d "{
            \"office_id\": \"$office_id\",
            \"feature_key\": \"g21_new_algorithm\",
            \"enabled\": true,
            \"note\": \"G-21 phase γ: 全拠点投入\"
          }"
     sleep 86400  # 1 日待つ
   done
   ```

2. Phase β と同じ KPI で全拠点モニタする (1 拠点 / 日 で進捗).
3. 全拠点 OK で 2 週間運用後、 旧経路コード削除 (= 別 PR で対応).

---

## Kill switch (= 緊急 OFF)

新経路で何か起きた場合は **即座に flag OFF** にして旧経路へ戻す.

```sql
-- 全拠点を 1 SQL で一斉 OFF
UPDATE office_feature_flags
SET    enabled_at         = NULL,
       enabled_by_user_id = NULL
WHERE  feature_key = 'g21_new_algorithm';
```

flag OFF にすると次のリクエストから旧経路に切替わる (= プロセス再起動不要).

---

## Rollback (= migration まで戻す)

DB レベルで G-21 列を巻き戻したい場合 (= ``is_pinned`` 列が悪さしている等):

1. **pg_dump で先にバックアップ** (必須. 過去 2 回の DB 全消失事故再発防止).

   ```bash
   pg_dump -h $PGHOST -U $PGUSER -d careflow_production \
           -F c -f /tmp/careflow_before_g21_rollback_$(date +%Y%m%d_%H%M%S).dump
   ```

2. 全拠点 flag OFF (= 上の kill switch SQL).
3. 5 分間旧経路で稼働確認 (= 全拠点 flag OFF の状態でログにエラーが出ないこと).
4. ``alembic downgrade -1`` で 0037 migration を戻す.

   ```bash
   docker compose exec api alembic downgrade -1
   ```

5. application を再起動.

   ```bash
   docker compose restart api
   ```

6. ``patient_fixed_visits.is_pinned`` 列が消えた / ``patient_same_address_links`` /
   ``office_feature_flags`` テーブルが消えたことを確認.
7. もし downgrade で DB が壊れた場合は手順 1 の pg_dump から復元.

   ```bash
   docker compose exec -T db psql -U postgres -c "DROP DATABASE careflow_production;"
   docker compose exec -T db psql -U postgres -c "CREATE DATABASE careflow_production;"
   docker compose exec -T db pg_restore -U postgres -d careflow_production /tmp/careflow_before_g21_rollback_*.dump
   ```

---

## 監視項目 まとめ

| Phase | flag 状態 | 期間 | reject 条件 |
|-------|-----------|------|-------------|
| α     | 全 office OFF | 1 週 | v2w_count > 0 / migration エラー / 既存 test fail |
| β     | 1 office ON   | 1 週 | pinned 違反 / unassigned +20% / 同住所 fail ≥ 5 |
| γ     | 全 office ON  | 1 拠点 / 日 で順次 | β と同じ |

任意 phase で reject 条件に該当したら **kill switch 即実行** → 原因分析 → 再投入.

---

## 関連ファイル

- ``app/services/scheduling/auto_allocator_v2.py`` ``G21_NEW_ALGORITHM_FEATURE_KEY``
- ``app/api/v1/office_feature_flags.py`` (flag CRUD)
- ``alembic/versions/0037_g21_pfv_is_pinned_and_same_address_links.py`` (DB schema)
- ``tests/test_g21_*.py`` (アルゴリズム / API テスト 90+ 件)
- ``tests/test_g21_final_checks.py`` (Critical 4 + High 5 ブロッカー検証)
