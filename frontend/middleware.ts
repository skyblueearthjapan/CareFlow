import { NextResponse, type NextRequest } from 'next/server';
import { auth } from '@/lib/auth';

const PUBLIC_PATHS = ['/login'];
const ADMIN_PREFIX = '/admin';
const COMMON_PREFIXES = ['/dashboard', '/patients', '/staff', '/schedule'];

export default auth((req: NextRequest & { auth?: { user?: { role?: string } } | null }) => {
  const { pathname } = req.nextUrl;
  const session = req.auth;
  const role = session?.user?.role;

  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  if (!session) {
    const loginUrl = new URL('/login', req.nextUrl.origin);
    loginUrl.searchParams.set('callbackUrl', pathname);
    return NextResponse.redirect(loginUrl);
  }

  if (pathname.startsWith(ADMIN_PREFIX) && role !== 'admin') {
    return NextResponse.redirect(new URL('/dashboard', req.nextUrl.origin));
  }

  if (
    COMMON_PREFIXES.some((p) => pathname.startsWith(p)) &&
    !['admin', 'manager', 'staff'].includes(role ?? '')
  ) {
    return NextResponse.redirect(new URL('/login', req.nextUrl.origin));
  }

  return NextResponse.next();
});

export const config = {
  // /api/* と /login と Next.js 内部資産 + PWA assets は除外
  matcher: [
    '/((?!api|_next/static|_next/image|favicon.ico|login|manifest.webmanifest|sw.js|offline.html|icons/).*)',
  ],
};
