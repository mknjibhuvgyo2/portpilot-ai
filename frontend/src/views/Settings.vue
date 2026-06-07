<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import WaIcon from '../components/WaIcon.vue'
import api from '../api/client'
import { useThemeStore } from '../stores/theme'
import { useAuthStore } from '../stores/auth'
import { LOCALES, setLocale, type LocaleCode } from '../i18n'

const { t, locale } = useI18n()
const theme = useThemeStore()
const auth = useAuthStore()
const oldPw = ref('')
const newPw = ref('')
const msg = ref('')

async function changePw() {
  msg.value = ''
  try {
    await api.post('/api/auth/change-password', { old_password: oldPw.value, new_password: newPw.value })
    msg.value = t('settings.pwUpdated')
    oldPw.value = newPw.value = ''
  } catch (e: any) {
    msg.value = '❌ ' + (e.response?.data?.detail || t('settings.pwFail'))
  }
}
function pickLang(code: LocaleCode) { setLocale(code) }

// --- config backup / migration ---
const cfgSecrets = ref(false)
const cfgMsg = ref('')
async function exportConfig() {
  const { data } = await api.get('/api/config/export', { params: { include_secrets: cfgSecrets.value } })
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'porthub-config.json'
  document.body.appendChild(a); a.click(); a.remove()
  URL.revokeObjectURL(a.href)
}
async function importConfig(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0]
  if (!f) return
  cfgMsg.value = ''
  try {
    const parsed = JSON.parse(await f.text())
    const { data: res } = await api.post('/api/config/import', { data: parsed })
    const a = res.added; const s = res.skipped
    cfgMsg.value = t('settings.cfgResult', {
      pa: a.providers, aa: a.aliases, poa: a.ports,
      ps: s.providers + s.aliases + s.ports,
    })
  } catch (err: any) {
    cfgMsg.value = '❌ ' + (err.response?.data?.detail || t('settings.cfgErr'))
  }
  input.value = ''
}

