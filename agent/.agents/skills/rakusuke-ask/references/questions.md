# よくある質問 48 問 → 使うコマンド

出典: `docs/plans/agent-skills/research/R3-data-guide.md` §4。日付は例（今日 = 2026-10-12（月）・週42 = 10/12〜10/18）。
書式: `rk get <path> -q k=v -q k=v`（クエリは `-q` を繰り返す）／`rk sql <名前> -p k=v`。空の引数は「指定なし」（`-p staff=`）。
`<pid>`・`<sid>` は `rk sql patient_lookup -p name=…`／`rk sql staff_lookup -p name=…` で得た `patient_id`／`staff_id`。

共通の注意（毎回）: 有効な訪問＝削除・取消を除く／【検証】を除く（SQL）・ダッシュボードは含む／2 名体制は 1 回／同行は件数に入れない／担当は主担当→コース担当で補う。

## A. 件数・予定

| # | 質問例 | まず使う | 補う・別の方法 | 注意 |
|---|---|---|---|---|
| 1 | 今日の訪問は何件？終わったのは？ | `rk get /dashboard/kpi`（today_visits・today_completed・today_unassigned・today_overlapping・this_week_*） | `rk sql visit_counts_by_day -p date_from=2026-10-12 -p date_to=2026-10-12` | kpi は検証用込み・行数・担当なしは主担当だけで判定。差があれば SQL の値と理由を併記 |
| 2 | 来週の土曜は何件？ | `rk sql visit_counts_by_day -p date_from=2026-10-24 -p date_to=2026-10-24` | `rk get /visits -q week_start=2026-10-24 -q week_end=2026-10-24 -q limit=500` | 「来週の土曜」= 10/24 か 10/17 か明記。0 件なら `rk sql visit_source_breakdown` 等で「週を作っていない」か確かめる |
| 3 | 週42 は全部で何件？曜日別は？ | `rk sql visit_counts_by_day -p date_from=2026-10-12 -p date_to=2026-10-18`（日 × 拠点を合計） | 表で見せる: `rk get /integrations/week-schedule -q weekStart=2026-10-12 -q weekEnd=2026-10-18` | ISO 週（月曜始まり） |
| 4 | 先週と比べて増えた？直近 30 日の推移 | `rk get /dashboard/trend -q days=30` | 週ごとの比較は `visit_counts_by_day` を 2 週分 | trend は行数ベース・検証用込み |
| 5 | 今月の訪問件数（全体・拠点別） | `rk sql visit_counts_by_day -p date_from=2026-10-01 -p date_to=2026-10-31`（拠点で合計） | — | 拠点＝コースの拠点、無ければ利用者の主担当拠点。先の日は予定 |
| 6 | 田中さんの今月の訪問数 | `rk get /dashboard/staff-performance -q from=2026-10-01 -q to=2026-10-31`（staff[].period.visits） | 予定込み・日別: `rk sql visits_by_staff_day -p date_from=2026-10-01 -p date_to=2026-10-31 -p staff=田中` | API は今日までで切る（先の予定は数えない）。同行は accompany_visits に別。名前の同定 |
| 7 | 田中さんの明日の予定（順番と時刻） | `rk get /monitor -q date=2026-10-13`（行＝職員・時刻順） | `rk get /visits -q week_start=2026-10-13 -q week_end=2026-10-13 -q staff_id=<sid>`＋同行 `rk get /accompaniments -q iso_year=2026 -q iso_week=42 -q staff_id=<sid>` | /visits の staff_id は主・副・旧メンターだけ（同行は入らない）。主担当が空でコース担当だけの訪問は /visits の staff_id で漏れる → `rk sql visits_valid` で補う |
| 8 | いちばん訪問が多い人は？1 日あたりは？ | `rk get /dashboard/staff-performance -q from=… -q to=…`（per_day・days） | `rk sql visits_by_staff_day` | 新人は担当 0（同行のみ） |
| 9 | 担当が決まっていない訪問は？ | `rk sql unassigned_visits -p date_from=2026-10-12 -p date_to=2026-10-31` | — | kpi の today_unassigned とずれる理由（主担当だけ見ている） |
| 10 | 時間が重なっている訪問は？ | `rk get /schedule/v2/feasibility-report -q iso_year=2026 -q iso_week=42 -q days=6`（移動・ゆとり・同住所も） | 重なりだけ: `rk sql staff_overlaps -p date_from=2026-10-12 -p date_to=2026-10-18` | SQL は移動を見ない。同じ建物の 2 名は重なりに見えても正常なことがある |
| 11 | この患者の次の訪問はいつ？ | `rk sql patient_next_visits -p patient=山本` | 週を作っていない先は `rk sql patient_fixed_visits -p patient=山本` | 今は日本時間で判定 |
| 12 | この患者の毎週の訪問（曜日・時刻・赤ピン） | `rk get /patients/<pid>/fixed-visits` | `rk sql patient_fixed_visits -p patient=山本` | mode=normal が通常週。locked＝🔴赤ピン |
| 13 | この患者の今月の訪問回数 | `rk sql visits_by_patient -p patient=山本 -p date_from=2026-10-01 -p date_to=2026-10-31`（status≠cancelled の group_key を数える） | — | 1 回 1 件。過去は実績（completed）と予定を分けて示す |
| 14 | コース A の担当は誰？ | `rk get /courses -q iso_year=2026 -q iso_week=42 -q weekday=0`（assigned_staff_id） | `rk get /schedule/v2/board -q iso_year=2026 -q iso_week=42` | course_status=proposed は案 |
| 15 | 週の健全性（未割当・定員超え・偏り） | `rk get /schedule/v2/schedule-health -q iso_year=2026 -q iso_week=42` | 日ごとの人手: `rk sql day_load_vs_staff -p date_from=2026-10-12 -p date_to=2026-10-18` | 診断は重なりを見つけない → #10 と併用 |
| 16 | 入らなかった訪問（保留プール）は？ | 特別訪問の○: `rk get /special-visit-marks/pool -q iso_year=2026 -q iso_week=42` | 近似の一覧: `rk sql pool_week -p monday=2026-10-12`（special＋希望回数に足りない方）。ならしで入らなかった訪問は runs/ の案の記録 | 「プール」の意味をまず確かめる。画面のプールは保存されず数えて出す |

