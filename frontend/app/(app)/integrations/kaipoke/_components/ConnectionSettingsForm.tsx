'use client';

/**
 * ConnectionSettingsForm — カイポケ接続設定の本体（admin専用）。
 *
 * PO要望 (2026-07-09): 認証情報 (法人ID / ユーザーID / パスワード) が常時ページ表側に
 * 出ているのはセキュリティ上こわい、という理由で「設定」メニュー内のダイアログへ格納した。
 * そのため本コンポーネントは Card / 見出しを持たず、ダイアログ本文としてのみ描画される。
 *
 * 設定済み: コンパクト表示（法人ID / ユーザーID / 更新日）＋「変更する」でフォーム展開。
 * 未設定:   フォームを最初から展開＋アンバー注意バナー。
 * 接続テスト: 設定済みのときのみ活性。約60秒の実ログインを試行（90秒タイムアウト）。
 */
import { useState } from 'react';
import { toast } from 'sonner';

import { ApiError } from '@/lib/api-client';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  useKaipokeCredentials,
  useSaveKaipokeCredentials,
  useTestKaipokeCredentials,
} from '@/lib/queries/integrations';

// --- 型 ---

type TestState = null | 'running' | 'busy' | { ok: boolean; message: string };

// --- ユーティリティ ---

function fmtUpdatedAt(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} 更新`;
}

// --- メインコンポーネント ---

export function ConnectionSettingsForm() {
  const credQuery = useKaipokeCredentials();
  const cred = credQuery.data;
  const configured = credQuery.isSuccess && (cred?.configured ?? false);

  const [showForm, setShowForm] = useState(false);
  const [corpId, setCorpId] = useState('');
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [testState, setTestState] = useState<TestState>(null);

  const save = useSaveKaipokeCredentials();
  const test = useTestKaipokeCredentials();

  const canSave = corpId.trim() !== '' && userId.trim() !== '' && password.trim() !== '';

  // フォームが表示される条件: 手動展開 OR 未設定（かつ読み込み完了後）
  const isFormOpen = showForm || (credQuery.isSuccess && !configured);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await save.mutateAsync({ corpId: corpId.trim(), userId: userId.trim(), password });
      toast.success('接続設定を保存しました');
      setShowForm(false);
      setCorpId('');
      setUserId('');
      setPassword('');
      setTestState(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存に失敗しました');
    }
  };

  const handleTest = async () => {
    setTestState('running');
    try {
      const res = await test.mutateAsync();
      setTestState({ ok: res.ok, message: res.message });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setTestState('busy');
      } else {
        setTestState({
          ok: false,
          message: err instanceof Error ? err.message : '接続テストに失敗しました',
        });
      }
    }
  };

  const openForm = () => {
    setShowForm(true);
    setCorpId(cred?.corpId ?? '');
    setUserId(cred?.userId ?? '');
    setPassword('');
    setTestState(null);
  };

  const cancelForm = () => {
    setShowForm(false);
    setCorpId('');
    setUserId('');
    setPassword('');
  };

  return (
    <div>
      {/* 読み込み中 */}
      {credQuery.isLoading && <Skeleton className="h-20 w-full" />}

      {/* 未設定の警告バナー */}
      {credQuery.isSuccess && !configured && (
        <Alert variant="warning" className="mb-4">
          <AlertDescription>カイポケ連携を使うには、最初に接続設定が必要です。</AlertDescription>
        </Alert>
      )}

      {/* 設定済みのコンパクト表示 */}
      {configured && !isFormOpen && (
        <div className="flex flex-wrap items-center gap-4">
          <p className="text-sm text-text-secondary">
            <span className="font-medium text-text-primary">設定済み</span>
            {' — '}
            法人ID: <span className="font-mono text-text-primary">{cred!.corpId}</span>
            {' / '}
            ユーザーID: <span className="font-mono text-text-primary">{cred!.userId}</span>
            {cred!.updatedAt && (
              <>
                {' / '}
                <span className="text-text-muted">{fmtUpdatedAt(cred!.updatedAt)}</span>
              </>
            )}
          </p>
          <Button variant="outline" size="sm" onClick={openForm}>
            変更する
          </Button>
        </div>
      )}

      {/* フォーム（新規設定 or 変更） */}
      {isFormOpen && (
        <form onSubmit={(e) => void handleSave(e)} className="space-y-3">
          <div>
            <label
              className="mb-1 block text-xs font-medium text-text-secondary"
              htmlFor="csc-corp-id"
            >
              法人ID
            </label>
            <Input
              id="csc-corp-id"
              value={corpId}
              onChange={(e) => setCorpId(e.target.value)}
              placeholder="例: C000000"
              required
            />
          </div>
          <div>
            <label
              className="mb-1 block text-xs font-medium text-text-secondary"
              htmlFor="csc-user-id"
            >
              ユーザーID
            </label>
            <Input
              id="csc-user-id"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              placeholder="例: user01"
              required
            />
          </div>
          <div>
            <label
              className="mb-1 block text-xs font-medium text-text-secondary"
              htmlFor="csc-password"
            >
              パスワード
            </label>
            <Input
              id="csc-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={configured ? '新しいパスワードを入力（変更する場合）' : 'パスワード'}
              required
            />
          </div>
          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" disabled={!canSave || save.isPending}>
              {save.isPending ? '保存中…' : '保存'}
            </Button>
            {configured && (
              <Button type="button" variant="ghost" size="sm" onClick={cancelForm}>
                キャンセル
              </Button>
            )}
          </div>
        </form>
      )}

      {/* 接続テスト */}
      <div className="mt-5 border-t border-border-subtle pt-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!configured || testState === 'running'}
            onClick={() => void handleTest()}
          >
            {testState === 'running' ? (
              <span className="flex items-center gap-2">
                <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
                テスト中…
              </span>
            ) : (
              '接続テスト'
            )}
          </Button>

          {testState === 'running' && (
            <span className="text-xs text-text-muted">約1分・ライブモニターで確認できます</span>
          )}

          {testState === 'busy' && (
            <span className="text-xs font-medium text-warning-strong">他のジョブが実行中です</span>
          )}

          {testState !== null && testState !== 'running' && testState !== 'busy' && (
            <span
              className={`rounded px-2 py-1 text-xs font-medium ${
                testState.ok ? 'bg-success-bg text-success' : 'bg-error-bg text-error'
              }`}
            >
              {testState.ok ? '成功' : '失敗'}: {testState.message}
            </span>
          )}
        </div>

        {!configured && credQuery.isSuccess && (
          <p className="mt-1.5 text-xs text-text-muted">
            接続設定を保存してから接続テストができます。
          </p>
        )}
      </div>
    </div>
  );
}