// --- reverse-proxy config exporter ---
const exp = ref({ kind: 'nginx' as 'nginx' | 'caddy', mode: 'gateway' as 'gateway' | 'direct', domain: '', tls: true })
const expOut = ref<{ content: string; filename: string; count: number } | null>(null)
const expMsg = ref('')
async function genConfig() {
  const { data } = await api.get('/api/exporters/reverse-proxy', {
    params: { kind: exp.value.kind, mode: exp.value.mode, domain: exp.value.domain, tls: exp.value.tls },
  })
  expOut.value = data
  expMsg.value = ''
}
function copyConfig() {
  if (!expOut.value) return
  navigator.clipboard?.writeText(expOut.value.content)
  expMsg.value = t('settings.exporterCopied'); setTimeout(() => (expMsg.value = ''), 1800)
}
function downloadConfig() {
  if (!expOut.value) return
  const blob = new Blob([expOut.value.content], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = expOut.value.filename
  document.body.appendChild(a); a.click(); a.remove()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="max-w-2xl space-y-5">
    <h1 class="heading"><WaIcon name="settings" :size="22" class="text-ai-700 dark:text-kin-400" /> {{ t('settings.title') }}</h1>

    <div class="card space-y-4">
      <h2 class="heading text-sm"><WaIcon name="palette" :size="18" /> {{ t('settings.appearance') }}</h2>
      <div class="flex items-center justify-between text-sm">
        <span class="text-steel-500 dark:text-steel-400">{{ t('settings.theme') }}</span>
        <button class="btn-ghost" @click="theme.toggle()">{{ theme.dark ? t('settings.toLight') : t('settings.toDark') }}</button>
      </div>
      <div class="flex items-center justify-between text-sm">
        <span class="text-steel-500 dark:text-steel-400">{{ t('settings.language') }}</span>
        <div class="flex overflow-hidden rounded-md border border-steel-300/70 dark:border-steel-700">
          <button v-for="l in LOCALES" :key="l.code" @click="pickLang(l.code)"
            class="px-3 py-1 text-xs transition-colors"
            :class="l.code === locale ? 'bg-accent-grad text-white' : 'bg-white/60 text-steel-500 hover:bg-white dark:bg-steel-800/50 dark:hover:bg-steel-800'">
            {{ l.label }}
          </button>
        </div>
      </div>
    </div>

    <div class="card space-y-3">
      <h2 class="heading text-sm"><WaIcon name="lock" :size="18" /> {{ t('settings.changePassword') }}</h2>
      <div><label class="label">{{ t('settings.currentPassword') }}</label><input v-model="oldPw" type="password" class="input" /></div>
      <div><label class="label">{{ t('settings.newPassword') }}</label><input v-model="newPw" type="password" class="input" /></div>
      <p v-if="msg" class="text-sm">{{ msg }}</p>
      <div class="flex justify-end"><button class="btn-primary" @click="changePw">{{ t('settings.updatePassword') }}</button></div>
    </div>

    <!-- config backup / migration -->
    <div v-if="auth.isAdmin" class="card space-y-3">
      <h2 class="heading text-sm"><WaIcon name="save" :size="18" /> {{ t('settings.cfgTitle') }}</h2>
      <p class="text-xs text-steel-400">{{ t('settings.cfgHint') }}</p>
      <label class="flex items-center gap-2 text-sm"><input v-model="cfgSecrets" type="checkbox" class="accent-ai-600" />{{ t('settings.cfgSecrets') }}</label>
      <div class="flex flex-wrap items-center gap-2">
        <button class="btn-primary" @click="exportConfig"><WaIcon name="download" :size="14" />{{ t('settings.cfgExport') }}</button>
        <label class="btn-ghost cursor-pointer"><WaIcon name="upload" :size="14" />{{ t('settings.cfgImport') }}<input type="file" accept=".json,application/json" class="hidden" @change="importConfig" /></label>
        <span v-if="cfgMsg" class="text-sm" :class="cfgMsg.startsWith('❌') ? 'text-aka-600' : 'text-matcha-600'">{{ cfgMsg }}</span>
      </div>
    </div>

    <div class="card space-y-2 text-sm text-steel-600 dark:text-steel-300">
      <h2 class="heading text-sm"><WaIcon name="network" :size="18" /> {{ t('settings.access') }}</h2>
      <p>• {{ t('settings.accessDirect') }}: <code class="rounded bg-steel-100 px-1 dark:bg-steel-800">http://IP:&lt;port&gt;/v1/chat/completions</code></p>
      <p>• {{ t('settings.accessGateway') }}: <code class="rounded bg-steel-100 px-1 dark:bg-steel-800">http://IP:8000/gw/&lt;slug&gt;/v1/chat/completions</code></p>
      <p>• {{ t('settings.accessDns') }}</p>
      <p class="text-steel-400">{{ t('settings.accessNote') }}</p>
    </div>

    <div class="card space-y-3">
      <h2 class="heading text-sm"><WaIcon name="network" :size="18" /> {{ t('settings.exporter') }}</h2>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="label">{{ t('settings.exporterServer') }}</label>
          <div class="flex overflow-hidden rounded-md border border-steel-300/70 dark:border-steel-700">
            <button v-for="k in (['nginx','caddy'] as const)" :key="k" @click="exp.kind = k"
              class="flex-1 px-3 py-1.5 text-xs capitalize transition-colors"
              :class="exp.kind === k ? 'bg-accent-grad text-white' : 'bg-white/60 text-steel-500 dark:bg-steel-800/50'">{{ k }}</button>
          </div>
        </div>
        <div>
          <label class="label">{{ t('settings.exporterMode') }}</label>
          <div class="flex overflow-hidden rounded-md border border-steel-300/70 dark:border-steel-700">
            <button v-for="m in (['gateway','direct'] as const)" :key="m" @click="exp.mode = m"
              class="flex-1 px-2 py-1.5 text-xs transition-colors"
              :class="exp.mode === m ? 'bg-accent-grad text-white' : 'bg-white/60 text-steel-500 dark:bg-steel-800/50'">
              {{ m === 'gateway' ? t('settings.modeGateway') : t('settings.modeDirect') }}</button>
          </div>
        </div>
      </div>
      <p class="text-xs text-steel-400">{{ exp.mode === 'gateway' ? t('settings.modeGatewayHint') : t('settings.modeDirectHint') }}</p>
      <div>
        <label class="label">{{ t('settings.exporterDomain') }}</label>
        <input v-model="exp.domain" class="input" placeholder="ai.example.lan" />
        <p class="mt-1 text-xs text-steel-400">{{ t('settings.exporterDomainHint') }}</p>
      </div>
      <label v-if="exp.kind === 'caddy'" class="flex items-center gap-2 text-sm text-steel-600 dark:text-steel-300">
        <input v-model="exp.tls" type="checkbox" class="accent-ai-600" /> {{ t('settings.exporterTls') }}
      </label>
      <div class="flex items-center justify-between">
        <span v-if="expMsg" class="text-xs text-matcha-600">{{ expMsg }}</span>
        <span v-else-if="expOut" class="text-xs text-steel-400">{{ expOut.count }} {{ t('settings.exporterCount') }}</span>
        <span v-else></span>
        <button class="btn-primary" @click="genConfig"><WaIcon name="file" :size="14" />{{ t('settings.exporterGenerate') }}</button>
      </div>
      <div v-if="expOut" class="space-y-2">
        <pre class="max-h-80 overflow-auto rounded-lg bg-steel-900 p-3 font-mono text-[11px] leading-relaxed text-steel-100 dark:bg-steel-950">{{ expOut.content }}</pre>
        <div class="flex justify-end gap-2">
          <button class="btn-ghost" @click="copyConfig"><WaIcon name="copy" :size="14" />{{ t('common.copy') }}</button>
          <button class="btn-ghost" @click="downloadConfig"><WaIcon name="download" :size="14" />{{ t('settings.exporterDownload') }}</button>
        </div>
      </div>
      <p v-else class="text-xs text-steel-400">{{ t('settings.exporterEmpty') }}</p>
    </div>

    <div class="card font-mono text-xs text-steel-500 dark:text-steel-400">
      {{ t('settings.currentUser') }}: {{ auth.username }} <span v-if="auth.isAdmin">· ADMIN</span> · PORTHUB v0.1.0
    </div>
  </div>
</template>
