/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{vue,ts,js}'],
  theme: {
    extend: {
      colors: {
        // Warm washi-paper / sumi-ink neutral ramp (replaces cool gray everywhere)
        steel: {
          50: '#f8f4ea', // 生成り cream
          100: '#f1ebdd',
          200: '#e2d8c4', // hairline borders
          300: '#cdbfa3',
          400: '#a89a87', // muted
          500: '#857655',
          600: '#645842',
          700: '#473f30', // 墨 ink
          800: '#2c2720',
          900: '#1d1813', // dark paper
          950: '#130f0a',
        },
        // 藍 (ai) indigo — primary accent. Kept under `accent`/`brand` so existing
        // utility classes keep working but now render traditional indigo.
        accent: {
          50: '#eef1f6', 100: '#dae2ee', 200: '#b5c4da', 300: '#8aa1c0',
          400: '#5f7da3', 500: '#3f5d83', 600: '#324b6c', 700: '#283c57',
          800: '#212f44', 900: '#1a2536',
        },
        brand: {
          50: '#eef1f6', 100: '#dae2ee', 200: '#b5c4da', 300: '#8aa1c0',
          400: '#5f7da3', 500: '#3f5d83', 600: '#324b6c', 700: '#283c57',
          800: '#212f44', 900: '#1a2536',
        },
        // 藍 alias
        ai: {
          50: '#eef1f6', 100: '#dae2ee', 200: '#b5c4da', 300: '#8aa1c0',
          400: '#5f7da3', 500: '#3f5d83', 600: '#324b6c', 700: '#283c57',
          800: '#212f44', 900: '#1a2536',
        },
        // 金 (kin) muted gold — elegant highlights
        kin: {
          300: '#d8be8c', 400: '#caa869', 500: '#b08d57', 600: '#937246', 700: '#765b38',
        },
        // 朱 (aka) vermillion — alerts
        aka: { 400: '#c46a52', 500: '#b3543f', 600: '#9a4634', 700: '#7d3829' },
        // 抹茶 (matcha) — success / running
        matcha: { 400: '#8a9a64', 500: '#6f7d4e', 600: '#5a663e', 700: '#475031' },
      },
      fontFamily: {
        serif: ['"Shippori Mincho"', '"Yu Mincho"', '"Hiragino Mincho ProN"', '"Songti SC"', 'serif'],
        sans: ['"Zen Maru Gothic"', 'ui-sans-serif', '"PingFang SC"', '"Microsoft YaHei"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'Menlo', 'Consolas', 'monospace'],
      },
      boxShadow: {
        soft: '0 1px 2px rgba(40,30,20,0.05), 0 6px 20px rgba(40,30,20,0.07)',
        glow: '0 2px 4px rgba(40,32,22,0.08), 0 14px 40px rgba(40,60,87,0.14)',
        'glow-sm': '0 4px 14px rgba(40,60,87,0.22)',
      },
      backgroundImage: {
        'accent-grad': 'linear-gradient(135deg, #3f5d83 0%, #283c57 100%)',
        'accent-grad-soft': 'linear-gradient(135deg, rgba(63,93,131,0.12), rgba(40,60,87,0.08))',
      },
      keyframes: {
        'fade-up': { '0%': { opacity: 0 }, '100%': { opacity: 1 } },
        pulseglow: { '0%,100%': { opacity: 1 }, '50%': { opacity: 0.5 } },
      },
      animation: {
        'fade-up': 'fade-up 0.3s ease both',
        pulseglow: 'pulseglow 2.4s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
