# W1-F shadcn/ui プリミティブ — Critic Review

**Reviewer**: oh-my-claudecode:critic (Opus, ADVERSARIAL モード)
**Commit**: `f2841cb feat(frontend): W1-F shadcn/ui primitives`
**Date**: 2026-05-05

## VERDICT: ACCEPT-WITH-RESERVATIONS

shadcn/ui 標準テンプレ準拠 + Warm & Human トークン参照 + displayName + forwardRef 型構造 はいずれも合格水準。ただし以下を W2 着手前に修正推奨。

## Critical Findings

### 1. `lib/forms.ts` の `useFormWithSchema` ヘルパで zod の transform/coerce で TS 型崩壊
- **Confidence**: MEDIUM
- **Evidence**: `forms.ts:16-29` で `useForm<z.infer<TSchema>>` 1 ジェネリックでは RHF v7 の 3 ジェネリック (`<TFieldValues, TContext, TTransformedValues>`) の `TTransformedValues` 推論が固定され、`zodResolver` の戻り値 `Resolver<input, any, output>` と `z.coerce.number()` 等で input/output が分岐するスキーマで `Type 'Resolver<input, any, output>' is not assignable to type 'Resolver<output, any, output>'` で TS エラーになる
- **Why this matters**: W1-A patient master でも `z.coerce.number()` を多用、ヘルパ統合で `pnpm typecheck` が落ちる
- **Fix**:
```ts
export function useFormWithSchema<TInput extends FieldValues, TSchema extends ZodType<unknown, ZodTypeDef, TInput>>(
  schema: TSchema,
  defaults?: DefaultValues<TInput>,
  options?: Omit<UseFormProps<TInput, unknown, z.output<TSchema>>, 'resolver' | 'defaultValues'>,
): UseFormReturn<TInput, unknown, z.output<TSchema>> {
  return useForm<TInput, unknown, z.output<TSchema>>({
    ...options,
    resolver: zodResolver(schema),
    defaultValues: defaults,
  });
}
```

## Major Findings

### 2. `Combobox` multiple モードの Badge 削除アイコンが SR/キーボード非対応
- **Evidence**: `combobox.tsx:99-112` `<Badge onClick=...>` で `role`/`tabIndex` なし、ネストインタラクティブ要素 (button > clickable div) で WCAG 違反
- **Fix**: 削除アイコンを別 `<button type="button" aria-label="${label}を削除">` に分離

### 3. `DialogContent` の `aria-describedby` 必須要件
- **Evidence**: `dialog.tsx:31-61` Radix `react-dialog@^1.1.2` は `DialogDescription` または `aria-describedby` 無しで開発時 console warning
- **Fix**: `DialogContent` に `aria-describedby={undefined}` パススルー、または `VisuallyHidden` で必須 description 強制

### 4. `react-day-picker@^8.10.1` ピン留め (v9 非互換)
- **Evidence**: `package.json:37` で v8 のみ動作する API (`classNames` keys, `IconLeft/IconRight`)。`shadcn add calendar` を後で実行すると v9 コードが混入し競合
- **Fix**: `package.json` に `"~8.10.1"` 固定 + ADR / README に明記、中期 v9 移行 ADR 起票

### 5. `Combobox` cmdk の filter 設計が同姓同名で破綻 + 残存検索文字列
- **Evidence**: `combobox.tsx:191-200` `Item value={opt.label}` で重複ラベル時に選択挙動が片方固定、Input controlled 化欠如で再オープン時に検索文字列残存
- **Fix**: `Item value={opt.value}` (一意 ID)、`filter` prop で `(value, search) => optionLabel.includes(search) ? 1 : 0`、Input controlled 化、`onOpenChange` で `setSearch('')`

## Minor Findings

m-1 (Calendar nav 親に `relative` 不在), m-2 (Badge forwardRef 化されてない), m-3 (DialogContent close で focus-visible 不統一), m-4 (toast.tsx vs sonner.tsx 二重 export 整理), m-5 (DatePicker 和文 format 検討), m-6 (Combobox cmdGroupCls 上書き不可), m-7 (forms.ts の CareLink 表記)

## What's Missing

- Toast announce / aria-live 方針 (デフォルト polite だが医療系は assertive 切替)
- RadioGroup / Tooltip / DropdownMenu プリミティブ未追加
- Skeleton/Spinner/LoadingButton 統合 (mutation busy 表示)
- Storybook / Ladle 等 視覚回帰テスト機構なし
- forms.ts のテスト不在 (型崩壊検出に必須)
- eslint-plugin-jsx-a11y の有無 (Combobox Badge 違反は lint で拾える)

## Verdict Justification

shadcn 標準テンプレに沿い、displayName + forwardRef + tokens.css 参照は合格水準。CRITICAL 1 + MAJOR 4 は W1-G 以降確実に表面化するため修正推奨。修正なしで W2 量産を始めると 2-3 日のリワーク見込み。

## Open Questions

- frontend/lib/schemas/** の z.coerce/z.transform 使用箇所
- docs/design/01-design-system.md の実在確認
- RadioGroup / Tooltip / DropdownMenu 追加スコープの計画
- shadcn CLI vs 手書きコピーの運用方針
