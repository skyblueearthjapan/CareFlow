import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Tailwind 用の className 結合ヘルパー。 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