## B. 実績・QR 打刻

| # | 質問例 | まず使う | 補う・別の方法 | 注意 |
|---|---|---|---|---|
| 17 | 山本様に昨日誰が何時に着いた？ | `rk get /visit-history -q from=2026-10-11 -q to=2026-10-11 -q q=山本`（または `-q patient_id=<pid>`） | `rk sql checkin_arrivals -p date_from=2026-10-11 -p date_to=2026-10-11 -p patient=山本 -p staff=` | 実績時刻は合わせた後の値。SQL は近似（再送・圏外遅延の細則なし）→ API を正 |
| 18 | 今日の進み具合（今どこ・遅れ・未訪問） | `rk get /monitor -q date=2026-10-12` | — | phase・alert_level・arrival_delay_min。確認済みはアラートが消える |
| 19 | QR 打刻率は？（人別・全体） | `rk get /dashboard/staff-performance -q from=… -q to=…`（qr_ratio＝到着と退出が揃った訪問 ÷ 訪問） | 到着の率: `rk get /visit-history …` の summary（with_arrival / visits）。人別の内訳: `rk sql checkin_rate_by_staff -p date_from=… -p date_to=…` | **定義を必ず書く**（揃った率か到着率か・終わった予定だけ・手入力/サイン込みか） |
| 20 | 退出を記録し忘れた訪問は？ | `rk get /visit-history -q from=… -q to=… -q state=nodep` | — | 前日以前で到着のみ |
| 21 | 打刻が全く無い訪問は？ | `rk get /visit-history -q from=… -q to=… -q state=none` | — | 未来の予定は数えない |
| 22 | 担当外の人が行った（代行）・予定外の訪問は？ | `rk get /visit-history -q from=… -q to=… -q state=special` | — | 代行＝到着を読んだ人が担当の集合（主・副・割当・同行）の外 |
| 23 | 時刻を合わせた訪問とその理由は？ | `rk get /visit-history -q from=… -q to=… -q state=adjusted` | 理由別の件数: `rk sql time_adjustment_reasons -p date_from=… -p date_to=…` | 「直した」でなく「合わせた」と書く |
| 24 | 平均の滞在時間は？予定より長い？ | `rk get /dashboard/staff-performance …`（actual_min・plan_min） | — | 揃った訪問が 5 件未満なら null |
| 25 | 移動距離・1 日の内訳 | 同上（km_per_day・travel_min_per_day・idle_min_per_day） | — | 直線距離。座標の無い区間は skipped_legs |
| 26 | 場所が違う打刻は？ | `rk get /monitor -q date=…`（alert_level） | 期間でまとめて: `rk sql checkin_location_alerts -p date_from=… -p date_to=…` | GPS が無い打刻は no_gps |
| 27 | 遅れて届いた打刻（圏外）は？ | `rk get /visit-history -q from=… -q to=… -q state=late` | — | 72 時間超は採らない |
| 28 | サインで記録した訪問は？ | `rk sql checkin_arrivals -p date_from=… -p date_to=… -p patient= -p staff=`（departure_source='signature' の行） | — | 画像そのものは渡さない |
| 29 | この患者の最近の様子（音声記録の要約） | `rk get /visit-recordings -q patient_id=<pid> -q from=… -q to=…` → summary_text。詳細 `rk get /visit-recordings/<id>` | — | 健康の情報。聞かれたことだけ最小限 |
| 30 | 月の打刻記録を印刷したい | `rk get /visit-history/report -q from=2026-10-01 -q to=2026-10-31 -q group=staff`（A4 HTML） | Excel: `/visit-history/export` | 92 日まで。保存先は `docs/reports/agent/`。出力は監査記録に残る |

