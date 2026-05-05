'use client';

/**
 * AI入力モーダル — D4 Phase E / Wave 4-B.
 *
 * Implements design 09 (`docs/design/09-global-ai-input.md`) states:
 *   1. input — テキスト入力 + 音声入力 + context_type 選択
 *   2. recording — Web Speech API による録音中表示
 *   3. reviewing — Gemini 解釈結果 + 信頼度バッジ + 適用 / 修正 / 破棄
 *
 * Phase 5 では「適用」は自動 form pre-fill ではなく、JSON コピペ運用です
 * (各 form コンポーネントへの直接ワイヤリングは別 PR)。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
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
import { useInterpret } from '@/lib/queries/ai';
import {
  AI_CONTEXT_LABELS,
  AI_CONTEXT_TYPES,
  type AiContextType,
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

// ------------------------------------------------------------------------

export function AiInputModal() {
  const open = useUIStore((s) => s.aiInputOpen);
  const setOpen = useUIStore((s) => s.setAiInputOpen);

  const [text, setText] = useState('');
  const [contextType, setContextType] = useState<AiContextType>('general');
  const [recording, setRecording] = useState(false);
  const [recognitionError, setRecognitionError] = useState<string | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);

  const interpret = useInterpret();
  const result: InterpretResponse | undefined = interpret.data;

  const speechAvailable = useMemo(() => getSpeechRecognitionCtor() !== null, []);

  // Reset state when the modal closes.
  useEffect(() => {
    if (!open) {
      setText('');
      setRecording(false);
      setRecognitionError(null);
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

  function onApply() {
    if (!result) return;
    // Phase 5: copy structured JSON to clipboard so the operator can paste
    // into the relevant form. Direct form pre-fill lands in a follow-up PR.
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
      <DialogContent
        className="max-w-[560px]"
        aria-describedby="ai-input-modal-desc"
      >
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

            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="例：田中さん木曜の午前休みにして"
              className="min-h-[100px]"
              autoFocus
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

            <div className="rounded-md bg-bg-muted p-3 text-xs text-text-secondary">
              <p className="mb-1 font-medium text-text-primary">ヒント</p>
              <ul className="list-disc space-y-0.5 pl-4">
                <li>「田中さん木曜休み」</li>
                <li>「火曜午後 管理者会議」</li>
                <li>「山田さん明日の訪問キャンセル」</li>
                <li>「鈴木さん明日10時に1時間追加」</li>
              </ul>
              {!speechAvailable && (
                <p className="mt-2 text-text-muted">
                  ※ 音声入力は Chrome / Edge のみ対応 (Safari / Firefox はテキスト入力のみ)
                </p>
              )}
            </div>

            <div className="flex items-center justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={recording ? stopRecording : startRecording}
                disabled={!speechAvailable || interpret.isPending}
                title={
                  speechAvailable
                    ? '音声入力 (ja-JP)'
                    : 'このブラウザは Web Speech API 非対応'
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
              >
                <Send className="h-4 w-4" />
                {interpret.isPending ? '解釈中...' : '送信 (Cmd+Enter)'}
              </Button>
            </div>
          </div>
        ) : (
          /* ---------- reviewing state ---------- */
          <ResultReview
            result={result}
            originalText={text}
            onApply={onApply}
            onDiscard={onDiscard}
            onRetry={onRetry}
          />
        )}
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
}: {
  result: InterpretResponse;
  originalText: string;
  onApply: () => void;
  onDiscard: () => void;
  onRetry: () => void;
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
        <Badge variant={variant}>
          信頼度 {confidenceLabel(result.confidence)}
        </Badge>
      </div>

      {lowConfidence && (
        <Alert variant="destructive">
          <AlertTitle>低信頼度</AlertTitle>
          <AlertDescription>
            解釈が曖昧です。手動で修正をご確認ください。
          </AlertDescription>
        </Alert>
      )}

      {actions.length === 0 ? (
        <Alert>
          <AlertTitle>アクションが検出されませんでした</AlertTitle>
          <AlertDescription>
            入力内容を変えてもう一度お試しください。
          </AlertDescription>
        </Alert>
      ) : (
        <ol className="space-y-3">
          {actions.map((a, i) => (
            <li
              key={i}
              className="rounded-md border border-border-default p-3 text-sm"
            >
              <div className="mb-2 flex items-center justify-between">
                <code className="rounded bg-bg-muted px-1.5 py-0.5 text-xs">
                  {a.action_type}
                </code>
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
          raw response (デバッグ) — model: {result.model} / latency:{' '}
          {result.latency_ms}ms / cost: ${result.cost_usd.toFixed(6)}
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
        <Button type="button" onClick={onApply} disabled={actions.length === 0}>
          適用 (JSON コピー)
        </Button>
      </div>
    </div>
  );
}
