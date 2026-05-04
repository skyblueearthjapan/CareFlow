import { z } from 'zod';

/**
 * Server-side environment variables.
 * Validated at module load. NextAuth runs server-side so NEXT_PUBLIC_ is not required;
 * we still fall back to a sensible local default for dev convenience.
 */
const serverEnvSchema = z.object({
  BACKEND_API_BASE_URL: z.string().url().default('http://localhost:8000'),
});

export type ServerEnv = z.infer<typeof serverEnvSchema>;

const parsed = serverEnvSchema.safeParse({
  BACKEND_API_BASE_URL:
    process.env.BACKEND_API_BASE_URL ??
    process.env.NEXT_PUBLIC_BACKEND_API_BASE_URL ??
    'http://localhost:8000',
});

if (!parsed.success) {
  throw new Error(
    `Invalid server environment: ${parsed.error.issues
      .map((i) => `${i.path.join('.')}: ${i.message}`)
      .join(', ')}`,
  );
}

export const env: ServerEnv = parsed.data;
