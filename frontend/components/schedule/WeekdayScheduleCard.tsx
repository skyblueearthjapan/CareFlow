'use client';

/**
 * WeekdayScheduleCard — 共通コンポーネント (2026-W20):
 * 曜日 × コース の visit 一覧を Before/After 方式で描画する共通コンポーネント。
 *
 * 用途:
 *   - FullOptimizeDialog の `CourseListColumn` (Before / After 2 ペイン)
 *   - /schedule の 月-土タブの「リスト表示モード」 (Before/After 形式統一)
 *
 * 統一する情報 (4 項目, ユーザー要件):
 *   (a) 個別条件: time_type + preferred_start/end (例: "🕐 固定 (10:00)")
 *   (b) 同住所セット (ペアリング): same_address_group_id で同 id を黄色枠 + 📍
 *   (c) 次の目的地までの距離: 行右端に `{km}km` 表示 (VisitArrow)
 *   (d) 住所詳細: 18 文字省略 + title 属性に full address
 *
 * DRY:
 *   旧 `BeforeAfterWeekPanel.CourseListColumn` の inline 描画を本コンポーネントに
 *   集約。/schedule のリスト表示モードも同じ視覚言語で出力する。
 */
import * as React from 'react';

import { cn } from '@/lib/utils';
import { VisitArrow } from './v2/VisitArrow';
import { trimSeconds } from './v2/_autoScheduleUtils';

// ─────────────────────────────────────────────────────────────────────────
// Types — 共通 visit / course データ型 (V2VisitForUI と互換)
// ─────────────────────────────────────────────────────────────────────────

/**
 * 1 visit の表示用データ. V2VisitForUI のサブセット + /schedule 側で
 * 構築する patient/visit ハイブリッド型と互換になるよう缓和.
 */
export interface VisitListItem {
  /** ユニーク key (visit_id or patient_id+slot 等). */
  key: string;
  /** "HH:MM" or "HH:MM:SS". 表示時は trimSeconds 適用. */
  start_time: string;
  patient_name: string;
  /** 例: "稲毛区小仲台 7-12-2-407" / null. */
  address?: string | null;
  /** 町レベルのエリアラベル. 任意. */
  area_label?: string | null;
  /** "固定" / "時間帯" / "午前" / "午後" / "終日". */
  time_type?: string | null;
  preferred_start?: string | null;
  preferred_end?: string | null;
  /** "female_only" / "male_only" / null. */
  sex_restriction?: string | null;
  /** 同住所グループの id (同 id を持つ visit を黄色枠でまとめる). */
  same_address_group_id?: string | null;
  /** 次の visit までの直線距離 (km). null = 最後尾. */
  distance_to_next_km?: number | null;
}

/** 1 コース分のサマリ. */
export interface CourseListItem {
  /** ユニーク key (office_id+code+staff_id 等). */
  key: string;
  /** "本店 A コース" のような表示ラベル. */
  title: string;
  /** "{visits_count}件 / {distance}km" の右端 summary. 任意. */
  summary?: string | null;
  /** 描画する visit 群. start_time 昇順で並べておくこと. */
  visits: VisitListItem[];
}

export interface WeekdayScheduleCardProps {
  /** ヘッダーラベル (例: "Before" / "After" / "曜日コース一覧"). */
  title: string;
  /** ヘッダー右端の合計サマリ (任意). 例: "23件 / 145.2km". */
  totalSummary?: string | null;
  /** ヘッダーの強調色 (primary=After 用 / muted=Before 用). */
  tone?: 'primary' | 'muted';
  /** 各コース行. 並び順を制御したい場合は呼び出し側で sort 済みで渡す. */
  courses: CourseListItem[];
  /** 表示する最大 visit 数 (overflow は "…他 N 件"). undefined / 0 = 全件表示. */
  maxVisitsPerCourse?: number;
  /** カードに付与する testid prefix. */
  testIdPrefix?: string;
}

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

/**
 * time_type バッジを preferred_start/end と合わせて整形.
 * FullOptimizeDialog 内の同名関数と完全互換 (移植元).
 */
