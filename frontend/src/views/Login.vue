<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import WaIcon from '../components/WaIcon.vue'
import { useAuthStore } from '../stores/auth'
import { useThemeStore } from '../stores/theme'
import { LOCALES, setLocale, type LocaleCode } from '../i18n'

const { t, locale } = useI18n()
const auth = useAuthStore()
const theme = useThemeStore()
const router = useRouter()
const username = ref('admin')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    router.push('/dashboard')
  } catch (e: any) {
    error.value = e.response?.data?.detail || t('login.error')
  } finally {
    loading.value = false
  }
}
function pickLang(code: LocaleCode) { setLocale(code) }
</script>

<template>
  <div class="bg-washi relative flex h-full items-center justify-center overflow-hidden font-sans">
    <!-- decorative motif panels -->
    <div class="tex-asanoha pointer-events-none absolute -left-10 top-0 h-full w-44 opacity-[0.12]"></div>
    <div class="tex-asanoha pointer-events-none absolute -right-10 top-0 h-full w-44 opacity-[0.12]"></div>
    <div class="pointer-events-none absolute -left-24 top-1/4 h-72 w-72 rounded-full bg-ai-500/10 blur-3xl"></div>
    <div class="pointer-events-none absolute -right-20 bottom-1/4 h-80 w-80 rounded-full bg-kin-400/10 blur-3xl"></div>

    <!-- top-right controls -->
    <div class="absolute right-4 top-4 flex items-center gap-2">
      <div class="flex overflow-hidden rounded-md border border-steel-300/70 text-xs dark:border-steel-700">
        <button v-for="l in LOCALES" :key="l.code" @click="pickLang(l.code)"
          class="px-2 py-1 transition-colors"
          :class="l.code === locale ? 'bg-accent-grad text-steel-50' : 'bg-steel-50/60 text-steel-500 hover:bg-steel-50 dark:bg-steel-800/50 dark:hover:bg-steel-800'">
          {{ l.label }}
        </button>
      </div>
      <button class="btn-ghost !px-2" @click="theme.toggle()">
        <WaIcon :name="theme.dark ? 'sun' : 'moon'" :size="15" />
      </button>
    </div>

    <div class="relative w-full max-w-sm animate-fade-up">
      <div class="mb-7 flex flex-col items-center text-center">
        <div class="logo-mark mb-4 !h-16 !w-16"><WaIcon name="crest" :size="36" /></div>
        <div class="font-serif text-3xl font-extrabold tracking-[0.2em]">
          <span class="grad-text">PORT</span>
          <span class="text-steel-800 dark:text-steel-100">HUB</span>
        </div>
        <div class="my-3 h-px w-40 bg-gradient-to-r from-transparent via-kin-400/60 to-transparent"></div>
        <p class="font-serif text-xs tracking-[0.2em] text-steel-500">{{ t('login.subtitle') }}</p>
      </div>
      <form class="card space-y-4 !p-6 shadow-glow" @submit.prevent="submit">
        <div>
          <label class="label">{{ t('login.username') }}</label>
          <input v-model="username" class="input" autocomplete="username" />
        </div>
        <div>
          <label class="label">{{ t('login.password') }}</label>
          <input v-model="password" type="password" class="input" autocomplete="current-password" />
        </div>
        <p v-if="error" class="text-sm text-aka-600">{{ error }}</p>
        <button class="btn-primary w-full !py-2.5 !text-sm" :disabled="loading">
          <WaIcon v-if="loading" name="spinner" :size="16" class="animate-spin" />
          {{ loading ? t('login.signingIn') : t('login.signIn') }}
        </button>
        <p class="text-center font-mono text-[10px] tracking-wider text-steel-400">
          {{ t('login.hint') }}
        </p>
      </form>
    </div>
  </div>
</template>
