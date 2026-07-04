# W-12d 詰まり解消相談 設計書 v1

作成 2026-07-05 / PO 方向性承認 2026-07-04（「Aさんを15:30にずらせば D の枠が空くよ」と囁くアドバイザー）。
前提: `docs/plans/two-staff-pairing-design.md` D-7 / 方式b（定員超過相談）の姉妹となる第2の相談型。

## 0. コンセプト

個別提案（プール患者クリック）で**候補0件**になったとき、「既存の訪問を1〜3手ずらせば入ります」
という具体的な開通手順を、乱れの小さい順に提案する。**発動は候補0件のときだけ・ボタンを押した
ときだけ**（普段は黙っている）。方式b と対をなす:

- 方式b: 「定員を +1名 許容すれば入ります — 相談しますか？」
- 本機能: 「この方をずらせば入ります — 相談しますか？」

## 1. 設計原則（PO 承認済みの制約）

| # | 原則 |
|---|---|
| P-1 | **連鎖は最大3手**（動かす既存訪問 ≤ 2 ＋ 対象患者の配置 1）。全体組み替えは提案しない |
| P-2 | **弁える**: pinned / movability=locked は絶対に動かさない。2名体制の枠・同住所ペアの片割れも v1 は動かさない（動かせない事情の会計に計上）。却下記憶（suggestion_dismissals）を尊重 |
| P-3 | **確認不要の手のみ**（D-2 踏襲）: 退避先は「希望範囲内」または movability が許す範囲のみ。希望外＋movability 不明は提案しない（confirmation_required_excluded 会計） |
| P-4 | **自動適用しない**: プランは提示のみ。適用は「誰がどう動くか」を明示した確認を経て1クリック原子適用 |
| P-5 | **同じ物差し**: 各手の効果は compute_exact_marginal（分/週）。プランの total_delta = 全手の合計 |
| P-6 | **適用は型レベル（pattern_and_week）固定**（v1）: 移動も配置も恒久パターンに対する操作で一貫させる（scope-optimization apply と同じ世界）。週限定の詰まり解消は将来課題。Ctrl+Z 対象外（バナー明示） |

## 2. アルゴリズム（新モジュール `unblock_search.py`）

### 2.1 探索（read-only・決定論）

```
入力: 対象患者 candidate（propose-slots と同じ CandidateInput。2名体制含む）、office、週
1. load_week_course_buckets で現状ロード → 可変コピー（_copy_bucket）
2. 「ブロッカー除去テスト」:
   各バケット B、B 内の各既存訪問 v（動かせるものだけ — P-2/P-3 の適格判定）について:
     a. B から v を取り除いた模擬状態で、対象患者が入るか？
        - 通常患者: find_available_slots_for_candidate（B 単体）
        - 2名体制: ペアアンカー（W-12a の slot_fits_exact — 相方バケットは現状のまま or
          相方側のブロッカー除去も同様に試す）
     b. 入るなら: v の退避先を列挙 — find_available_slots_for_candidate を
        「v の患者・v を除いた全バケット」で実行（improvement_engine の move と同じ）。
        退避先も P-3 でフィルタ。delta = compute_exact_marginal（v の現位置 vs 退避先）
     c. 成立: プラン { moves: [v→r], insert: (B, T) } を生成
3. 深さ2（同一バケット内の2訪問 v,w を両方除去 → 入るか → 各々の退避先）。
   組合せは同一バケット内 C(≤6,2)=15 に限定。玉突き連鎖（v の退避先が w を追い出す）は v1 対象外
4. ランキング: (動かす人数 asc, 全手が希望範囲内=先, total_delta asc, 決定的タイブレーク)。上位5件
5. 会計: 動かせない訪問の内訳（pinned/locked/two_staff/pair/却下記憶/確認要）を返す（N-6）
```

計算量: バケット ~30 × 訪問 ≤6 × 退避先探索（ソルバ1回 ~100候補）≈ 数千判定 ＋ 深さ2の限定組合せ。
同期 API で成立（scope-optimization simulate と同オーダー）。

### 2.2 API

**POST /v2/propose-unblock**（read-only）
```jsonc
// Request: propose-slots と同形の candidate 情報 + { office_id, iso_year, iso_week, limit?: 5 }
// Response:
{
  "plans": [ {
    "plan_id": "決定的ハッシュ",
    "moves": [ { "patient_id","patient_name","from": {weekday,course_code,start_time},
                 "to": {weekday,course_code,start_time}, "delta_minutes",
                 "within_preference": true } ],
    "insert": { "weekday","course_code","start_time","end_time",
                "partner_course_code": null /* 2名体制ならペア */ },
    "total_delta_minutes": 5, "moved_count": 1
  } ],
  "unmovable_summary": { "pinned": 2, "locked": 1, "two_staff": 0, "pair": 1,
                          "dismissed": 0, "confirmation_required": 3 },
  "state_token": "sha256…"   // scope-opt と同じ PFV 指紋（office スコープ）
}
```

