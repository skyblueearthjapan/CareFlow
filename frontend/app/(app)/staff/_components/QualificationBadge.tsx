/**
 * 資格バッジ (K-1b カイポケ「職種」列)。一覧と詳細で共有する。
 *
 * 未設定は警告色で「資格未設定」を出す — カイポケのサービス内容
 * (正看 / 准看) が資格から決まるため、空欄のままだと准看護師の訪問が
 * 「正看」で送られて偽差分になる (kaipoke-service-content-design.md §1-2 / §4)。
 */
import { qualificationLabel, type StaffRead } from '@/lib/schemas/staff';

export function QualificationBadge({
  qualification,
}: {
  qualification: StaffRead['qualification'] | string | null | undefined;
}) {
  const unset = !qualification;
  return (
    <span
      data-testid="staff-qualification-badge"
      className={
        unset
          ? 'inline-flex items-center rounded bg-warning-bg px-1.5 py-0.5 text-xs font-semibold text-warning-strong'
          : 'inline-flex items-center rounded bg-bg-muted px-1.5 py-0.5 text-xs text-text-secondary'
      }
    >
      {qualificationLabel(qualification)}
    </span>
  );
}
