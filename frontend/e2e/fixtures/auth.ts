/**
 * Authentication fixture — Wave 5-C.
 *
 * Provides `loginAs(role)` which fills the credentials form and waits for the
 * post-login dashboard route. Real backend (FastAPI) must be running and
 * seeded with the matching test users; passwords are read from env so secrets
 * never live in the repository.
 *
 * Default test users (seed via `backend/scripts/create_admin.py`):
 *   admin    : E2E_ADMIN_EMAIL    / E2E_ADMIN_PASSWORD
 *   manager  : E2E_MANAGER_EMAIL  / E2E_MANAGER_PASSWORD
 *   staff    : E2E_STAFF_EMAIL    / E2E_STAFF_PASSWORD
 *
 * If env vars are missing the fixture falls back to local-dev defaults that
 * the seed script creates by default. CI overrides via repository secrets.
 */
import { test as base, expect, type Page } from '@playwright/test';

export type Role = 'admin' | 'manager' | 'staff';

interface Credentials {
  email: string;
  password: string;
}

const DEFAULTS: Record<Role, Credentials> = {
  admin: {
    email: process.env.E2E_ADMIN_EMAIL ?? 'admin@example.com',
    password: process.env.E2E_ADMIN_PASSWORD ?? 'secret-pass-01',
  },
  manager: {
    email: process.env.E2E_MANAGER_EMAIL ?? 'manager@example.com',
    password: process.env.E2E_MANAGER_PASSWORD ?? 'secret-pass-01',
  },
  staff: {
    email: process.env.E2E_STAFF_EMAIL ?? 'staff@example.com',
    password: process.env.E2E_STAFF_PASSWORD ?? 'secret-pass-01',
  },
};

/**
 * Drive the credentials form on `/login` and wait for the dashboard. Returns
 * the resolved credentials so individual specs can reference them in
 * subsequent assertions (e.g. profile menu shows the right email).
 */
export async function loginAs(page: Page, role: Role): Promise<Credentials> {
  const creds = DEFAULTS[role];
  await page.goto('/login');
  await page.getByLabel('メールアドレス').fill(creds.email);
  await page.getByLabel('パスワード').fill(creds.password);
  await page.getByRole('button', { name: 'ログイン' }).click();
  // The login page redirects to /dashboard on success.
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 10_000 });
  return creds;
}

interface AuthFixtures {
  loginAs: (role: Role) => Promise<Credentials>;
}

/**
 * Per-test fixture wrapper. Use `test('...', async ({ page, loginAs }) => {})`
 * instead of importing `loginAs` directly — keeps the spec terse and shares
 * the page instance.
 */
export const test = base.extend<AuthFixtures>({
  loginAs: async ({ page }, use) => {
    await use((role: Role) => loginAs(page, role));
  },
});

export { expect };
