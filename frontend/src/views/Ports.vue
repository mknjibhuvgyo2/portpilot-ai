<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import WaIcon from '../components/WaIcon.vue'
import api from '../api/client'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const ENDPOINT_BY_TYPE: Record<string, string> = { embedding: '/v1/embeddings', rerank: '/v1/rerank' }
const endpointPath = computed(() => ENDPOINT_BY_TYPE[form.value.app_type] || '/v1/chat/completions')

const ports = ref<any[]>([])
const templates = ref<any[]>([])
const aliases = ref<any[]>([])
const promptFiles = ref<any[]>([])
const showForm = ref(false)
const editingId = ref<number | null>(null)
const tab = ref<'basic' | 'model' | 'prompt' | 'runtime' | 'gateway'>('basic')
const logsFor = ref<any | null>(null)
const logs = ref<any[]>([])
const err = ref('')
const saveMsg = ref('')
const toast = ref('')
const selectedPromptFile = ref('')

function showToast(msg: string) { toast.value = msg; setTimeout(() => { if (toast.value === msg) toast.value = '' }, 3000) }

const tabs = [
  { key: 'basic', icon: 'ports' },
  { key: 'model', icon: 'models' },
  { key: 'prompt', icon: 'file' },
  { key: 'runtime', icon: 'sliders' },
  { key: 'gateway', icon: 'shield' },
] as const

const blank = () => ({
  name: '', slug: '', port: 9001, app_type: 'generic_chat', model_alias: '',
  system_prompt: '', streaming: true, concurrency: 8, timeout: 120,
  max_retries: 2, logging_enabled: true, log_keep: 10, auth_required: false, autostart: false,
})
const form = ref(blank())

async function load() { ports.value = (await api.get('/api/ports')).data }
async function loadMeta() {
  templates.value = (await api.get('/api/ports/templates')).data
  aliases.value = (await api.get('/api/models/aliases')).data
  promptFiles.value = (await api.get('/api/prompts')).data
}
onMounted(async () => {
  await Promise.all([load(), loadMeta()])
  const eid = route.query.edit
  if (eid) {
    const p = ports.value.find((x) => String(x.id) === String(eid))
    if (p) openEdit(p)
  }
})

function onAppTypeChange() {
  // prefill the template's suggested system prompt if the user hasn't typed one
  const tpl = templates.value.find((t) => t.app_type === form.value.app_type)
  if (tpl && tpl.default_prompt && !form.value.system_prompt) form.value.system_prompt = tpl.default_prompt
}
function onAliasSelect() {
  // bottom "+ new route" option jumps to the route editor (opens create modal)
  if (form.value.model_alias === '__new__') {
    form.value.model_alias = ''
    router.push('/models?new=alias')
  }
}

function openCreate() { editingId.value = null; form.value = blank(); tab.value = 'basic'; err.value = ''; showForm.value = true }
function openEdit(p: any) { editingId.value = p.id; form.value = { ...blank(), ...p }; tab.value = 'basic'; err.value = ''; showForm.value = true }
function closeForm() { showForm.value = false; if (route.query.edit) router.replace({ path: '/ports' }) }

async function save() {
  err.value = ''
  try {
    if (editingId.value) {
      const { name, model_alias, system_prompt, streaming, concurrency, timeout,
        max_retries, logging_enabled, log_keep, auth_required, autostart } = form.value
      const { data } = await api.patch(`/api/ports/${editingId.value}`, {
        name, model_alias, system_prompt, streaming, concurrency, timeout,
        max_retries, logging_enabled, log_keep, auth_required, autostart,
      })
      if (data?.hot_swapped) showToast(t('ports.hotSwapped'))
    } else {
      await api.post('/api/ports', form.value)
    }
    closeForm()
    await load()
  } catch (e: any) { err.value = e.response?.data?.detail || 'error' }
}
async function start(p: any) { await api.post(`/api/ports/${p.id}/start`); await load() }
async function stop(p: any) { await api.post(`/api/ports/${p.id}/stop`); await load() }
async function remove(p: any) {
  if (!confirm(`${t('common.delete')} ${p.name}?`)) return
  await api.delete(`/api/ports/${p.id}`); await load()
}
async function openLogs(p: any) { logsFor.value = p; logs.value = (await api.get(`/api/ports/${p.id}/logs`)).data }

// ---- prompt file read/write ----
function onPromptFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f) return
  const r = new FileReader()
  r.onload = () => {
    let text = String(r.result || '')
    if (f.name.toLowerCase().endsWith('.json')) {
      try {
        const j = JSON.parse(text)
        if (typeof j === 'string') text = j
        else if (j && typeof j === 'object') text = j.system_prompt ?? j.content ?? j.prompt ?? j.text ?? text
      } catch { /* keep raw */ }
    }
    form.value.system_prompt = text
  }
  r.readAsText(f)
  ;(e.target as HTMLInputElement).value = ''
}
function download(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}
function exportTxt() { download(`${form.value.slug || 'prompt'}.txt`, form.value.system_prompt) }
function exportJson() { download(`${form.value.slug || 'prompt'}.json`, JSON.stringify({ system_prompt: form.value.system_prompt }, null, 2)) }

