'use client';

import { Toaster as SonnerToaster, type ToasterProps } from 'sonner';

/**
 * sonner-based toast container.
 * 既定で右上配置・rich color 表示。toast() / sonner export はそのまま再公開。
 */
export function Toaster(props: ToasterProps) {
  return (
    <SonnerToaster
      position="top-right"
      richColors
      closeButton
      toastOptions={{
        classNames: {
          toast:
            'group toast bg-bg-base text-text-primary border border-border-default shadow-lg rounded-md',
          description: 'text-text-muted',
          actionButton: 'bg-brand-primary text-white',
          cancelButton: 'bg-bg-muted text-text-primary',
        },
      }}
      {...props}
    />
  );
}

export { toast } from 'sonner';
