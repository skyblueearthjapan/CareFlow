import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  // Phase G-15: 性別制限色 (女性=赤/男性=青) を静的解析で見落とさないよう明示
  safelist: ['text-red-600', 'text-blue-600'],
  theme: {
    extend: {
      colors: {
        // Brand
        'brand-primary': 'var(--brand-primary)',
        'brand-primary-hover': 'var(--brand-primary-hover)',
        'brand-primary-light': 'var(--brand-primary-light)',
        'brand-primary-50': 'var(--brand-primary-50)',
        'brand-accent': 'var(--brand-accent)',
        'brand-accent-light': 'var(--brand-accent-light)',
        // Surfaces / borders / text
        'bg-base': 'var(--bg-base)',
        'bg-app': 'var(--bg-app)',
        'bg-muted': 'var(--bg-muted)',
        'bg-window': 'var(--color-bg-window, #FAF7F2)',
        'border-default': 'var(--border-default)',
        'border-subtle': 'var(--border-subtle)',
        'border-strong': 'var(--border-strong)',
        'text-primary': 'var(--text-primary)',
        'text-secondary': 'var(--text-secondary)',
        'text-muted': 'var(--text-muted)',
        // データ系カラー (業務種別)
        'c-coupled': 'var(--c-coupled)',
        'c-coupled-bg': 'var(--c-coupled-bg)',
        // Semantic
        success: 'var(--success)',
        warning: 'var(--warning)',
        error: 'var(--error)',
        info: 'var(--info)',
      },
      borderRadius: {
        sm: 'var(--radius-sm)',
        DEFAULT: 'var(--radius)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        xl: 'var(--radius-xl)',
      },
      boxShadow: {
        xs: 'var(--shadow-xs)',
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        xl: 'var(--shadow-xl)',
        'outer-card': 'var(--shadow-outer-card)',
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        serif: ['var(--font-serif)'],
        mono: ['var(--font-mono)'],
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
};

export default config;
