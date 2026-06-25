/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,jsx}',
  ],
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        ben: {
          bg: 'var(--ben-bg-surface)',
          elevated: 'var(--ben-bg-elevated)',
          border: 'var(--ben-border)',
          text: 'var(--ben-text)',
          muted: 'var(--ben-text-muted)',
          accent: 'var(--ben-accent)',
          danger: 'var(--ben-danger)',
        },
      },
      fontSize: {
        '2xs': ['0.68rem', { lineHeight: '1.25' }],
      },
      minHeight: {
        card: '7.5rem',
      },
    },
  },
  plugins: [],
}
