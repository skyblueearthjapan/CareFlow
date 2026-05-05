'use client';

/**
 * AI入力モーダル — D4 Phase E / Wave 4-B / **Wave 5 FE10 拡張**.
 *
 * Implements design 09 (`docs/design/09-global-ai-input.md`) states:
 *   1. input — テキスト入力 + 音声入力 + context_type 選択
 *   2. recording — Web Speech API による録音中表示
 *   3. reviewing — Gemini 解釈結果 + 信頼度バッジ + 適用 / 修正 / 破棄
 *   4. out_of_scope — AI 範囲外検知時のフィードバック (W5-FE10 / §3.5.7)
 *
 * Phase 5 では既定の「適用」は自動 form pre-fill ではなく JSON コピペ運用。
 * Wave 5 (FE11/12/13) は本ファイルの **拡張ポイント** を経由して機能を追加するため、
 * モーダル本体への直接改修は **避けること**（衝突回避）。
 *
 * Wave 5 拡張ポイント（後続チケットからの利用想定）:
 *   - `onSubmitInterceptor` — FE11 が pending_requests POST に切替
 *   - `missingInfoSlot`     — FE12 が MissingInfoModal を mount
 *   - `submissionMode`      — 'clipboard' (既定) | 'pending_request' (FE11)
 *   - `voiceFirst`          — モバイル特化（音声入力主体）モードを強制
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Mic, MicOff, Send, Sparkles, X } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { CapabilitiesPanel } from '@/components/ai/CapabilitiesPanel';
import { AI_OUT_OF_SCOPE_FALLBACK_MESSAGE, isOutOfScopeActionType } from '@/lib/ai-capabilities';
import { useInterpret } from '@/lib/queries/ai';
import {
  AI_CONTEXT_LABELS,
  AI_CONTEXT_TYPES,
  type AiContextType,
  type InterpretedAction,
  type InterpretResponse,
} from '@/lib/schemas/ai';
import { useUIStore } from '@/lib/stores/ui';

// --- Web Speech API typing ----------------------------------------------

type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};

type SpeechRecognitionEventLike = {
  results: ArrayLike<{
    isFinal: boolean;
    0: { transcript: string };
  }>;
};

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

// --- Confidence helpers --------------------------------------------------

function confidenceVariant(c: number): 'success' | 'warning' | 'destructive' {
  if (c >= 0.9) return 'success';
  if (c >= 0.7) return 'warning';
  return 'destructive';
}

function confidenceLabel(c: number): string {
  return `${Math.round(c * 100)}%`;
}

// --- Out-of-scope helpers (W5-FE10 / §3.5.7) -----------------------------

/**
 * AI 解釈結果に `out_of_scope` action_type が含まれているか判定する。
 * 含まれていれば「申し訳ありません、これは AI で対応していません」UI に切替。
 */
function findOutOfScopeAction(
  result: InterpretResponse | undefined,
): InterpretedAction | undefined {
  if (!result) return undefined;
  return result.interpreted.actions?.find((a) => isOutOfScopeActionType(a.action_type));
}

/**
 * `out_of_scope` アクションの fields からユーザー向けメッセージを取り出す。
 * `message` / `reason` どちらでも拾えるようにし、無ければ既定文言を返す。
 */
function getOutOfScopeMessage(action: InterpretedAction): string {
  const f = action.fields as Record<string, unknown>;
  const candidates = [f.message, f.reason, f.detail];
  for (const c of candidates) {
    if (typeof c === 'string' && c.trim().length > 0) return c;
  }
  return AI_OUT_OF_SCOPE_FALLBACK_MESSAGE;
}

// --- Submission modes (W5-FE10 拡張ポイント) -----------------------------

/**
 * 反映モード:
 *   - `'clipboard'`       — 既存挙動。解釈結果 JSON をクリップボードにコピー
 *   - `'pending_request'` — FE11 が `pending_requests` POST に切替（要 onSubmitInterceptor）
 *
 * 既定は `'clipboard'`。FE11 統合時は `<AiInputModal submissionMode="pending_request" />`
 * + `onSubmitInterceptor` を併用する想定。
 */
