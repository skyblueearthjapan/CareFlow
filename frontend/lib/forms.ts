import { zodResolver } from '@hookform/resolvers/zod';
import {
  useForm,
  type DefaultValues,
  type UseFormProps,
  type UseFormReturn,
} from 'react-hook-form';
import type { z, ZodType } from 'zod';

/**
 * react-hook-form + zod 用ヘルパ。
 * 推奨利用例:
 *   const schema = z.object({ name: z.string().min(1) });
 *   const form = useFormWithSchema(schema, { name: '' });
 */
export function useFormWithSchema<TSchema extends ZodType>(
  schema: TSchema,
  defaults?: Partial<z.infer<TSchema>>,
  options?: Omit<
    UseFormProps<z.infer<TSchema>>,
    'resolver' | 'defaultValues'
  >,
): UseFormReturn<z.infer<TSchema>> {
  return useForm<z.infer<TSchema>>({
    ...options,
    resolver: zodResolver(schema),
    defaultValues: defaults as DefaultValues<z.infer<TSchema>> | undefined,
  });
}

export type InferFormValues<TSchema extends ZodType> = z.infer<TSchema>;