**POST /v2/propose-unblock/apply**
```jsonc
// Request: { office_id, iso_year, iso_week, plan: <simulate の plan をそのまま>, state_token }
// Response: { applied_moves: 1, inserted: true, warnings: [...] }
```
- state_token 不一致 409 / **1TX**: moves を scope apply の `_validate_and_move_one` 系で逐次適用
  （pfv_validator・明示 flush）→ 対象患者の PFV 挿入（2名体制は slot0+slot1 原子・W-12a の経路）→
  影響患者全員の reset_visits_to_fixed（pattern_and_week）→ commit。V2 pinned 違反は 422 全ロールバック
- 監査: AuditLog(action="propose_unblock_apply")（pool-bulk と同方式・op_log 非汚染）

## 3. FE（PoolCandidateList）

- 発動条件: 通常候補0件 かつ 除外理由に時間起因（no_gap / no_pair_slot / travel_shortage）を含む
  → 静かなボタン **「ずらせば入る手を探す」**（方式b の callout と並ぶ位置・アンバー系実値トークン）
- 結果表示: プランカード（上位5件）
  「**① 田中様**: 火 16:00 稲B → **火 15:30 稲B**（移動 +3分・希望範囲内）
   **② この枠に配置**: 火 16:00 稲B（＋相方: 稲C 16:00）
   — 合計 +5分/週・動くのは1名」
- 適用: カードの「この手順で配置する」→ 確認ダイアログ（**動く患者の一覧を明示**・
  「毎週の型が変わります。Ctrl+Z 対象外です」バナー・pool-bulk の見せる流儀）→ apply → 409 は再探索導線
- 0件のとき: unmovable_summary を「動かせない事情」として表示（例: ピン留め2件・確認が必要3件 —
  黙って諦めない）
- 訳語辞書・寛容パース・既存テスト流儀

## 4. テスト要点

- BE: 深さ1成立（ブロッカー1移動で開通）/ 深さ2（同一バケット2移動）/ pinned・locked・2名体制・
  同住所ペア・却下記憶・希望外が動かない / 2名体制ターゲットのペア開通 / 決定性 / ランキング順 /
  apply の 1TX・409・422 ロールバック・reset 波及 / 非対象患者の挙動不変
- FE: 発動条件（候補0＋時間起因のみ）/ プラン表示 / 確認ダイアログの患者明示 / apply 経路 / 409

## 4.5 最終ゴールまでのロードマップ（PO 指示 2026-07-05 — 一気通貫で完遂する）

| Wave | 内容 |
|---|---|
| **W-13a** | 「拠点を選択してから探索してください」の解消: unblock の office スコープを**対象患者の primary_office_id** から自動解決（ページの拠点フィルタに依存しない。未設定患者は W-6 の採用ガードと同じ案内） |
| **W-13b** | **操作感の統一 = 「変更されるコースの before/after 一覧を見て管理者が判断」**: ①スケジュール最適化・②改善提案は CourseMoveTimeline で対応済み（W3/c6e22fa 正典） ③個別配置提案（プール個別・ペア含む）= 採用確認パネルに対象コース（＋相方コース）の before/after タイムラインを表示（mini_schedule から FE 構築 — is_here 行を除いたものが before） ④一括投入 = BeforeAfterWeekPanel で対応済み（表記の整合のみ確認） ⑤**詰まり解消プランカード = 影響する全コース（移動元・移動先・配置先）の before/after を BE がスナップショットで返し、CourseMoveTimeline の正典部品で表示** |
| **W-14** | 一括投入→詰まり解消の橋渡し: bulk の投入不能（時間起因 no_gap 等）患者に「ずらせば入る手を探せます」導線 → 患者詳細を autoUnblock フラグつきで開き探索を自動実行（W-5b autoOvercapacity と同じ伝搬パターン。事前計算はしない — 重いため） |
| **W-15** | 定員起因への拡張: capacity_full でもブロッカーの**他コースへの退避**で定員を空ける手を探索（同一バケット退避は定員を空けないため対象外）。方式b（+1名相談）と並列表示し、管理者が「ずらす」か「+1名」かを選べる |

各 Wave = Opus 実装 → 独立レビュー → 修正 → コミット → デプロイ。思想の正典（§6 余白の原則）逸脱禁止。

## 5. 将来課題（v2 バックログ）

> **PO 記録（2026-07-05）**: 本機能の思想（余白の原則・予防/保全/救急の分業）は
> `docs/plans/schedule-advisor-design.md` §6 が正典（PO が「本ソフトの要」と明言）。
> 拡張候補は下記のうち「一括投入との橋渡し」「定員起因への拡張」の2件のみが PO 承認済み —
> **これ以外に適用範囲を広げない**（スケジュール最適化・改善提案への組み込みは意図的に非対象）。

- 玉突き連鎖（v の退避先が別の訪問を追い出す 3手チェーン）
- 週限定（B）の詰まり解消 / 定員起因への拡張（コース跨ぎ移動で定員を空ける — 方式b との合流）
- 「要確認の手も含める」トグル（scope-opt の同名バックログと同時に）
- 2名体制ターゲットで相方側ブロッカーも同時に動かす複合プラン
