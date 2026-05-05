'use client';

import { useState } from 'react';

import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { useAiInterpretLogs } from '@/lib/queries/integrations';

export default function AiInterpretLogsPage() {
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');
  const [model, setModel] = useState('');

  const { data: rows, isLoading, isError, error } = useAiInterpretLogs({
    since: since || undefined,
    until: until || undefined,
    model: model || undefined,
  });

  return (
    <section className="space-y-4">
      <header>
        <h2 className="font-serif text-xl font-bold text-text-primary">AI 解釈ログ</h2>
        <p className="text-sm text-text-secondary">
          LLM (Gemini 等) への prompt / response 監査ログ (admin のみ閲覧)
        </p>
      </header>

      <Card className="p-4">
        <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-secondary">since (ISO8601)</span>
            <Input
              type="datetime-local"
              value={since}
              onChange={(e) => setSince(e.target.value)}
            />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-secondary">until (ISO8601)</span>
            <Input
              type="datetime-local"
              value={until}
              onChange={(e) => setUntil(e.target.value)}
            />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-text-secondary">model</span>
            <Input
              placeholder="gemini-1.5-pro"
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
            <table className="w-full text-sm">
              <thead className="border-b border-border-default text-left text-text-secondary">
                <tr>
                  <th className="px-3 py-2 font-medium">作成日時</th>
                  <th className="px-3 py-2 font-medium">model</th>
                  <th className="px-3 py-2 font-medium">latency(ms)</th>
                  <th className="px-3 py-2 font-medium">prompt (先頭)</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-b border-border-default last:border-0">
                    <td className="px-3 py-2 text-text-secondary">{r.created_at}</td>
                    <td className="px-3 py-2">{r.model}</td>
                    <td className="px-3 py-2 tnum">{r.latency_ms}</td>
                    <td className="px-3 py-2">
                      <span className="line-clamp-2 text-text-secondary">
                        {r.prompt.slice(0, 200)}
                      </span>
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
