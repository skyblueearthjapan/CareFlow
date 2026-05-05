import { zodResolver } from '@hookform/resolvers/zod';
import {
  useForm,
  type DefaultValues,
  type FieldValues,
  type UseFormProps,
  type UseFormReturn,
} from 'react-hook-form';
import { z, type ZodType, type ZodTypeDef } from 'zod';

/**
 * react-hook-form + zod 用ヘルパ。
 * 推奨利用例:
 *   const schema = z.object({ name: z.string().min(1) });
 *   const form = useFormWithSchema(schema, { name: '' });
 */
export function useFormWithSchema<
  TInput extends FieldValues,
  TSchema extends ZodType<unknown, ZodTypeDef, TInput>,
>(
  schema: TSchema,
  defaults?: DefaultValues<TInput>,
  options?: Omit<
    UseFormProps<TInput, unknown, z.output<TSchema>>,
    'resolver' | 'defaultValues'
  >,
): UseFormReturn<TInput, unknown, z.output<TSchema>> {
  return useForm<TInput, unknown, z.output<TSchema>>({
    ...options,
    resolver: zodResolver(schema),
    defaultValues: defaults,
  });
}

export type InferFormValues<TSchema extends ZodType> = z.infer<TSchema>;
