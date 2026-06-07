import { defineStore } from 'pinia'

export const useThemeStore = defineStore('theme', {
  state: () => ({ dark: false }),
  actions: {
    init() {
      const saved = localStorage.getItem('theme')
      this.dark = saved ? saved === 'dark'
        : window.matchMedia('(prefers-color-scheme: dark)').matches
      this.apply()
    },
    apply() {
      document.documentElement.classList.toggle('dark', this.dark)
    },
    toggle() {
      this.dark = !this.dark
      localStorage.setItem('theme', this.dark ? 'dark' : 'light')
      this.apply()
    },
  },
})