export type AiSubmissionMode = 'clipboard' | 'pending_request';

// --- Props (W5-FE10 拡張) ------------------------------------------------

export interface AiInputModalProps {
  /**
   * AI 解釈成功後の「適用」サブミット処理を差し替える。
   *
   * 渡すと既定のクリップボードコピーは行わず、本コールバックのみが呼ばれる。
   * Promise を返した場合はその完了を待ってモーダルを閉じる（rejection 時は閉じない）。
   *
   * **想定利用 (FE11)**: `pending_requests` への POST に置き換える。
   *
   * @example
   * ```tsx
   * <AiInputModal
   *   submissionMode="pending_request"
   *   onSubmitInterceptor={async (result) => {
   *     await postPendingRequest(result);
   *   }}
   * />
   * ```
   */
  onSubmitInterceptor?: (result: InterpretResponse) => void | Promise<void>;

  /**
   * 不足情報補完モーダル等を差し込むスロット。
   *
   * 描画位置は本モーダルの最下段（DialogContent 末尾）。zIndex の都合上、
   * Slot 内のコンテンツは独自 `<Dialog />` を mount しても動作する。
   *
   * **想定利用 (FE12)**: `<MissingInfoModal />` を配置し、
   * `patient_create` / `staff_create` の必須未入力を赤枠でハイライトする。
   *
   * @example
   * ```tsx
   * <AiInputModal missingInfoSlot={<MissingInfoModal />} />
   * ```
   */
  missingInfoSlot?: ReactNode;

  /**
   * 反映モード。
   *
   * - `'clipboard'`       (既定) — 解釈結果 JSON をクリップボードにコピー
   * - `'pending_request'` — `onSubmitInterceptor` 経由で pending_requests に POST
   *
   * `'pending_request'` で `onSubmitInterceptor` 未指定の場合は警告 toast を出し
   * `'clipboard'` にフォールバックする（誤設定の保険）。
   *
   * **想定利用 (FE11)**: モバイル / Staff ロールでは pending_request を使用。
   */
  submissionMode?: AiSubmissionMode;

  /**
   * モバイル特化（音声入力主体）モードを強制する。
   *
   * - 起動時にコンテキスト選択を畳み（モバイルは general 既定）
   * - 「できること」パネルを既定で閉じた状態に
   * - 録音ボタンを大きめ・先頭に配置（CSS 切替）
   *
   * 既定は `false`。AiFab 経由のグローバル起動では PC レイアウト。
   * モバイル UI から呼び出す `<AiInputModal voiceFirst />` でモバイル最適化。
   */
  voiceFirst?: boolean;
}

// ------------------------------------------------------------------------