async function loadFromLibrary() {
  if (!selectedPromptFile.value) return
  const { data } = await api.get(`/api/prompts/${encodeURIComponent(selectedPromptFile.value)}`)
  form.value.system_prompt = data.content
}
async function saveToLibrary() {
  const name = prompt(t('ports.saveAsName'), form.value.slug || 'prompt')
  if (!name) return
  const fmt = name.toLowerCase().endsWith('.json') ? 'json' : 'txt'
  await api.post('/api/prompts', { name, content: form.value.system_prompt, format: fmt })
  promptFiles.value = (await api.get('/api/prompts')).data
  saveMsg.value = t('ports.saved'); setTimeout(() => (saveMsg.value = ''), 2000)
}
</script>

<template>
  <div class="space-y-5">
    <transition name="fade">
      <div v-if="toast" class="fixed right-5 top-5 z-[60] flex items-center gap-2 rounded-lg border border-matcha-500/40 bg-matcha-500/12 px-4 py-2.5 text-sm font-medium text-matcha-700 shadow-glow backdrop-blur dark:text-matcha-300">
        <WaIcon name="power" :size="16" /> {{ toast }}
      </div>
    </transition>
    <div class="flex items-center justify-between">
      <h1 class="heading"><WaIcon name="ports" :size="22" class="text-ai-700 dark:text-kin-400" /> {{ t('ports.title') }}</h1>
      <button v-if="auth.isAdmin" class="btn-primary" @click="openCreate"><WaIcon name="plus" :size="15" />{{ t('ports.newService') }}</button>
    </div>

    <div class="card overflow-x-auto p-0">
      <table class="w-full text-sm">
        <thead class="border-b border-steel-200/70 text-left dark:border-steel-800">
          <tr class="[&>th]:label [&>th]:px-4 [&>th]:py-3">
            <th>{{ t('ports.name') }}</th><th>{{ t('ports.port') }}</th><th>{{ t('ports.path') }}</th>
            <th>{{ t('ports.modelAlias') }}</th><th>{{ t('ports.status') }}</th><th class="text-right">{{ t('ports.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in ports" :key="p.id"
            class="cursor-pointer border-b border-steel-100 transition-colors last:border-0 hover:bg-accent-50/50 dark:border-steel-800/60 dark:hover:bg-steel-800/40"
            @click="openEdit(p)">
            <td class="px-4 py-3 font-medium">{{ p.name }}</td>
            <td class="stat px-4 py-3">{{ p.port }}</td>
            <td class="px-4 py-3 font-mono text-xs text-steel-500">/gw/{{ p.slug }}/</td>
            <td class="px-4 py-3">{{ p.model_alias || '—' }}</td>
            <td class="px-4 py-3">
              <span class="chip" :class="p.status === 'running' ? 'bg-matcha-500/12 text-matcha-600' : 'bg-steel-500/10 text-steel-400'">
                <span class="dot" :class="p.status === 'running' ? 'bg-matcha-500' : 'bg-steel-400'"></span>{{ p.status }}
              </span>
            </td>
            <td class="px-4 py-3" @click.stop>
              <div class="flex justify-end gap-2">
                <button class="btn-ghost" @click="router.push({ path: '/chat', query: { port: p.id } })"><WaIcon name="chat" :size="14" />{{ t('common.test') }}</button>
                <button class="btn-ghost" @click="openLogs(p)"><WaIcon name="logs" :size="14" />{{ t('common.logs') }}</button>
                <template v-if="auth.isAdmin">
                  <button class="btn-ghost" @click="openEdit(p)"><WaIcon name="edit" :size="14" />{{ t('common.edit') }}</button>
                  <button v-if="p.status !== 'running'" class="btn-primary" @click="start(p)"><WaIcon name="play" :size="14" />{{ t('common.start') }}</button>
                  <button v-else class="btn-ghost" @click="stop(p)"><WaIcon name="stop" :size="14" />{{ t('common.stop') }}</button>
                  <button class="btn-danger" @click="remove(p)"><WaIcon name="trash" :size="14" /></button>
                </template>
              </div>
            </td>
          </tr>
          <tr v-if="!ports.length"><td colspan="6" class="px-4 py-10 text-center text-steel-400">{{ t('ports.noServices') }}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Create / Edit modal with tabs -->
    <div v-if="showForm" class="fixed inset-y-0 right-0 left-[var(--sidebar-w,0px)] z-50 flex items-center justify-center overflow-y-auto bg-steel-950/50 p-4 backdrop-blur-sm" @click.self="closeForm">
      <div class="card flex h-[88vh] w-[94vw] max-w-[1700px] flex-col animate-fade-up !p-0 shadow-glow">
        <div class="flex items-center justify-between border-b border-steel-200/70 px-5 py-4 dark:border-steel-800">
          <h2 class="heading text-sm">{{ editingId ? t('ports.editTitle') : t('ports.createTitle') }}</h2>
          <button class="btn-ghost !px-2" @click="closeForm">✕</button>
        </div>
        <!-- tabs -->
        <div class="flex gap-1 border-b border-steel-200/70 px-3 pt-2 dark:border-steel-800">
          <button v-for="tb in tabs" :key="tb.key" @click="tab = tb.key as any"
            class="flex items-center gap-1.5 rounded-t-md px-3 py-2 text-xs font-semibold transition-colors"
            :class="tab === tb.key ? 'border-b-2 border-accent-500 text-accent-600 dark:text-accent-300' : 'text-steel-400 hover:text-steel-700 dark:hover:text-steel-200'">
            <WaIcon :name="tb.icon" :size="15" />{{ t('ports.tabs.' + tb.key) }}
          </button>
        </div>

        <div class="flex-1 overflow-auto p-5">
          <!-- basic -->
          <div v-show="tab === 'basic'" class="grid grid-cols-2 gap-3">
            <div><label class="label">{{ t('ports.name') }}</label><input v-model="form.name" class="input" /></div>
            <div><label class="label">{{ t('ports.slug') }}</label><input v-model="form.slug" class="input disabled:opacity-50" :disabled="!!editingId" placeholder="score" /></div>
            <div><label class="label">{{ t('ports.port') }}</label><input v-model.number="form.port" type="number" class="input disabled:opacity-50" :disabled="!!editingId" /></div>
            <div>
              <label class="label">{{ t('ports.appType') }}</label>
              <select v-model="form.app_type" class="input disabled:opacity-50" :disabled="!!editingId" @change="onAppTypeChange">
                <option v-for="tp in templates" :key="tp.app_type" :value="tp.app_type">{{ tp.title }}</option>
              </select>
            </div>
          </div>

          <!-- model -->
          <div v-show="tab === 'model'" class="space-y-3">
            <div>
              <label class="label">{{ t('ports.modelAlias') }}</label>
              <select v-model="form.model_alias" class="input" @change="onAliasSelect">
                <option value="">{{ t('ports.selectAlias') }}</option>
                <option v-for="a in aliases" :key="a.id" :value="a.alias">{{ a.alias }}</option>
                <option value="__new__">＋ {{ t('ports.newAliasInline') }}</option>
              </select>
            </div>
            <p class="text-xs text-steel-400">{{ t('models.noAliases') }}</p>
          </div>

          <!-- prompt -->
          <div v-show="tab === 'prompt'" class="space-y-3">
            <div class="flex flex-wrap items-center gap-2">
              <label class="btn-ghost cursor-pointer">
                <WaIcon name="upload" :size="14" />{{ t('ports.importFile') }}
                <input type="file" accept=".txt,.json" class="hidden" @change="onPromptFile" />
              </label>
              <button class="btn-ghost" @click="exportTxt"><WaIcon name="download" :size="14" />{{ t('ports.exportTxt') }}</button>
              <button class="btn-ghost" @click="exportJson"><WaIcon name="download" :size="14" />{{ t('ports.exportJson') }}</button>
              <div class="ml-auto flex items-center gap-2">
                <select v-model="selectedPromptFile" class="input !w-40 !py-1.5 text-xs">
                  <option value="">{{ t('ports.promptLibrary') }}</option>
                  <option v-for="f in promptFiles" :key="f.name" :value="f.name">{{ f.name }}</option>
                </select>
                <button class="btn-ghost" :disabled="!selectedPromptFile" @click="loadFromLibrary"><WaIcon name="folder" :size="14" />{{ t('ports.loadLibrary') }}</button>
                <button class="btn-ghost" @click="saveToLibrary"><WaIcon name="save" :size="14" />{{ t('ports.saveAs') }}</button>
              </div>
            </div>
            <textarea v-model="form.system_prompt" rows="12" class="input font-mono text-xs leading-relaxed"
              :placeholder="t('ports.systemPrompt')"></textarea>
            <p v-if="saveMsg" class="text-xs text-matcha-600">{{ saveMsg }}</p>
          </div>

          <!-- runtime -->
          <div v-show="tab === 'runtime'" class="space-y-4">
            <div class="grid grid-cols-2 gap-3">
              <div><label class="label">{{ t('ports.concurrency') }}</label><input v-model.number="form.concurrency" type="number" class="input" /></div>
              <div><label class="label">{{ t('ports.timeout') }}</label><input v-model.number="form.timeout" type="number" class="input" /></div>
              <div><label class="label">{{ t('ports.retries') }}</label><input v-model.number="form.max_retries" type="number" class="input" /></div>
              <div><label class="label">{{ t('ports.logKeep') }}</label><input v-model.number="form.log_keep" type="number" class="input" /></div>
            </div>
            <div class="flex flex-wrap gap-5 text-xs">
              <label class="flex items-center gap-2"><input v-model="form.streaming" type="checkbox" class="accent-accent-600" />{{ t('ports.streaming') }}</label>
              <label class="flex items-center gap-2"><input v-model="form.logging_enabled" type="checkbox" class="accent-accent-600" />{{ t('ports.enableLog') }}</label>
            </div>
          </div>

          <!-- gateway -->
          <div v-show="tab === 'gateway'" class="space-y-4">
            <div class="flex flex-wrap gap-5 text-xs">
              <label class="flex items-center gap-2"><input v-model="form.auth_required" type="checkbox" class="accent-accent-600" />{{ t('ports.authRequired') }}</label>
              <label class="flex items-center gap-2"><input v-model="form.autostart" type="checkbox" class="accent-accent-600" />{{ t('ports.autostart') }}</label>
            </div>
            <div>
              <div class="label">{{ t('ports.gatewayUrl') }}</div>
              <div class="rounded-lg bg-matcha-500/8 p-3 font-mono text-xs text-matcha-700 dark:text-matcha-400">
                /gw/{{ form.slug || '&lt;slug&gt;' }}{{ endpointPath }}
              </div>
            </div>
            <div>
              <div class="label">{{ t('ports.directUrl') }}</div>
              <div class="rounded-lg bg-steel-100/70 p-3 font-mono text-xs text-steel-500 dark:bg-steel-800/50">
                http://&lt;host&gt;:{{ form.port }}{{ endpointPath }}
              </div>
            </div>
            <p class="flex items-start gap-1.5 rounded-lg border border-kin-400/40 bg-kin-400/5 p-2.5 text-[11px] leading-relaxed text-steel-600 dark:text-steel-300">
              <WaIcon name="alert" :size="14" class="mt-0.5 shrink-0 text-kin-600 dark:text-kin-400" />
              <span>{{ t('ports.directNote') }}</span>
            </p>
          </div>
        </div>

        <div class="flex items-center justify-between gap-2 border-t border-steel-200/70 px-5 py-3 dark:border-steel-800">
          <p v-if="editingId" class="font-mono text-[10px] uppercase tracking-wider text-steel-400">{{ t('ports.restartHint') }}</p>
          <p v-if="err" class="text-sm text-aka-600">{{ err }}</p>
          <div class="ml-auto flex gap-2">
            <button class="btn-ghost" @click="closeForm">{{ t('common.cancel') }}</button>
            <button class="btn-primary" @click="save">{{ editingId ? t('common.save') : t('common.create') }}</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Logs modal -->
    <div v-if="logsFor" class="fixed inset-y-0 right-0 left-[var(--sidebar-w,0px)] z-50 flex items-center justify-center overflow-y-auto bg-steel-950/50 p-4 backdrop-blur-sm" @click.self="logsFor = null">
      <div class="card my-auto max-h-[88vh] w-[94vw] max-w-[1400px] animate-fade-up overflow-auto">
        <div class="mb-3 flex items-center justify-between">
          <h2 class="heading text-sm"><WaIcon name="logs" :size="18" /> {{ t('ports.logsTitle') }} · {{ logsFor.name }}</h2>
          <button class="btn-ghost" @click="logsFor = null">{{ t('common.close') }}</button>
        </div>
        <div class="max-h-[60vh] space-y-2 overflow-auto">
          <div v-for="l in logs" :key="l.id" class="rounded-lg border border-steel-200/70 p-2 text-xs dark:border-steel-800">
            <div class="flex justify-between font-mono text-steel-400">
              <span>{{ l.ts }} · {{ l.model_used }}</span>
              <span :class="l.ok ? 'text-matcha-600' : 'text-aka-500'">{{ l.ok ? 'OK' : 'ERR' }} · {{ Math.round(l.latency_ms) }}ms</span>
            </div>
            <div class="mt-1"><span class="text-steel-400">→ </span>{{ l.request_excerpt }}</div>
            <div class="mt-1"><span class="text-steel-400">← </span>{{ l.response_excerpt || l.error }}</div>
          </div>
          <div v-if="!logs.length" class="py-6 text-center text-steel-400">{{ t('ports.noLogs') }}</div>
        </div>
      </div>
    </div>
  </div>
</template>
