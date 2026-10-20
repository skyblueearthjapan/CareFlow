import { redirect } from 'next/navigation';

/**
 * /m/shifts — 旧「出勤カレンダー」。スマホの「休み」ページ (/m/leave) にまとめたので転送する
 * (設計 leave-and-kinmu-sheet §6)。ブックマークや通知の古いリンクのために残す。
 */
export default function MobileShiftsRedirect(): never {
  redirect('/m/leave');
}
