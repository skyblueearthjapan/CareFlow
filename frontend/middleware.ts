import { NextResponse, type NextRequest } from 'next/server';
import { auth } from '@/lib/auth';
import { decideRoute } from '@/lib/middleware-routing';

export default auth(
  (
    req: NextRequest & {
      auth?: { user?: { role?: string; mustChangePassword?: boolean } } | null;
    },
  ) => {
    const decision = decideRoute({
      pathname: req.nextUrl.pathname,
      session: req.auth ?? null,
    });

    if (decision.kind === 'next') return NextResponse.next();

    const url = new URL(decision.to, req.nextUrl.origin);
    if (decision.preserveCallback) {
      url.searchParams.set('callbackUrl', req.nextUrl.pathname);
    }
    return NextResponse.redirect(url);
  },
);

export const config = {
  // /api/* と /login と Next.js 内部資産 + PWA assets は除外。
  // /api/auth/* (NextAuth signOut 等) も api 除外で守られるため、
  // 強制パスワード変更画面からログアウトできる。
  // brand/ = らく助ブランド画像 (ログイン画面でも未認証で読めること)
  matcher: [
    '/((?!api|_next/static|_next/image|favicon.ico|icon.png|apple-icon.png|login|manifest.webmanifest|sw.js|offline.html|icons/|brand/).*)',
  ],
};