export function AiInputModal({
  onSubmitInterceptor,
  missingInfoSlot,
  submissionMode = 'clipboard',
  voiceFirst = false,
}: AiInputModalProps = {}) {
  const open = useUIStore((s) => s.aiInputOpen);
  const setOpen = useUIStore((s) => s.setAiInputOpen);

  const [text, setText] = useState('');
  const [contextType, setContextType] = useState<AiContextType>('general');
  const [recording, setRecording] = useState(false);
  const [recognitionError, setRecognitionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);

  const interpret = useInterpret();
  const result: InterpretResponse | undefined = interpret.data;
  const outOfScopeAction = useMemo(() => findOutOfScopeAction(result), [result]);

  const speechAvailable = useMemo(() => getSpeechRecognitionCtor() !== null, []);

  // Reset state when the modal closes.
  useEffect(() => {
    if (!open) {
      setText('');
      setRecording(false);
      setRecognitionError(null);
      setSubmitting(false);
      interpret.reset();
      try {
        recognitionRef.current?.abort();
      } catch {
        /* noop */
      }
      recognitionRef.current = null;
    }
    // intentionally exclude `interpret` (referential identity changes)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Cmd/Ctrl + Enter → send.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        if (text.trim() && !interpret.isPending) {
          void onSubmit();
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, text, interpret.isPending]);

  function startRecording() {
    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) {
      setRecognitionError(
        'このブラウザは音声入力に対応していません。Chrome / Edge をご利用ください。',
      );
      return;
    }
    setRecognitionError(null);
    const rec = new Ctor();
    rec.lang = 'ja-JP';
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (event) => {
      let finalText = '';
      let interim = '';
      for (let i = 0; i < event.results.length; i += 1) {
        const r = event.results[i];
        if (!r) continue;
        if (r.isFinal) finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      setText((prev) => {
        // Only append finals; interim is shown via separate state next pass.
        if (finalText) return prev ? `${prev}${finalText}` : finalText;
        return prev || interim;
      });
    };
    rec.onerror = (event) => {
      setRecognitionError(`音声認識エラー: ${event.error ?? 'unknown'}`);
      setRecording(false);
    };
    rec.onend = () => setRecording(false);
    try {
      rec.start();
      recognitionRef.current = rec;
      setRecording(true);
    } catch (e) {
      setRecognitionError(`録音を開始できません: ${(e as Error).message}`);
    }
  }

  function stopRecording() {
    try {
      recognitionRef.current?.stop();
    } catch {
      /* noop */
    }
    setRecording(false);
  }

  async function onSubmit() {
    try {
      await interpret.mutateAsync({
        prompt: text.trim(),
        context_type: contextType,
        context: {},
      });
    } catch (e) {
      toast.error(`AI解釈に失敗しました: ${(e as Error).message}`);
    }
  }

  /**
   * 「適用」ハンドラ。submissionMode と onSubmitInterceptor の組合せで挙動を分岐。
   *
   * 1. `out_of_scope` 検知時は適用ボタン自体を非表示にしているのでここには来ない
   * 2. `onSubmitInterceptor` が渡されていれば、それを呼び出して終了
   *    - mode が 'pending_request' でも 'clipboard' でも interceptor を優先
   * 3. 渡されていない時:
   *    - mode === 'pending_request' なら「未実装フォールバック」を toast 警告
   *      し、clipboard コピーで継続（誤設定の保険）
   *    - mode === 'clipboard' なら従来通り JSON をコピー
   */
  async function onApply() {
    if (!result) return;

    if (onSubmitInterceptor) {
      try {
        setSubmitting(true);
        await onSubmitInterceptor(result);
        setOpen(false);
      } catch (e) {
        toast.error(`送信に失敗しました: ${(e as Error).message}`);
      } finally {
        setSubmitting(false);
      }
      return;
    }

    if (submissionMode === 'pending_request') {
      toast.warning(
        'pending_request モードですが onSubmitInterceptor が未指定のため、JSON コピーで代替します',
      );
    }

    // Default: copy structured JSON to clipboard so the operator can paste
    // into the relevant form. Direct form pre-fill lands via FE11/12.
    const json = JSON.stringify(result.interpreted, null, 2);
    navigator.clipboard
      ?.writeText(json)
      .then(() => toast.success('解釈結果を JSON でクリップボードにコピーしました'))
      .catch(() => toast.warning('クリップボードへのコピーに失敗しました'));
    setOpen(false);
  }

  function onDiscard() {
    interpret.reset();
    setText('');
    setOpen(false);
  }

  function onRetry() {
    // 「修正して送信」: keep the text so the operator can tweak and resubmit.
    interpret.reset();
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-[560px]" aria-describedby="ai-input-modal-desc">
        <DialogHeader>
          <div className="flex items-start gap-3">
            <div
              className="flex h-7 w-7 items-center justify-center rounded-md text-white"
              style={{ background: 'linear-gradient(135deg,#0D9488,#14B8A6)' }}
              aria-hidden
            >
              <Sparkles className="h-4 w-4" strokeWidth={1.75} />
            </div>
            <div>
              <DialogTitle>AI入力</DialogTitle>
              <DialogDescription id="ai-input-modal-desc">
                自然言語でスケジュール・患者・休みを編集できます。
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {/* ---------- input / recording state ---------- */}
        {!result ? (
          <div className="space-y-4">
            {/* PC: コンテキスト選択を上部に。voiceFirst では畳む（モバイルは general 既定）*/}
            {!voiceFirst && (
              <div className="space-y-2">
                <label className="block text-xs font-medium text-text-secondary">
                  コンテキスト
                </label>
                <Select
                  value={contextType}
                  onValueChange={(v) => setContextType(v as AiContextType)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {AI_CONTEXT_TYPES.map((c) => (
                      <SelectItem key={c} value={c}>
                        {AI_CONTEXT_LABELS[c]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="例：田中さん木曜の午前休みにして"
              className={voiceFirst ? 'min-h-[80px]' : 'min-h-[100px]'}
              autoFocus={!voiceFirst}
            />

            {recording && (
              <div className="flex items-center gap-2 text-sm text-error">
                <span className="inline-block h-2.5 w-2.5 animate-pulse rounded-full bg-error" />
                録音中... もう一度マイクをクリックすると停止します
              </div>
            )}

            {recognitionError && (
              <Alert variant="destructive">
                <AlertTitle>音声入力</AlertTitle>
                <AlertDescription>{recognitionError}</AlertDescription>
              </Alert>
            )}

            {/* W5-FE10: 「できること / できないこと」恒常表示パネル */}
            <CapabilitiesPanel defaultCanOpen={!voiceFirst} />

            {!speechAvailable && (
              <p className="text-xs text-text-muted">
                ※ 音声入力は Chrome / Edge のみ対応 (Safari / Firefox はテキスト入力のみ)
              </p>
            )}

            <div
              className={
                voiceFirst
                  ? 'flex flex-col-reverse items-stretch gap-2'
                  : 'flex items-center justify-end gap-2'
              }
            >
              <Button
                type="button"
                variant="outline"
                onClick={recording ? stopRecording : startRecording}
                disabled={!speechAvailable || interpret.isPending}
                className={voiceFirst ? 'h-12 text-base' : undefined}
                title={
                  speechAvailable ? '音声入力 (ja-JP)' : 'このブラウザは Web Speech API 非対応'
                }
              >
                {recording ? (
                  <>
                    <MicOff className="h-4 w-4" /> 停止
                  </>
                ) : (
                  <>
                    <Mic className="h-4 w-4" /> 録音
                  </>
                )}
              </Button>
              <Button
                type="button"
                onClick={onSubmit}
                disabled={!text.trim() || interpret.isPending}
                className={voiceFirst ? 'h-12 text-base' : undefined}
              >
                <Send className="h-4 w-4" />
                {interpret.isPending ? '解釈中...' : voiceFirst ? '送信' : '送信 (Cmd+Enter)'}
              </Button>
            </div>
          </div>
        ) : outOfScopeAction ? (
          /* ---------- out_of_scope state (W5-FE10 / §3.5.7) ---------- */
          <OutOfScopeFeedback
            originalText={text}
            action={outOfScopeAction}
            onRetry={onRetry}
            onClose={onDiscard}
          />
        ) : (
          /* ---------- reviewing state ---------- */
          <ResultReview
            result={result}
            originalText={text}
            onApply={onApply}
            onDiscard={onDiscard}
            onRetry={onRetry}
            applyLabel={
              submissionMode === 'pending_request' && onSubmitInterceptor
                ? '申請を送信'
                : '適用 (JSON コピー)'
            }
            applyDisabled={submitting}
          />
        )}

        {/*
         * W5-FE10 拡張ポイント: missingInfoSlot
         * 不足情報補完モーダル (FE12) はここに mount される。
         * 既定では何も描画されない。
         */}
        {missingInfoSlot}
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------------

function ResultReview({
  result,
  originalText,
  onApply,
  onDiscard,
  onRetry,
  applyLabel,
  applyDisabled,
}: {
  result: InterpretResponse;
  originalText: string;
  onApply: () => void;
  onDiscard: () => void;
  onRetry: () => void;
  applyLabel: string;
  applyDisabled: boolean;
}) {
  const actions = result.interpreted.actions ?? [];
  const variant = confidenceVariant(result.confidence);
  const lowConfidence = result.confidence < 0.7;

  return (
    <div className="space-y-4">
      <div>
        <p className="mb-1 text-xs uppercase tracking-wide text-text-muted">元の発話</p>
        <div className="rounded-md bg-bg-muted px-3 py-2 text-sm text-text-secondary">
          {originalText || '(空)'}
        </div>
      </div>

      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-text-primary">AI解釈</p>
        <Badge variant={variant}>信頼度 {confidenceLabel(result.confidence)}</Badge>
      </div>

      {lowConfidence && (
        <Alert variant="destructive">
          <AlertTitle>低信頼度</AlertTitle>
          <AlertDescription>解釈が曖昧です。手動で修正をご確認ください。</AlertDescription>
        </Alert>
      )}

      {actions.length === 0 ? (
        <Alert>
          <AlertTitle>アクションが検出されませんでした</AlertTitle>
          <AlertDescription>入力内容を変えてもう一度お試しください。</AlertDescription>
        </Alert>
      ) : (
        <ol className="space-y-3">
          {actions.map((a, i) => (
            <li key={i} className="rounded-md border border-border-default p-3 text-sm">
              <div className="mb-2 flex items-center justify-between">
                <code className="rounded bg-bg-muted px-1.5 py-0.5 text-xs">{a.action_type}</code>
                <Badge variant={confidenceVariant(a.confidence)}>
                  {confidenceLabel(a.confidence)}
                </Badge>
              </div>
              <pre className="whitespace-pre-wrap break-words text-xs text-text-secondary">
                {JSON.stringify(a.fields, null, 2)}
              </pre>
            </li>
          ))}
        </ol>
      )}

      <details className="rounded-md border border-border-default px-3 py-2 text-xs">
        <summary className="cursor-pointer text-text-secondary">
          raw response (デバッグ) — model: {result.model} / latency: {result.latency_ms}ms / cost: $
          {result.cost_usd.toFixed(6)}
        </summary>
        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words text-text-muted">
          {result.raw_response}
        </pre>
      </details>

      <div className="flex items-center justify-end gap-2">
        <Button type="button" variant="outline" onClick={onRetry}>
          <X className="h-4 w-4" /> 修正して送信
        </Button>
        <Button type="button" variant="ghost" onClick={onDiscard}>
          破棄
        </Button>
        <Button type="button" onClick={onApply} disabled={actions.length === 0 || applyDisabled}>
          {applyLabel}
        </Button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------

/**
 * `out_of_scope` action_type 検知時の専用 UI（W5-FE10 / §3.5.7）。
 *
 * ユーザー発話: 「拠点を追加したい」
 * AI 応答    : 「拠点の追加は AI では対応していません。サイドバーの『拠点』
 *               画面から登録してください」
 */
function OutOfScopeFeedback({
  originalText,
  action,
  onRetry,
  onClose,
}: {
  originalText: string;
  action: InterpretedAction;
  onRetry: () => void;
  onClose: () => void;
}) {
  const message = getOutOfScopeMessage(action);

  return (
    <div className="space-y-4" data-testid="ai-out-of-scope-feedback">
      <div>
        <p className="mb-1 text-xs uppercase tracking-wide text-text-muted">元の発話</p>
        <div className="rounded-md bg-bg-muted px-3 py-2 text-sm text-text-secondary">
          {originalText || '(空)'}
        </div>
      </div>

      <Alert variant="destructive">
        <AlertTitle>AI で対応していない依頼です</AlertTitle>
        <AlertDescription>
          <p>{message}</p>
          <p className="mt-2 text-xs text-text-muted">
            AI でできる操作は「できること」セクションをご確認ください。
          </p>
        </AlertDescription>
      </Alert>

      <div className="flex items-center justify-end gap-2">
        <Button type="button" variant="outline" onClick={onRetry}>
          <X className="h-4 w-4" /> 別の指示で送信
        </Button>
        <Button type="button" variant="ghost" onClick={onClose}>
          閉じる
        </Button>
      </div>
    </div>
  );
}