## C. 患者マスター

| # | 質問例 | まず使う | 補う・別の方法 | 注意 |
|---|---|---|---|---|
| 31 | 入院中の患者は？いつから？ | `rk sql patients_by_status -p status=admitted` | 全状態: `-p status=`（稼働中以外すべて） | since_jst が空＝記録の始まる前で不明。`/patients` には絞り込みが無い |
| 32 | 稼働中の患者は何人？拠点別は？ | `rk sql patient_counts_by_office` | — | 開始前（pending）は稼働中に数えない。検証用を除く |
| 33 | 今月終了した患者は？ | `rk sql patients_by_status -p status=cancelled`（since_jst が今月のもの） | — | 「解約済み」＝終了。planned_from_today が 0 でなければ予定が残っている（点検） |
| 34 | 特別訪問週間中の患者は？ | `rk sql special_periods_active -p on_date=2026-10-12` | `rk get /special-visit-periods`・`rk get /special-visit-periods/<id>/calendar` | active でも期間外がありうる。旧・置換型の特別週は別物 |
| 35 | この患者の NG 職員は？ | `rk get /patients/<pid>/ng-staff`（逆引き `rk get /staff/<sid>/ng-patients`） | `rk sql patient_constraints -p patient=山本` | 理由メモは内部用 |
| 36 | 女性限定・2 名必須の患者は？ | `rk sql patient_constraints -p patient=` | — | sex_restriction の値は `female_only` |
| 37 | 同じ建物の患者は？ | `rk get /patients/<pid>/same-address-candidates`・`rk get /patient-same-address-links` | 件数だけ: `patient_constraints` の same_address_links | リンクの無い組は「なるべく同時」扱い |

## D. 職員・休み

