# 終了にした方の予定と固定訪問の枠を自動で消す（設計 2026-10-09）

PO の希望: 患者様を「終了」にしたら、固定訪問の枠と今後の予定が自動で消えてほしい。

## 1. 調べたこと

- 画面の「終了」は内部の `cancelled`（表示は「解約済み」）。他は `suspended`（一時休止）/ `admitted`（入院中）/ `pending`（開始前）/ `active`（稼働中）。
- 今の動き（`patient_status_sync.apply_status_change`）
  - 稼働中 -> 稼働中以外: 今日（または明日）以降の「予定」を取消にする（`status_cancel`・行は残す）。打刻済み・訪問中・青ピン・過去日は対象外。
  - 固定訪問の枠（`patient_fixed_visits`）は残す。取消した行も残る。
  - 稼働中に戻すと、`status_cancel` の行を消して、固定訪問の枠から予定を作り直す。
  - 未処理の申請は自動で却下。管理者へお知らせ。盤面の「戻る」は取消には効かない（BE 決定済み）。
- 休止・入院は「戻る」前提なので、消してはいけない（枠が無いと復帰で作り直せない）。だから消すのは「終了」だけ。
- 影響の確認:
  - カイポケ CSV: 取消と消した行は同じ扱い（どちらも送らない）なので差分は変わらない。
  - 週の生成: 稼働中以外の患者様は元から対象外。プール: 稼働中以外は出てこない。
  - 担当（visit_staff_assignments）: 消す予定の分は外す（既存の復帰処理と同じ）。
  - 同行: 予定の行を消す（soft-delete）だけで、同行の行は触らない。他の取消経路と同じ。
  - 打刻済み・過去日: そもそも取消の対象外なので、消す対象にも入らない。

## 2. 決めたこと

- 「終了」にするときだけ、次の 2 つを行う。**ダイアログから `remove_schedule=true` を明示したときだけ**動く。省略・false は従来どおり（取消だけ・枠は残す）。
  - 画面の保存（PATCH /patients）・申請の承認・Excel 取込などダイアログを通らない経路は、省略扱いなので消さない。
  1. 取消した今後の予定を soft-delete する（すでに取消済みの `status_cancel` も含む）。手で取消した分（`manual_cancel`）・青ピン・過去日・打刻済み・開始前の日付（from_date より前）は触らない。
     - 起点が今日のとき、今日の開始時刻をもう過ぎた予定は消さない（実際に行われた可能性があるため）。件数表示も同じ規則で数える。
     - 消した予定を指す特別訪問週間の ● は取消にする（残すと ⭐ の自己回復で ○ に戻って出てくるため）。
  2. 固定訪問の枠を全部消す（通常・特別の両方）。
- 枠は物理削除するが、消す前の中身（曜日・時刻・分数・コース・可動域など）を `audit_logs`（action = `patient_end_rm_pfv`・`before.rows`）に控える。手で入れ直せる。
- ダイアログ: **稼働中・休止・入院・開始前のどこから終了にするときも**同じ確認を出す。「予定と固定訪問の枠も消す（終了の方はおすすめ）」のチェックは既定 ON で、消える予定の件数と枠の行数を出す。画面は ON/OFF を必ず `remove_schedule` に明示して送る。
- 件数の表示（status-impact）は、ダイアログの既定（ON）で数える。`remove_schedule=false` を付けたときだけ 0。

## 3. 変更したところ

- BE: `schemas/patient_status.py`（要求 `remove_schedule`、影響 `remove_schedule` / `removable_visits`、結果 `removed_visit_count` / `removed_fixed_visit_rows`）、`services/patient_status_sync.py`（`resolve_remove_schedule`・`_remove_schedule`）、`api/v1/patients.py`（status-impact のクエリ `remove_schedule`、status-change に引き渡し）。
- FE: `lib/schemas/patientStatus.ts`、`components/patients/PatientStatusChangeDialog.tsx`、`lib/hooks/usePatientStatusGate.ts`（終了への変更は常に確認・トースト文言）。
- テスト: `backend/tests/test_patient_status_sync.py` に追加、FE のダイアログ・ゲート・トースト。

## 4. 気をつけること

- 消した予定と枠は、稼働中に戻しても自動では復活しない（ダイアログにも書いた）。入れ直しは `audit_logs` の控えを見て手で行う。
- 終了にしたあとカイポケの週間パターンを止める作業は今までどおり（お知らせにも書いてある）。
- 特別訪問週間は「残す」が既定のまま。終了のときに「終了する」を選ぶかは今までどおり管理者の判断。
