import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#EEF2FF',
          100: '#E0E7FF',
          200: '#C7D2FE',
          300: '#A5B4FC',
          400: '#818CF8',
          500: '#6366F1',
          600: '#4F46E5',
          700: '#4338CA',
          800: '#3730A3',
          900: '#312E81',
        },
        slate: {
          50: '#F8FAFC',
          100: '#F1F5F9',
          200: '#E2E8F0',
          300: '#CBD5E1',
          400: '#94A3B8',
          500: '#64748B',
          600: '#475569',
          700: '#334155',
          800: '#1E293B',
          900: '#0F172A',
        },
        risk: {
          low: '#16A34A',
          medium: '#D97706',
          high: '#EA580C',
          critical: '#DC2626',
          // Not in the ticket's token list — added for real WCAG AA text
          // contrast. Even against pure white, low/medium/high's pinned
          // hex only reach ~3.2-3.6:1 for small text (axe flagged this as
          // a Serious violation on the Badge/Table stories); critical's
          // #DC2626 already clears 4.5:1 so has no separate -text token.
          // One shade darker in the same hue family (matching Tailwind's
          // own -700 step), used only where the color IS the text.
          'low-text': '#15803D',
          'medium-text': '#B45309',
          'high-text': '#C2410C',
        },
        // Landing page only — the dashboard itself stays light. A near-
        // black "radar screen" surface for the marketing hero, distinct
        // from slate-900 so it reads as its own register rather than a
        // dark-mode variant of the app.
        ink: {
          DEFAULT: '#05070C',
          panel: '#0B0F1A',
        },
        domain: {
          financial: '#2563EB',
          clinical: '#7C3AED',
          environmental: '#0D9488',
          // Same reasoning as risk's -text tokens: #0D9488 only reaches
          // ~3.74:1 against white for small text (financial/clinical both
          // clear 4.5:1 on their own, so neither needs one).
          'environmental-text': '#0F766E',
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'],
      },
      spacing: {
        1: '4px',
        2: '8px',
        3: '12px',
        4: '16px',
        6: '24px',
        8: '32px',
        12: '48px',
        16: '64px',
      },
      borderRadius: {
        sm: '6px',
        md: '8px',
        lg: '12px',
        xl: '16px',
        full: '9999px',
      },
    },
  },
  plugins: [],
} satisfies Config
