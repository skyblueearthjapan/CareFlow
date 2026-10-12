'use client';

/** 作業場のダイアログ: 最適化・もしも・見比べ・計算の前提・登録（出口 2 つ）。言葉は落ち着いて、「合わせる・調整」。 */
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { useCommitPreview, useCompare, useInputs, useWeekPreview } from '@/lib/queries/templateWs';
import { WEEKDAYS, estimateSeconds, fmtMin } from '@/lib/templateWs/helpers';
import type { DraftSummary, DraftView, OfficeRef } from '@/lib/templateWs/types';

const WD_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat'] as const;

/** 秒 → 「40 秒」「2 分」「2 分 30 秒」。 */
export function fmtDuration(sec: number): string {
  if (sec < 60) return `${sec} 秒`;
  const m = Math.floor(sec / 60);
  const r = sec % 60;
  return r ? `${m} 分 ${r} 秒` : `${m} 分`;
}

// ---------------------------------------------------------------------------
// 最適化
// ---------------------------------------------------------------------------
export function OptimiseDialog({
  open,
  offices = 1,
  days = 6,
  onClose,
  onRun,
}: {
  open: boolean;
  /** 下書きの拠点の数（全拠点なら 2 など）と、営業日の数。かかる時間の見込みに使う。 */
  offices?: number;
  days?: number;
  onClose: () => void;
  onRun: (weekdays: string[] | null, seconds: number) => void;
}) {
  const [scope, setScope] = useState<'all' | (typeof WD_KEYS)[number]>('all');
  const [seconds, setSeconds] = useState(20);
  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent className="max-w-md" data-testid="optimise-dialog">
        <DialogHeader>
          <DialogTitle>最適化</DialogTitle>
          <DialogDescription>
            コースと、希望の範囲の中の時刻を合わせ直します。曜日は動かしません。赤ピンの枠は曜日・時刻を守り、手で動かした枠もそのままです。
          </DialogDescription>
        </DialogHeader>
        <label className="flex items-center gap-2 text-sm">
          範囲
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value as typeof scope)}
            className="rounded border border-border-default bg-bg-base px-2 py-1"
            aria-label="範囲"
          >
            <option value="all">全部の曜日</option>
            {WD_KEYS.map((k, i) => (
              <option key={k} value={k}>
                {WEEKDAYS[i]}曜だけ
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          1 曜日あたりの計算時間（秒）
          <input
            type="number"
            min={1}
            max={60}
            value={seconds}
            onChange={(e) => setSeconds(Math.max(1, Math.min(60, Number(e.target.value) || 20)))}
            className="w-20 rounded border border-border-default bg-bg-base px-2 py-1"
            aria-label="計算時間（秒）"
          />
        </label>
        <p className="text-sm text-text-secondary" data-testid="optimise-estimate">
          かかる時間の見込み: 約 {fmtDuration(estimateSeconds(seconds, offices, scope === 'all' ? days : 1))}
          {offices > 1 ? `（${offices} 拠点を拠点ごとに解くので、1 曜日あたりの時間を拠点の数で分けます。全体は長くても 3 分ほど）` : ''}
        </p>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            やめる
          </Button>
          <Button
            type="button"
            onClick={() => onRun(scope === 'all' ? null : [scope], seconds)}
            data-testid="optimise-run"
          >
            最適化する
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// もしも
// ---------------------------------------------------------------------------
export function WhatifDialog({
  open,
  view,
  staff,
  onClose,
  onApply,
}: {
  open: boolean;
  view: DraftView;
  staff: { code: string; name: string }[];
  onClose: () => void;
  onApply: (patch: { staff_count?: Record<string, number | null>; drop_staff?: string[]; clear?: boolean }) => void;
}) {
  const initial = Object.fromEntries(
    view.days.map((d) => [
      d.weekday,
      String(view.scenario.staff_count[String(d.weekday)] ?? d.courses.filter((c) => !c.virtual).length),
    ]),
  );
  const [counts, setCounts] = useState<Record<number, string>>(initial);
  const [drop, setDrop] = useState<string[]>(view.scenario.drop_staff);
  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent className="max-w-lg" data-testid="whatif-dialog">
        <DialogHeader>
          <DialogTitle>もしも</DialogTitle>
          <DialogDescription>
            職員の人数を変えたり、この人がいないとしたら、を試します。条件を変えたあとで最適化すると、並べ直されます。
          </DialogDescription>
        </DialogHeader>
        {view.all_offices ? (
          <p className="text-sm text-text-secondary" data-testid="whatif-all-offices-note">
            全拠点の下書きでは、曜日ごとの職員の人数のもしもは使えません（拠点ごとの下書きで試せます）。「この人がいないとしたら」は使えます。
          </p>
        ) : null}
        <div className={`grid grid-cols-3 gap-2 text-sm ${view.all_offices ? 'hidden' : ''}`}>
          {view.days.map((d) => (
            <label key={d.weekday} className="flex items-center gap-1">
              {d.name}曜
              <input
                type="number"
                min={1}
                max={20}
                value={counts[d.weekday] ?? ''}
                onChange={(e) => setCounts({ ...counts, [d.weekday]: e.target.value })}
                className="w-14 rounded border border-border-default bg-bg-base px-1.5 py-1"
                aria-label={`${d.name}曜の職員の人数`}
              />
              人
            </label>
          ))}
        </div>
        <fieldset className="space-y-1 text-sm">
          <legend className="text-xs font-semibold">この人がいないとしたら</legend>
          <div className="flex flex-wrap gap-2">
            {staff.map((s) => (
              <label key={s.code} className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={drop.includes(s.code)}
                  onChange={(e) =>
                    setDrop(e.target.checked ? [...drop, s.code] : drop.filter((c) => c !== s.code))
                  }
                />
                {s.name}
              </label>
            ))}
          </div>
        </fieldset>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onApply({ clear: true })}>
            もしもをすべて外す
          </Button>
          <Button
            type="button"
            onClick={() => {
              const staff_count: Record<string, number | null> = {};
              for (const d of view.all_offices ? [] : view.days) {
                const normal = d.courses.filter((c) => !c.virtual).length;
                const n = Number(counts[d.weekday]);
                const was = view.scenario.staff_count[String(d.weekday)];
                if (Number.isFinite(n) && n >= 1 && n !== (was ?? normal)) staff_count[String(d.weekday)] = n;
              }
              onApply({ staff_count, drop_staff: drop });
            }}
            data-testid="whatif-apply"
          >
            この条件にする
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 見比べ
// ---------------------------------------------------------------------------
export function CompareDialog({
  open,
  draft,
  others,
  onClose,
  onShowOnBoard,
}: {
  open: boolean;
  draft: DraftView;
  others: DraftSummary[];
  onClose: () => void;
  onShowOnBoard: (otherId: string | null) => void;
}) {
  const [other, setOther] = useState<string>('master');
  const q = useCompare(open ? draft.id : null, open ? other : null);
  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent className="max-w-2xl" data-testid="compare-dialog">
        <DialogHeader>
          <DialogTitle>見比べ</DialogTitle>
          <DialogDescription>この下書きを、いまの型か別の下書きと並べます。</DialogDescription>
        </DialogHeader>
        <label className="flex items-center gap-2 text-sm">
          比べる相手
          <select
            value={other}
            onChange={(e) => setOther(e.target.value)}
            className="rounded border border-border-default bg-bg-base px-2 py-1"
            aria-label="比べる相手"
          >
            <option value="master">いまの型（マスター）</option>
            {others
              .filter((d) => d.id !== draft.id)
              .map((d) => (
                <option key={d.id} value={d.id}>
                  下書き: {d.name}
                </option>
              ))}
          </select>
        </label>
        {q.isLoading ? <p className="text-sm text-text-muted">読み込み中…</p> : null}
        {q.error ? <p className="text-sm text-error">{apiErrorMessage(q.error)}</p> : null}
        {q.data ? (
          <div className="max-h-80 space-y-3 overflow-auto text-xs">
            <table className="w-full">
              <thead>
                <tr className="text-left text-text-muted">
                  <th>曜日</th>
                  <th>違う枠</th>
                  <th>間に合わない組</th>
                  <th>上限超え</th>
                  <th>コースの無い枠</th>
                  <th>移動（分）</th>
                </tr>
              </thead>
              <tbody>
                {q.data.days.map((d) => (
                  <tr key={d.weekday} className="tnum">
                    <td>{d.weekday}</td>
                    <td>{d.differ}</td>
                    <td>{d.b.conflicts} → {d.a.conflicts}</td>
                    <td>{d.b.over_cap} → {d.a.over_cap}</td>
                    <td>{d.b.no_course} → {d.a.no_course}</td>
                    <td>{d.b.travel_min} → {d.a.travel_min}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-text-muted">左が {q.data.b}・右が {q.data.a}</p>
            <ul className="space-y-0.5">
              {q.data.changed.slice(0, 40).map((c) => (
                <li key={c.key}>
                  {c.weekday} {c.patient}
                  {c.office && draft.all_offices ? `（${c.office}）` : ''}
                  {c.red ? '（赤ピン）' : ''}：{c.b.course ?? '—'} {c.b.start ?? ''} → {c.a.course ?? '—'} {c.a.start ?? ''}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onShowOnBoard(other === 'master' ? null : other)}
            data-testid="compare-show"
          >
            盤面でも違いを桃色で見る
          </Button>
          <Button type="button" onClick={onClose}>
            閉じる
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 計算の前提（最適化が使うものを、そのまま見せる）
// ---------------------------------------------------------------------------
export function InputsDialog({
  open,
  draftId,
  offices = [],
  onClose,
}: {
  open: boolean;
  draftId: string;
  /** 全拠点の下書きのとき、前提を見る拠点を選ぶ。 */
  offices?: OfficeRef[];
  onClose: () => void;
}) {
  const [wd, setWd] = useState(0);
  const [office, setOffice] = useState<string | null>(offices[0]?.id ?? null);
  const q = useInputs(draftId, wd, open, offices.length > 1 ? office : null);
  const d = q.data;
  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent className="max-w-3xl" data-testid="inputs-dialog">
        <DialogHeader>
          <DialogTitle>計算の前提</DialogTitle>
          <DialogDescription>
            最適化が使う距離・職員の勤務と予定・時刻の決まり方です。職員マスタと患者マスタの値をそのまま使います。
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap items-center gap-1">
          {offices.length > 1 ? (
            <select
              value={office ?? ''}
              onChange={(e) => setOffice(e.target.value)}
              className="mr-2 h-9 rounded border border-border-default bg-bg-base px-2 text-sm"
              aria-label="前提を見る拠点"
            >
              {offices.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          ) : null}
          {WEEKDAYS.map((w, i) => (
            <Button key={w} type="button" size="sm" variant={i === wd ? 'default' : 'outline'} onClick={() => setWd(i)}>
              {w}
            </Button>
          ))}
        </div>
        {q.isLoading ? <p className="text-sm text-text-muted">読み込み中…</p> : null}
        {d ? (
          <div className="max-h-96 space-y-3 overflow-auto text-xs">
            <section>
              <h4 className="font-semibold">決まり</h4>
              <ul className="list-disc pl-4 text-text-secondary">
                <li>移動は直線距離を時速 {d.rules.speed_kmh}km で見積もり、別の家へ移るたびに {d.rules.buffer_min} 分足します（約 {d.rules.same_address_m}m 以内は同じ建物で移動 0）。</li>
                <li>昼休みは {fmtMin(d.rules.lunch_window[0])}〜{fmtMin(d.rules.lunch_window[1])} の中で {d.rules.lunch_min} 分は必ず（なるべく {d.rules.lunch_target} 分）。予定の前後 {d.rules.event_buffer_min} 分は入れません。</li>
                <li>1 コース 1 日 {d.rules.cap_regular} 名まで（マネージャーのコースは受け皿）。</li>
                <li>{d.rules.time_order}</li>
              </ul>
            </section>
            <section>
              <h4 className="font-semibold">この曜日のコースと職員</h4>
              <ul>
                {d.courses.map((c) => (
                  <li key={c.id}>
                    {c.label}：{c.staff}さん　勤務 {fmtMin(c.shift[0])}〜{fmtMin(c.shift[1])}
                    {c.events.length ? `　予定 ${c.events.map(([a, b]) => `${fmtMin(a)}〜${fmtMin(b)}`).join('・')}` : ''}
                  </li>
                ))}
              </ul>
              {d.off_staff.length ? <p className="text-text-muted">お休み（勤務の型が休み）：{d.off_staff.join('・')}</p> : null}
            </section>
            <section>
              <h4 className="font-semibold">枠ごとの時刻の決まり方</h4>
              <table className="w-full">
                <tbody>
                  {d.slots.map((s) => (
                    <tr key={s.key} className="border-t border-border-subtle">
                      <td className="py-0.5">{s.patient}</td>
                      <td className="tnum">{fmtMin(s.start)}</td>
                      <td>
                        {s.rule === 'red'
                          ? '赤ピン：曜日と時刻を守る'
                          : s.rule === 'fixed'
                            ? '今の時刻のまま'
                            : `今の時刻を第一に、だめなら ${fmtMin(s.win[0])}〜${fmtMin(s.win[1])} の中`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
            <section>
              <h4 className="font-semibold">患者どうしの移動時間（分）</h4>
              <div className="overflow-auto">
                <table className="tnum text-[10px]" data-testid="travel-matrix">
                  <thead>
                    <tr>
                      <th />
                      {d.matrix.patients.map((p, i) => (
                        <th key={i} className="px-1 font-normal">{p.name.slice(0, 3)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {d.matrix.minutes.map((row, i) => (
                      <tr key={i}>
                        <th className="pr-1 text-left font-normal">{d.matrix.patients[i]?.name.slice(0, 3)}</th>
                        {row.map((m, j) => (
                          <td key={j} className="px-1 text-right">{m}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        ) : null}
        <DialogFooter>
          <Button type="button" onClick={onClose}>閉じる</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 登録（出口 2 つ）
// ---------------------------------------------------------------------------
export function CommitDialog({
  open,
  draftId,
  version,
  isoYear,
  isoWeek,
  onClose,
  onCommit,
  onWeekApply,
  busy,
}: {
  open: boolean;
  draftId: string;
  version: number;
  isoYear: number;
  isoWeek: number;
  onClose: () => void;
  onCommit: () => void;
  onWeekApply: (weekHash: string) => void;
  busy: boolean;
}) {
  const [exit, setExit] = useState<'master' | 'week'>('master');
  const [year, setYear] = useState(isoYear);
  const [week, setWeek] = useState(isoWeek);
  const prev = useCommitPreview(draftId, open && exit === 'master');
  const wk = useWeekPreview(draftId, year, week, open && exit === 'week');
  const p = prev.data;
  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent className="max-w-2xl" data-testid="commit-dialog">
        <DialogHeader>
          <DialogTitle>この下書きを使う</DialogTitle>
          <DialogDescription>出口は 2 つあります。どちらも、内容を確かめてから登録します。</DialogDescription>
        </DialogHeader>
        <div className="flex gap-2" role="tablist">
          <Button type="button" size="sm" variant={exit === 'master' ? 'default' : 'outline'} onClick={() => setExit('master')} data-testid="exit-master">
            ① 固定訪問（型）に登録
          </Button>
          <Button type="button" size="sm" variant={exit === 'week' ? 'default' : 'outline'} onClick={() => setExit('week')} data-testid="exit-week">
            ② この週のコースに入れる
          </Button>
        </div>

        {exit === 'master' ? (
          <div className="max-h-80 space-y-2 overflow-auto text-sm" data-testid="commit-master">
            {prev.isLoading ? <p className="text-text-muted">確かめています…</p> : null}
            {prev.error ? <p className="text-error">{apiErrorMessage(prev.error)}</p> : null}
            {p ? (
              <>
                <p data-testid="commit-counts">
                  患者 {p.counts.patients} 名の固定訪問を合わせます（コースが変わる枠 {p.counts.course}・時刻が変わる枠 {p.counts.time}・新しい枠 {p.counts.new}）。
                </p>
                <p className="text-text-secondary">登録すると、次に「週を生成」した週から反映されます。すでにある週の予定は変わりません。</p>
                {p.blockers.map((b) => (
                  <p key={b} className="rounded bg-warning-bg px-2 py-1 text-warning-strong">まだ登録できません：{b}</p>
                ))}
                {p.warnings.map((b) => (
                  <p key={b} className="text-xs text-text-muted">{b}</p>
                ))}
                <ul className="space-y-0.5 text-xs">
                  {p.patients.map((pt) => (
                    <li key={pt.patient_id}>
                      {pt.name}：
                      {pt.changes.map((c) => `${c.weekday} ${c.new ? '新しい枠' : `${c.from_start}→${c.to_start}`} ${c.from_course}→${c.to_course}`).join('、')}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>
        ) : (
          <div className="max-h-80 space-y-2 overflow-auto text-sm" data-testid="commit-week">
            <div className="flex items-center gap-2">
              <input type="number" value={year} onChange={(e) => setYear(Number(e.target.value))} className="w-20 rounded border border-border-default px-1.5 py-1" aria-label="年" />
              年 第
              <input type="number" min={1} max={53} value={week} onChange={(e) => setWeek(Number(e.target.value))} className="w-16 rounded border border-border-default px-1.5 py-1" aria-label="週" />
              週
            </div>
            <p className="text-text-secondary">選んだ 1 週だけを合わせます。型（固定訪問）は変わりません。アプリの「戻る」で元に戻せます。</p>
            {wk.isLoading ? <p className="text-text-muted">確かめています…</p> : null}
            {wk.error ? <p className="text-error" data-testid="week-error">{apiErrorMessage(wk.error)}</p> : null}
            {wk.data ? (
              <>
                <p data-testid="week-counts">
                  コースが変わる訪問 {wk.data.counts.course}・時刻が変わる訪問 {wk.data.counts.time}・今週だけ足す訪問 {wk.data.counts.add}・その週に無くて入れられない枠 {wk.data.counts.skipped}
                </p>
                {wk.data.course_notes.map((n, i) => (
                  <p key={i} className="text-xs text-text-muted">{n.weekday}曜 {n.course}：{n.note}</p>
                ))}
                {wk.data.skipped.length ? (
                  <ul className="list-disc pl-4 text-xs text-text-secondary">
                    {wk.data.skipped.map((s, i) => (
                      <li key={i}>{s.weekday}曜 {s.patient}：{s.reason}</li>
                    ))}
                  </ul>
                ) : null}
              </>
            ) : null}
          </div>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>やめる</Button>
          {exit === 'master' ? (
            <Button type="button" disabled={busy || !p?.ready} onClick={onCommit} data-testid="commit-run">
              型に登録する
            </Button>
          ) : (
            <Button
              type="button"
              disabled={busy || !wk.data || wk.data.counts.course + wk.data.counts.time + wk.data.counts.add === 0}
              onClick={() => wk.data && onWeekApply(wk.data.week_hash)}
              data-testid="week-run"
            >
              この週のコースに入れる
            </Button>
          )}
        </DialogFooter>
        <span className="sr-only" data-testid="commit-version">{version}</span>
      </DialogContent>
    </Dialog>
  );
}