| # | 質問例 | まず使う | 補う・別の方法 | 注意 |
|---|---|---|---|---|
| 38 | 今週休みの人は？ | `rk get /staff/overrides-week -q iso_year=2026 -q iso_week=42` | `rk sql staff_week_overrides -p monday=2026-10-12`（日付つき・種類の日本語 `type_ja`・終日か `full_day_off`・入れた元 `source`） | 種類は公休・午前休（13:00 から勤務）・午後休（12:30 から休み）・有給。終日の休みは公休と有給。申請中は入らない（#39） |
| 39 | 未承認の休み申請は？ | `rk get /pending-requests -q status=pending -q request_type=staff_off` | `rk sql pending_leave_requests -p request_type=staff_off`（種類 `leave_type`・変更のお願い `is_change`・理由 `note`） | 1 日 = 1 件。締め切り（前の月の 20 日・日本時間）の後は「変更のお願い」。`cancel` は休みを取り消してほしいというお願い |
| 40 | ◯月の出勤カレンダーは確定した？ | `rk get /staff/<sid>/shift-confirmations` | — | 再確定は同じ行の更新 |
| 41 | 田中さんの会議・研修の予定は？ | `rk get /staff/<sid>/events -q from=2026-10-12 -q to=2026-10-18` | 週の全員: `rk sql staff_week_events -p monday=2026-10-12` | 時刻は壁時計（UTC として保存）。cancelled は「今週だけ外した」 |
| 42 | 新人の同行は今週どこ？ | `rk get /accompaniments -q iso_year=2026 -q iso_week=42 -q staff_id=<sid>` | `rk sql accompaniments_week -p monday=2026-10-12` | course リンクはそのコースの全訪問 |

## E. カイポケ連携・記録

| # | 質問例 | まず使う | 補う・別の方法 | 注意 |
|---|---|---|---|---|
| 43 | カイポケとずれている訪問は？（週） | `echo '{"weekStart":"2026-10-19"}' \| rk post-read /integrations/unsent-summary --json -`（●未送信・items・sendable_count・past_count・snapshot.fetched_at） | 一覧: `rk get /integrations/reconcile-report -q weekStart=2026-10-19 -q format=json`。写しの鮮度: `rk sql kaipoke_snapshot_latest` | **写しの時刻を必ず言う**。月またぎ週は空。担当なし行は export に出ない。取り直しはロボット → 了承 |
| 44 | ◯月の予定と実績の食い違いは？ | `rk get /integrations/plan-actual-report -q month=2026-10 -q format=json` | — | 写しが無ければ 404（取得はロボット → 了承） |
| 45 | 最後にカイポケへ送ったのはいつ？結果は？ | `rk get /integrations/kaipoke/jobs` → `rk get /integrations/kaipoke/jobs/<id>/report` | `rk sql kaipoke_jobs_recent -p days=14`（op=apply の行） | unknown＝要目視 |
| 46 | 取り込む前に戻せる？ | `rk get /integrations/inbound-snapshots -q weekStart=2026-10-12` | — | 打刻のある週は戻せない。戻す操作は既定で禁止（PO の指示が要る） |
| 47 | 誰がこの訪問を動かした？ | `rk get /schedule/v2/op-log/state`（盤面操作の日本語ラベル） | `rk get /audit-logs -q since=2026-10-12T00:00:00+09:00 -q path=visits`・`rk sql audit_recent_writes -p days=3 -p path_like=visits` | 本文（request_body）は出さない |
| 48 | 自分あての通知は？ | `rk get /notifications -q unread_only=true` | — | rk のアカウント（エージェント）宛ての分だけ。人の通知は見えない |

## 名前つき SQL だけで答える問い（API が無い）
- 日ごとの人手の過不足 → `day_load_vs_staff`
- 型と今週の食い違い → `master_vs_week_diff -p monday=…`
- 希望の回数と枠の数が違う方 → `preference_vs_fixed_count`
- 取消の内訳（今週だけ取消／非稼働化）→ `visit_source_breakdown`
- 終了・入院なのに予定が残っている方 → `patients_by_status -p status=`（planned_from_today > 0）

## 答えられない問い（正直に言う）
- 職員ごとの「取消・変更の数」: 記録の仕組みが無い（保留中）。
- カイポケの写しより後にお客様がカイポケで直した分: 写しを取り直すまで見えない。
- 利用者の連絡先: マスターに無い。
