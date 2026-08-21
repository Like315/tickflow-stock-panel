import type { Config } from 'tailwindcss'
import animate from 'tailwindcss-animate'
import plugin from 'tailwindcss/plugin'
import tailwindColors from 'tailwindcss/colors'

const LIGHT_YELLOW_REPLACEMENT = {
  text: '37 99 235',
  background: '59 130 246',
  border: '96 165 250',
} as const

const lightYellowVariables: Record<string, string> = {}
const yellowScales: Record<'text' | 'background' | 'border', Record<'amber' | 'yellow', Record<string, string>>> = {
  text: { amber: {}, yellow: {} },
  background: { amber: {}, yellow: {} },
  border: { amber: {}, yellow: {} },
}

for (const family of ['amber', 'yellow'] as const) {
  const source = tailwindColors[family] as Record<string, string>
  for (const [shade, hex] of Object.entries(source)) {
    const value = hex.replace('#', '')
    const fallback = [0, 2, 4].map(offset => Number.parseInt(value.slice(offset, offset + 2), 16)).join(' ')
    for (const role of ['text', 'background', 'border'] as const) {
      const variable = `--ui-${role}-${family}-${shade}`
      yellowScales[role][family][shade] = `rgb(var(${variable}, ${fallback}) / <alpha-value>)`
      lightYellowVariables[variable] = LIGHT_YELLOW_REPLACEMENT[role]
    }
  }
}

// 设计语言 §6.0:暗色为主 + 电光蓝强调 + 等宽数字
export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    container: { center: true, padding: '1rem' },
    extend: {
      colors: {
        // §6.0.1 色板 — CSS variables 见 src/index.css
        base:      'hsl(var(--base) / <alpha-value>)',
        surface:   'hsl(var(--surface) / <alpha-value>)',
        elevated:  'hsl(var(--elevated) / <alpha-value>)',
        border:    'hsl(var(--border) / <alpha-value>)',
        foreground: 'hsl(var(--fg-primary) / <alpha-value>)',
        secondary:  'hsl(var(--fg-secondary) / <alpha-value>)',
        muted:      'hsl(var(--fg-muted) / <alpha-value>)',
        accent:     'hsl(var(--accent) / <alpha-value>)',
        // A 股语义色:仅用于价格 / K 线,不用于 UI 状态
        bull:       'hsl(var(--bull) / <alpha-value>)',
        bear:       'hsl(var(--bear) / <alpha-value>)',
        warning:    'hsl(var(--warning) / <alpha-value>)',
        danger:     'hsl(var(--danger) / <alpha-value>)',
      },
      textColor: yellowScales.text,
      backgroundColor: yellowScales.background,
      borderColor: yellowScales.border,
      fontFamily: {
        sans: ['Inter', '"HarmonyOS Sans SC"', '"PingFang SC"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"IBM Plex Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        card: '8px',
        btn: '6px',
        input: '4px',
        dialog: '12px',
      },
      transitionTimingFunction: {
        // §6.0.4 Linear/Vercel 同款缓动
        smooth: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
    },
  },
  plugins: [
    animate,
    plugin(({ addBase }) => {
      addBase({ 'html:not(.dark)': lightYellowVariables })
    }),
  ],
} satisfies Config
