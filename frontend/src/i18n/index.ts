import { createI18n } from 'vue-i18n'
import { zh } from './zh'
import { en } from './en'
import { ja } from './ja'

export const LOCALES = [
  { code: 'zh', label: '中文' },
  { code: 'en', label: 'English' },
  { code: 'ja', label: '日本語' },
] as const

export type LocaleCode = (typeof LOCALES)[number]['code']

function detect(): LocaleCode {
  const saved = localStorage.getItem('locale') as LocaleCode | null
  if (saved && LOCALES.some((l) => l.code === saved)) return saved
  const nav = navigator.language.toLowerCase()
  if (nav.startsWith('ja')) return 'ja'
  if (nav.startsWith('zh')) return 'zh'
  if (nav.startsWith('en')) return 'en'
  return 'zh'
}

const initialLocale = detect()

export const i18n = createI18n({
  legacy: false,
  locale: initialLocale,
  fallbackLocale: 'en',
  messages: { zh, en, ja },
})

// Apply the detected locale to <html lang> on first load so locale-aware font
// stacks (see style.css :lang(zh)) take effect before the user switches.
document.documentElement.setAttribute('lang', initialLocale)

export function setLocale(code: LocaleCode) {
  ;(i18n.global.locale as any).value = code
  localStorage.setItem('locale', code)
  document.documentElement.setAttribute('lang', code)
}