function formatTimeCondition(v: VisitListItem): string | null {
  if (v.time_type === '時間帯' && v.preferred_start && v.preferred_end) {
    return `🕐 時間帯 (${trimSeconds(v.preferred_start)}-${trimSeconds(v.preferred_end)})`;
  }
  if (v.time_type === '固定' && v.preferred_start) {
    return `🕐 固定 (${trimSeconds(v.preferred_start)})`;
  }
  if (v.time_type === '午前') return '🕐 午前 (~12:00)';
  if (v.time_type === '午後') return '🕐 午後 (13:00~)';
  if (v.time_type === '終日') return '🕐 終日';
  return v.time_type ? `🕐 ${v.time_type}` : null;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function WeekdayScheduleCard({
  title,
  totalSummary,
  tone = 'muted',
  courses,
  maxVisitsPerCourse,
  testIdPrefix,
}: WeekdayScheduleCardProps) {
  const headerCls =
    tone === 'primary'
      ? 'border-brand-primary/40 bg-brand-primary/5 text-brand-primary'
      : 'border-border-default bg-bg-muted text-text-muted';
  return (
    <div
      className="overflow-hidden rounded border border-border-default"
      data-testid={testIdPrefix ? `${testIdPrefix}-card` : undefined}
    >
      <div
        className={`flex items-center justify-between border-b px-2 py-1 text-[11px] font-semibold ${headerCls}`}
      >
        <span>{title}</span>
        {totalSummary ? <span className="tnum text-text-secondary">{totalSummary}</span> : null}
      </div>
      {courses.length === 0 ? (
        <div className="py-4 text-center text-[11px] text-text-muted">(コースなし)</div>
      ) : (
        <ul className="divide-y divide-border-default">
          {courses.map((c) => (
            <CourseRow
              key={c.key}
              course={c}
              maxVisits={maxVisitsPerCourse}
              testIdPrefix={testIdPrefix}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

interface CourseRowProps {
  course: CourseListItem;
  maxVisits?: number;
  testIdPrefix?: string;
}

function CourseRow({ course, maxVisits, testIdPrefix }: CourseRowProps) {
  const sliced = maxVisits && maxVisits > 0 ? course.visits.slice(0, maxVisits) : course.visits;
  const overflowCount = course.visits.length - sliced.length;
  return (
    <li className="px-2 py-1.5">
      <div className="flex items-center justify-between text-[11px]">
        <span className="font-semibold text-text-primary">{course.title}</span>
        {course.summary ? <span className="tnum text-text-muted">{course.summary}</span> : null}
      </div>
      {sliced.length > 0 ? (
        <ul className="mt-1 space-y-0.5">
          {sliced.map((v, i, arr) => (
            <VisitRow
              key={v.key}
              visit={v}
              prev={arr[i - 1] ?? null}
              next={arr[i + 1] ?? null}
              fullVisits={course.visits}
              testIdPrefix={testIdPrefix}
            />
          ))}
          {overflowCount > 0 ? (
            <li className="text-[10px] text-text-muted">…他 {overflowCount} 件</li>
          ) : null}
        </ul>
      ) : null}
    </li>
  );
}

interface VisitRowProps {
  visit: VisitListItem;
  prev: VisitListItem | null;
  next: VisitListItem | null;
  /** 同住所グループサイズ計算用に slice 前の全 visit を渡す. */
  fullVisits: VisitListItem[];
  testIdPrefix?: string;
}

function VisitRow({ visit, prev, next, fullVisits, testIdPrefix }: VisitRowProps) {
  const inGroup = !!visit.same_address_group_id;
  const sameAsPrev = inGroup && prev?.same_address_group_id === visit.same_address_group_id;
  const sameAsNext = inGroup && next?.same_address_group_id === visit.same_address_group_id;
  const isGroupStart = inGroup && !sameAsPrev;
  const isGroupEnd = inGroup && !sameAsNext;

  // グループ先頭で患者数を計算 (slice 前の全 visit を参照).
  let groupSize = 0;
  if (isGroupStart) {
    for (const fv of fullVisits) {
      if (fv.same_address_group_id === visit.same_address_group_id) {
        groupSize += 1;
      }
    }
  }

  const timeCondition = formatTimeCondition(visit);

  return (
    <li
      className={cn(
        'flex flex-wrap items-center gap-1 text-[10px]',
        inGroup && 'border-l-2 border-yellow-400 bg-yellow-50/60 pl-2',
        isGroupStart && 'pt-1 mt-1',
        isGroupEnd && 'pb-1 mb-1',
      )}
      data-testid={testIdPrefix ? `${testIdPrefix}-visit-${visit.key}` : undefined}
      data-same-address-group-id={visit.same_address_group_id ?? undefined}
    >
      {isGroupStart && groupSize >= 2 ? (
        <span className="w-full text-[9px] font-semibold text-yellow-700">
          📍 同住所グループ ({groupSize} 名)
        </span>
      ) : null}
      <span className="tnum text-text-muted">{trimSeconds(visit.start_time)}</span>
      <span className="text-text-primary">{visit.patient_name}</span>
      {visit.area_label ? (
        <span className="rounded bg-brand-primary/10 px-1 text-[9px] text-brand-primary">
          {visit.area_label}
        </span>
      ) : null}
      {visit.address ? (
        <span
          className="text-[9px] text-text-muted"
          title={visit.address}
          aria-label={`住所 ${visit.address}`}
        >
          {visit.address.length > 18 ? `${visit.address.slice(0, 18)}…` : visit.address}
        </span>
      ) : null}
      {timeCondition ? (
        <span className="text-[9px] text-text-secondary">{timeCondition}</span>
      ) : null}
      {visit.sex_restriction === 'female_only' ? (
        <span className="text-[9px] text-pink-600">👩 女性のみ</span>
      ) : null}
      {visit.sex_restriction === 'male_only' ? (
        <span className="text-[9px] text-blue-600">👨 男性のみ</span>
      ) : null}
      {/* 次の patient までの距離 (= VisitArrow). null なら描画しない. */}
      <VisitArrow distanceKm={visit.distance_to_next_km ?? null} />
    </li>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Adapter Helpers — caller 側で V2 / patients から VisitListItem を構築する
// ─────────────────────────────────────────────────────────────────────────

/**
 * 2 地点 (lat, lng) 間の Haversine 距離 (km).
 * Backend と同等のアルゴリズム (CourseListColumn 移植元の用途と整合).
 */
export function haversineKm(
  a: { lat: number; lng: number },
  b: { lat: number; lng: number },
): number {
  const R = 6371; // 地球半径 (km)
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(x)));
}

/**
 * lat/lng から同住所グループキーを生成する (tolerance 0.001 ≒ 100m).
 * Backend と同じ rounding (toFixed(3)) で bucket 化.
 * lat/lng が無い patient は null を返す (グルーピングしない).
 */
export function buildSameAddressKey(
  lat: number | null | undefined,
  lng: number | null | undefined,
): string | null {
  if (lat === null || lat === undefined || lng === null || lng === undefined) return null;
  return `${lat.toFixed(3)}:${lng.toFixed(3)}`;
}
