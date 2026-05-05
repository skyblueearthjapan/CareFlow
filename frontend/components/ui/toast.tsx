'use client';

/**
 * Toast プリミティブ。CareLink frontend は sonner ベースで統一する。
 * ここでは sonner.tsx の Toaster / toast を再公開し、UI から
 * `@/components/ui/toast` でも import できるよう薄いラッパを提供する。
 */
export { Toaster, toast } from '@/components/ui/sonner';
