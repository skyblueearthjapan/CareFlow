'use client';

import { useState } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { useAiLogs } from '@/lib/queries/ai';
import type { AiLogRead } from '@/lib/schemas/ai';

function confidenceBadge(c: number | null | undefined) {
  if (c == null) return null;
  if (c >= 0.9) return <Badge variant="success">{Math.round(c * 100)}%</Badge>;
  if (c >= 0.7) return <Badge variant="warning">{Math.round(c * 100)}%</Badge>;
  return <Badge variant="destructive">{Math.round(c * 100)}%</Badge>;
}

function actionsSummary(row: AiLogRead): string {
  const interpreted = (row.response as { interpreted?: { actions?: { action_type: string }[] } })
    ?.interpreted;
  const actions = interpreted?.actions ?? [];
  if (actions.length === 0) return '(なし)';
  return actions.map((a) => a.action_type).join(', ');
}

function metaUserText(row: AiLogRead): string {
  const meta = (row.response as { _meta?: { user_text?: string; error?: string } })?._meta;
  return meta?.user_text ?? meta?.error ?? '';
}

export function AiLogsList() {
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');
  const [model, setModel] = useState('');

  const { data, isLoading, isError, error } = useAiLogs({
    since: since || undefined,
    until: until || undefined,
    model: model || undefined,
  });
  const rows = data?.items;
  const total = data?.total ?? 0;

  return (
    <section className="space-y-4">
      <header>
        <h2 className="font-serif text-xl font-bold text-text-primary">AI 解釈ログ</h2>
        <p className="text-sm text-text-secondary">
          Gemini への prompt / response 監査ログ (admin / manager のみ閲覧)
        </p>
      </header>

      <Card className="p-4">
        <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-secondary">since (ISO8601)</span>
            <Input type="datetime-local" value={since} onChange={(e) => setSince(e.target.value)} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-secondary">until (ISO8601)</span>
            <Input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-secondary">model</span>
            <Input
              placeholder="gemini-1.5-flash"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
          </label>
        </div>

        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-3/4" />
          </div>
        ) : isError ? (
          <Alert variant="destructive">
            <AlertTitle>取得に失敗しました</AlertTitle>
            <AlertDescription>
              {error instanceof Error ? error.message : '不明なエラー'}
            </AlertDescription>
          </Alert>
        ) : !rows || rows.length === 0 ? (
          <p className="text-sm text-text-muted">該当するログはありません</p>
        ) : (
          <div className="overflow-x-auto">
            <p className="mb-2 text-xs text-text-muted">全 {total} 件</p>
            <table className="w-full text-sm">
              <thead className="border-b border-border-default text-left text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">作成日時</th>
                  <th className="px-3 py-2 font-medium">context</th>
                  <th className="px-3 py-2 font-medium">model</th>
                  <th className="px-3 py-2 font-medium">latency</th>
                  <th className="px-3 py-2 font-medium">cost</th>
                  <th className="px-3 py-2 font-medium">conf.</th>
                  <th className="px-3 py-2 font-medium">user input</th>
                  <th className="px-3 py-2 font-medium">actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-b border-border-default align-top last:border-0">
                    <td className="whitespace-nowrap px-3 py-2 text-text-secondary">
                      {r.created_at}
                    </td>
                    <td className="px-3 py-2">
                      {r.context_type ? <Badge variant="secondary">{r.context_type}</Badge> : '-'}
                    </td>
                    <td className="px-3 py-2">{r.model}</td>
                    <td className="px-3 py-2 tnum">{r.latency_ms}ms</td>
                    <td className="px-3 py-2 tnum">
                      {r.cost_usd != null ? `$${r.cost_usd.toFixed(6)}` : '-'}
                    </td>
                    <td className="px-3 py-2">{confidenceBadge(r.confidence)}</td>
                    <td className="max-w-[260px] px-3 py-2">
                      <span className="line-clamp-2 text-text-secondary">
                        {metaUserText(r) || r.prompt.slice(0, 120)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-text-secondary">
                      <code className="text-xs">{actionsSummary(r)}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </section>
  );
}
