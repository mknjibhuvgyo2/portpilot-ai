<script setup lang="ts">
import { computed, ref, watchEffect } from 'vue'
import { RouterLink, RouterView, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import WaIcon from './WaIcon.vue'
import { useThemeStore } from '../stores/theme'
import { useAuthStore } from '../stores/auth'
import { useUiStore } from '../stores/ui'
import { LOCALES, setLocale, type LocaleCode } from '../i18n'

const { t, locale } = useI18n()
const theme = useThemeStore()
const auth = useAuthStore()
const ui = useUiStore()
const router = useRouter()
const langOpen = ref(false)

const nav = [
  { to: '/dashboard', key: 'dashboard', icon: 'dashboard' },
  { to: '/ports', key: 'ports', icon: 'ports' },
  { to: '/taskflows', key: 'taskflows', icon: 'sliders' },
  { to: '/models', key: 'models', icon: 'models' },
  { to: '/promptlab', key: 'promptlab', icon: 'spark' },
  { to: '/keys', key: 'keys', icon: 'keys' },
  { to: '/chat', key: 'chat', icon: 'chat' },
  { to: '/users', key: 'users', icon: 'users', adminOnly: true },
  { to: '/settings', key: 'settings', icon: 'settings' },
]
const visibleNav = computed(() => nav.filter((n) => !n.adminOnly || auth.isAdmin))

// Expose the sidebar width so modal overlays can avoid covering the nav.
watchEffect(() => {
  document.documentElement.style.setProperty('--sidebar-w', ui.sidebarCollapsed ? '66px' : '240px')
})

function pickLang(code: LocaleCode) { setLocale(code); langOpen.value = false }
function logout() { auth.logout(); router.push('/login') }
</script>

<template>
  <div class="flex h-full bg-steel-100 font-sans text-steel-900 dark:bg-steel-950 dark:text-steel-100">
    <!-- Sidebar -->
    <aside
      class="relative flex flex-col border-r border-steel-200/70 bg-steel-50/70 backdrop-blur-xl transition-[width] duration-300 ease-in-out dark:border-steel-800 dark:bg-steel-900/60"
      :class="ui.sidebarCollapsed ? 'w-[66px]' : 'w-60'">
      <!-- brand + collapse (the logo itself toggles the sidebar) -->
      <div class="flex h-16 items-center gap-2.5 border-b border-steel-200/70 px-3 dark:border-steel-800">
        <button type="button" :title="t('header.toggleNav')" @click="ui.toggleSidebar()"
          class="logo-mark group relative shrink-0 transition hover:brightness-110">
          <WaIcon name="crest" :size="20" class="transition-opacity group-hover:opacity-0" />
          <WaIcon :name="ui.sidebarCollapsed ? 'panelOpen' : 'panelClose'" :size="17"
            class="absolute inset-0 m-auto opacity-0 transition-opacity group-hover:opacity-100" />
        </button>
        <transition name="fade">
          <span v-if="!ui.sidebarCollapsed" class="font-serif text-base font-bold tracking-[0.15em]">
            <span class="grad-text">PORT</span>HUB
          </span>
        </transition>
      </div>

      <!-- nav -->
      <nav class="flex-1 space-y-1 overflow-hidden px-2.5 py-4">
        <RouterLink v-for="n in visibleNav" :key="n.to" :to="n.to" :title="t('nav.' + n.key)"
          class="nav-item" active-class="nav-item-active">
          <span class="absolute -left-2.5 h-5 w-1 rounded-r bg-accent-grad opacity-0 transition-opacity"
            :class="$route.path === n.to ? 'opacity-100' : ''"></span>
          <WaIcon :name="n.icon" :size="19" class="shrink-0" />
          <transition name="fade">
            <span v-if="!ui.sidebarCollapsed" class="whitespace-nowrap">{{ t('nav.' + n.key) }}</span>
          </transition>
        </RouterLink>
      </nav>

      <div class="border-t border-steel-200/70 px-3 py-3 dark:border-steel-800">
        <transition name="fade">
          <span v-if="!ui.sidebarCollapsed" class="font-mono text-[10px] tracking-widest text-steel-400">
            v0.1.0 · {{ t('app.selfHosted') }}
          </span>
        </transition>
      </div>
    </aside>

    <!-- Main -->
    <div class="flex flex-1 flex-col overflow-hidden">
      <header class="z-10 flex h-16 items-center justify-between border-b border-steel-200/70 bg-steel-50/70 px-6 backdrop-blur-xl dark:border-steel-800 dark:bg-steel-900/60">
        <div class="font-serif text-xs tracking-[0.22em] text-steel-400">{{ t('app.tagline') }}</div>
        <div class="flex items-center gap-2">
          <!-- language -->
          <div class="relative">
            <div v-if="langOpen" class="fixed inset-0 z-10" @click="langOpen = false"></div>
            <button class="btn-ghost" @click="langOpen = !langOpen">
              <WaIcon name="lang" :size="15" />
              {{ LOCALES.find((l) => l.code === locale)?.label }}
            </button>
            <transition name="slide">
              <div v-if="langOpen"
                class="absolute right-0 z-20 mt-1 w-32 overflow-hidden rounded-md border border-steel-200 bg-steel-50 py-1 shadow-soft dark:border-steel-700 dark:bg-steel-800">
                <button v-for="l in LOCALES" :key="l.code" @mousedown.prevent="pickLang(l.code)"
                  class="flex w-full items-center px-3 py-1.5 text-left text-sm hover:bg-steel-200/60 dark:hover:bg-steel-700"
                  :class="l.code === locale ? 'font-semibold text-ai-700 dark:text-kin-400' : ''">
                  {{ l.label }}
                </button>
              </div>
            </transition>
          </div>
          <button class="btn-ghost" @click="theme.toggle()">
            <WaIcon :name="theme.dark ? 'sun' : 'moon'" :size="15" />
            {{ theme.dark ? t('header.light') : t('header.dark') }}
          </button>
          <div class="hidden items-center gap-1.5 font-mono text-xs text-steel-500 sm:flex dark:text-steel-400">
            {{ auth.username }}
            <span v-if="auth.isAdmin" class="chip border border-kin-400/40 bg-kin-400/10 text-kin-600 dark:text-kin-400">官</span>
          </div>
          <button class="btn-ghost" @click="logout"><WaIcon name="logout" :size="15" />{{ t('header.logout') }}</button>
        </div>
      </header>
      <main class="bg-washi flex-1 overflow-auto p-6">
        <div class="animate-fade-up"><RouterView /></div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
