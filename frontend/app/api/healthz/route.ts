// Lightweight liveness endpoint for Docker healthcheck.
// - Bypasses NextAuth middleware (matcher excludes /api/*).
// - `force-dynamic` + `nodejs` runtime to avoid any static optimization /
//   edge-runtime surprises that could cache or fail under build-time eval.
export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

export async function GET() {
  return Response.json({ status: 'ok' }, { status: 200 });
}
