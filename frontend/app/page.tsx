import { headers } from 'next/headers';
import { redirect } from 'next/navigation';

const MOBILE_UA = /Android|iPhone|iPad|iPod|Opera Mini|IEMobile|Mobile/i;

export default async function RootPage(): Promise<never> {
  const headerList = await headers();
  const ua = headerList.get('user-agent') ?? '';
  if (MOBILE_UA.test(ua)) {
    redirect('/m/home');
  }
  redirect('/dashboard');
}
