<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import WaIcon from '../components/WaIcon.vue'
import api from '../api/client'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const route = useRoute()
const auth = useAuthStore()
const providers = ref<any[]>([])
const aliases = ref<any[]>([])
const tab = ref<'providers' | 'aliases'>('providers')
const kinds = ['openai_compat', 'anthropic', 'gemini', 'ollama', 'lmstudio', 'llamacpp']
const provForm = ref<any>(null)
const aliasForm = ref<any>(null)
const msg = ref('')

// ---- local-engine advanced params ----
const advFields: { key: string; type: 'int' | 'float' | 'str'; ph: string }[] = [
  { key: 'num_ctx', type: 'int', ph: '4096' },
  { key: 'num_gpu', type: 'int', ph: '99' },
  { key: 'num_thread', type: 'int', ph: '8' },
  { key: 'num_predict', type: 'int', ph: '512' },
  { key: 'repeat_penalty', type: 'float', ph: '1.1' },
  { key: 'top_k', type: 'int', ph: '40' },
  { key: 'min_p', type: 'float', ph: '0.05' },
  { key: 'seed', type: 'int', ph: '-1' },
  { key: 'keep_alive', type: 'str', ph: '5m' },
]
const advanced = ref<Record<string, any>>({})
const advCustom = ref('')
const isLocalKind = computed(() => !!provForm.value && ['ollama', 'lmstudio', 'llamacpp'].includes(provForm.value.kind))

// ---- vendor presets + custom headers ----
const presets = ref<any[]>([])
const presetSel = ref('')
const customHeaders = ref('')
const presetGroups = ['intl', 'cn', 'local', 'custom']
function applyPreset() {
  const p = presets.value.find((x) => x.id === presetSel.value)
  if (!p || !provForm.value) return
  provForm.value.kind = p.kind
  if (p.base_url) provForm.value.base_url = p.base_url
}

async function load() {
  providers.value = (await api.get('/api/models/providers')).data
  aliases.value = (await api.get('/api/models/aliases')).data
  if (!presets.value.length) presets.value = (await api.get('/api/models/provider-presets')).data
}
onMounted(async () => {
  await load()
  // deep-link from the port editor's "+ new route / provider" shortcut
  if (route.query.new === 'alias') { tab.value = 'aliases'; newAlias() }
  else if (route.query.new === 'provider') { tab.value = 'providers'; newProvider() }
})

function newProvider() {
  provForm.value = { name: '', kind: 'openai_compat', base_url: '', api_key: '', gpu_index: '', weight: 1, enabled: true, extra: {} }
  advanced.value = {}; advCustom.value = ''; customHeaders.value = ''; presetSel.value = ''
}
function editProvider(p: any) {
  provForm.value = { ...p, api_key: '' }
  const a = (p.extra && p.extra.advanced) || {}
  const known: Record<string, any> = {}; const rest: Record<string, any> = {}
  for (const [k, v] of Object.entries(a)) {
    if (advFields.some((f) => f.key === k)) known[k] = v; else rest[k] = v
  }
  advanced.value = known
  advCustom.value = Object.keys(rest).length ? JSON.stringify(rest) : ''
  customHeaders.value = (p.extra && p.extra.headers) ? JSON.stringify(p.extra.headers) : ''
  presetSel.value = ''
}
async function saveProvider() {
  msg.value = ''
  const adv: Record<string, any> = {}
  for (const f of advFields) {
    const v = advanced.value[f.key]
    if (v === '' || v == null) continue
    adv[f.key] = f.type === 'str' ? String(v) : Number(v)
  }
  if (advCustom.value.trim()) {
    try { Object.assign(adv, JSON.parse(advCustom.value)) }
    catch { msg.value = t('models.advJsonErr'); return }
  }
  const extra: any = { ...(provForm.value.extra || {}), advanced: adv }
  if (customHeaders.value.trim()) {
    try { extra.headers = JSON.parse(customHeaders.value) }
    catch { msg.value = t('models.headersJsonErr'); return }
  } else { delete extra.headers }
  provForm.value.extra = extra
  try {
    if (provForm.value.id) await api.patch(`/api/models/providers/${provForm.value.id}`, provForm.value)
    else await api.post('/api/models/providers', provForm.value)
    provForm.value = null; await load()
  } catch (e: any) { msg.value = e.response?.data?.detail || 'error' }
}
async function delProvider(p: any) { if (!confirm(`${t('common.delete')} ${p.name}?`)) return; await api.delete(`/api/models/providers/${p.id}`); await load() }
// ---- provider health panel ----
const healthMap = ref<Record<number, { checking?: boolean; healthy?: boolean; models?: string[] }>>({})
const health = (id: number) => healthMap.value[id] || {}
async function testProvider(p: any) {
  healthMap.value = { ...healthMap.value, [p.id]: { checking: true } }
  try {
    const r = await api.post(`/api/models/providers/${p.id}/test`)
    healthMap.value = { ...healthMap.value, [p.id]: { healthy: !!r.data.healthy, models: r.data.models || [], checking: false } }
  } catch {
    healthMap.value = { ...healthMap.value, [p.id]: { healthy: false, models: [], checking: false } }
  }
}
async function testAll() { await Promise.all(providers.value.map((p) => testProvider(p))) }

// ---- one-click local connect ----
async function connectLocal(kind: string) {
  try {
    const { data } = await api.post('/api/models/connect-local', { kind })
    await load()
    const key = data.created ? 'models.connectedNew' : 'models.connectedExisting'
    alert(t(key, { n: data.name }) + (data.healthy ? '' : ' — ' + t('models.unreachable')))
  } catch (e: any) { alert(e.response?.data?.detail || 'error') }
}

// ---- ollama model management ----
const modelsFor = ref<any>(null)
const localModels = ref<any[]>([])
const pullName = ref('')
const modelBusy = ref(false)
const modelMsg = ref('')
const pullPct = ref<number | null>(null)
const pullStatus = ref('')
async function openModels(p: any) { modelsFor.value = p; localModels.value = []; modelMsg.value = ''; await loadLocalModels() }
async function loadLocalModels() {
  if (!modelsFor.value) return
  try { localModels.value = (await api.get(`/api/models/providers/${modelsFor.value.id}/local-models`)).data }
  catch (e: any) { modelMsg.value = e.response?.data?.detail || 'error' }
}
async function pullModel() {
  const n = pullName.value.trim()
  if (!n || !modelsFor.value) return
  modelBusy.value = true; modelMsg.value = t('models.pulling', { n })
  pullPct.value = null; pullStatus.value = ''
  let errored = ''
  try {
    const token = localStorage.getItem('token')
    const resp = await fetch(`/api/models/providers/${modelsFor.value.id}/pull-stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ name: n }),
    })
    if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status)
    const reader = resp.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      const parts = buf.split('\n\n'); buf = parts.pop() || ''
      for (const part of parts) {
        const line = part.split('\n').find((l) => l.startsWith('data:'))
        if (!line) continue
        let evt: any
        try { evt = JSON.parse(line.slice(5).trim()) } catch { continue }
        if (evt.error) { errored = evt.error; continue }
        if (evt.status) pullStatus.value = evt.status
        if (typeof evt.percent === 'number') pullPct.value = evt.percent
      }
    }
    if (errored) { modelMsg.value = '❌ ' + errored }
    else { pullName.value = ''; modelMsg.value = t('models.pulled'); await loadLocalModels() }
  } catch (e: any) { modelMsg.value = '❌ ' + (e.message || 'pull failed') }
  finally { modelBusy.value = false; pullPct.value = null; pullStatus.value = '' }
}
async function delModel(name: string) {
  if (!modelsFor.value || !confirm(`${t('common.delete')} ${name}?`)) return
  try { await api.delete(`/api/models/providers/${modelsFor.value.id}/local-models`, { params: { name } }); await loadLocalModels() }
  catch (e: any) { alert(e.response?.data?.detail || 'error') }
}

function newAlias() { aliasForm.value = { alias: '', targets: [{ provider_id: providers.value[0]?.id, model: '', lb_group: '' }], fallback_text: '', params: { lb_strategy: 'weighted' }, enabled: true } }
function editAlias(a: any) { const c = JSON.parse(JSON.stringify(a)); c.targets = (c.targets || []).map((t: any) => ({ lb_group: '', ...t })); c.params = c.params || {}; if (!c.params.lb_strategy) c.params.lb_strategy = 'weighted'; aliasForm.value = c }
function addTarget() { aliasForm.value.targets.push({ provider_id: providers.value[0]?.id, model: '', lb_group: '' }) }
function rmTarget(i: number) { aliasForm.value.targets.splice(i, 1) }
async function saveAlias() {
  msg.value = ''
  try {
    if (aliasForm.value.id) await api.patch(`/api/models/aliases/${aliasForm.value.id}`, aliasForm.value)
    else await api.post('/api/models/aliases', aliasForm.value)
    aliasForm.value = null; await load()
  } catch (e: any) { msg.value = e.response?.data?.detail || 'error' }
}
async function delAlias(a: any) { if (!confirm(`${t('common.delete')} ${a.alias}?`)) return; await api.delete(`/api/models/aliases/${a.id}`); await load() }

const provName = computed(() => (id: number) => providers.value.find((p) => p.id === id)?.name || '?')
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-center justify-between">
      <h1 class="heading"><WaIcon name="models" :size="22" class="text-ai-700 dark:text-kin-400" /> {{ t('models.title') }}</h1>
      <div class="flex gap-1 rounded-lg border border-steel-200/70 p-1 dark:border-steel-800">
        <button class="rounded-md px-3 py-1 text-xs font-semibold transition-colors" :class="tab === 'providers' ? 'bg-accent-grad text-white' : 'text-steel-500'" @click="tab = 'providers'">{{ t('models.providersTab') }}</button>
        <button class="rounded-md px-3 py-1 text-xs font-semibold transition-colors" :class="tab === 'aliases' ? 'bg-accent-grad text-white' : 'text-steel-500'" @click="tab = 'aliases'">{{ t('models.aliasesTab') }}</button>
      </div>
    </div>

    <!-- Providers -->
    <div v-if="tab === 'providers'" class="space-y-3">
      <div class="flex flex-wrap items-center justify-end gap-2">
        <span class="mr-auto text-[11px] uppercase tracking-wider text-steel-400">{{ t('models.localConnect') }}</span>
        <button v-if="auth.isAdmin" class="btn-ghost" @click="connectLocal('ollama')"><WaIcon name="plug" :size="14" />Ollama</button>
        <button v-if="auth.isAdmin" class="btn-ghost" @click="connectLocal('lmstudio')"><WaIcon name="plug" :size="14" />LM Studio</button>
        <button v-if="providers.length" class="btn-ghost" @click="testAll"><WaIcon name="plug" :size="15" />{{ t('models.testAll') }}</button>
        <button v-if="auth.isAdmin" class="btn-primary" @click="newProvider"><WaIcon name="plus" :size="15" />{{ t('models.newProvider') }}</button>
      </div>
      <div class="grid gap-3 md:grid-cols-2">
        <div v-for="p in providers" :key="p.id" class="card card-hover">
          <div class="flex items-start justify-between">
            <div>
              <div class="flex items-center gap-2 font-semibold">{{ p.name }}<span class="chip bg-accent-grad-soft text-accent-700 dark:text-accent-300">{{ p.kind }}</span></div>
              <div class="mt-1 font-mono text-xs text-steel-400">{{ p.base_url }}</div>
              <div v-if="p.gpu_index" class="font-mono text-xs text-steel-400">GPU: {{ p.gpu_index }} · w{{ p.weight }}</div>
              <div class="mt-1.5">
                <span v-if="health(p.id).checking" class="chip bg-steel-500/10 text-steel-400"><WaIcon name="spinner" :size="11" class="animate-spin" />{{ t('models.checking') }}</span>
                <span v-else-if="health(p.id).healthy === true" class="chip bg-matcha-500/12 text-matcha-700 dark:text-matcha-400" :title="(health(p.id).models || []).join(', ')"><span class="dot bg-matcha-500"></span>{{ t('models.healthy') }} · {{ (health(p.id).models || []).length }} {{ t('models.nModels') }}</span>
                <span v-else-if="health(p.id).healthy === false" class="chip bg-aka-500/12 text-aka-600"><span class="dot bg-aka-500"></span>{{ t('models.unreachable') }}</span>
                <span v-else class="chip bg-steel-500/10 text-steel-400">{{ t('models.notTested') }}</span>
              </div>
            </div>
            <div class="flex flex-wrap justify-end gap-1">
              <button class="btn-ghost" @click="testProvider(p)"><WaIcon name="plug" :size="14" />{{ t('common.test') }}</button>
              <button v-if="p.kind === 'ollama' && auth.isAdmin" class="btn-ghost" @click="openModels(p)"><WaIcon name="models" :size="14" />{{ t('models.manageModels') }}</button>
              <template v-if="auth.isAdmin">
                <button class="btn-ghost !px-2" @click="editProvider(p)"><WaIcon name="edit" :size="14" /></button>
                <button class="btn-danger !px-2" @click="delProvider(p)"><WaIcon name="trash" :size="14" /></button>
              </template>
            </div>
          </div>
        </div>
        <div v-if="!providers.length" class="card text-sm text-steel-400">{{ t('models.noProviders') }}</div>
      </div>
    </div>

    <!-- Aliases -->
    <div v-else class="space-y-3">
      <div class="flex justify-end"><button v-if="auth.isAdmin" class="btn-primary" @click="newAlias"><WaIcon name="plus" :size="15" />{{ t('models.newAlias') }}</button></div>
      <div class="grid gap-3 md:grid-cols-2">
        <div v-for="a in aliases" :key="a.id" class="card card-hover">
          <div class="flex items-start justify-between">
            <div>
              <div class="font-semibold">{{ a.alias }}</div>
              <div class="mt-1 space-y-0.5 font-mono text-xs text-steel-500">
                <div v-for="(tg, i) in a.targets" :key="i">
                  <span class="text-steel-400">{{ i === 0 ? t('models.primary') : t('models.backup') + i }}:</span>
                  {{ provName(tg.provider_id) }} / {{ tg.model }}
                  <span v-if="tg.lb_group" class="ml-1 text-kin-600 dark:text-kin-400">· LB:{{ tg.lb_group }}</span>
                </div>
                <div v-if="a.fallback_text" class="text-steel-400">{{ t('models.fallback') }}: {{ a.fallback_text }}</div>
              </div>
            </div>
            <div v-if="auth.isAdmin" class="flex gap-1">
              <button class="btn-ghost !px-2" @click="editAlias(a)"><WaIcon name="edit" :size="14" /></button>
              <button class="btn-danger !px-2" @click="delAlias(a)"><WaIcon name="trash" :size="14" /></button>
            </div>
          </div>
        </div>
        <div v-if="!aliases.length" class="card text-sm text-steel-400">{{ t('models.noAliases') }}</div>
      </div>
    </div>

    <!-- Provider modal -->
    <div v-if="provForm" class="fixed inset-y-0 right-0 left-[var(--sidebar-w,0px)] z-50 flex items-center justify-center overflow-y-auto bg-steel-950/50 p-4 backdrop-blur-sm" @click.self="provForm = null">
      <div class="card flex h-[88vh] w-[94vw] max-w-[1700px] flex-col animate-fade-up !p-0 shadow-glow">
        <h2 class="heading border-b border-steel-200/70 px-5 py-4 text-sm dark:border-steel-800">{{ provForm.id ? t('common.edit') : t('common.add') }} · {{ t('models.providersTab') }}</h2>
        <div class="flex-1 space-y-3 overflow-auto p-5">
        <div>
          <label class="label">{{ t('models.preset') }}</label>
          <select v-model="presetSel" class="input" @change="applyPreset">
            <option value="">{{ t('models.presetCustom') }}</option>
            <optgroup v-for="g in presetGroups" :key="g" :label="t('models.presetGroup.' + g)">
              <option v-for="p in presets.filter((x) => x.group === g)" :key="p.id" :value="p.id">{{ t('models.vendor.' + p.id) }}</option>
            </optgroup>
          </select>
        </div>
        <div><label class="label">{{ t('models.providerName') }}</label><input v-model="provForm.name" class="input" /></div>
        <div><label class="label">{{ t('models.kind') }}</label><select v-model="provForm.kind" class="input"><option v-for="k in kinds" :key="k">{{ k }}</option></select></div>
        <div><label class="label">{{ t('models.baseUrl') }}</label><input v-model="provForm.base_url" class="input" placeholder="http://127.0.0.1:11434" /></div>
        <div><label class="label">{{ t('models.apiKeyKeep') }}</label><input v-model="provForm.api_key" class="input" placeholder="sk-..." /></div>
        <div><label class="label">{{ t('models.customHeaders') }}</label><input v-model="customHeaders" class="input font-mono text-xs" placeholder='{"X-Custom":"value"}' /></div>
        <div class="grid grid-cols-2 gap-3">
          <div><label class="label">{{ t('models.gpuIndex') }}</label><input v-model="provForm.gpu_index" class="input" placeholder="0" /></div>
          <div><label class="label">{{ t('models.weight') }}</label><input v-model.number="provForm.weight" type="number" class="input" /></div>
        </div>

        <!-- Local-engine advanced options -->
        <div v-if="isLocalKind" class="rounded-lg border border-kin-400/40 bg-kin-400/5 p-3">
          <div class="mb-2 flex items-center gap-2">
            <WaIcon name="sliders" :size="15" class="text-kin-600 dark:text-kin-400" />
            <span class="label !mb-0">{{ t('models.advanced') }}</span>
          </div>
          <div class="grid grid-cols-3 gap-2">
            <div v-for="f in advFields" :key="f.key">
              <label class="label !text-[9px] !tracking-wide">{{ f.key }}</label>
              <input v-model="advanced[f.key]" :type="f.type === 'str' ? 'text' : 'number'"
                :step="f.type === 'float' ? '0.01' : '1'" :placeholder="f.ph" class="input !py-1.5 text-xs" />
            </div>
          </div>
          <div class="mt-2">
            <label class="label !text-[9px]">{{ t('models.advCustom') }}</label>
            <input v-model="advCustom" class="input !py-1.5 font-mono text-xs" placeholder='{"mirostat":2,"stop":["</s>"]}' />
          </div>
          <p class="mt-2 text-[11px] text-steel-400">{{ t('models.advHint') }}</p>
        </div>

        <p v-if="msg" class="text-sm text-aka-600">{{ msg }}</p>
        </div>
        <div class="flex justify-end gap-2 border-t border-steel-200/70 px-5 py-3 dark:border-steel-800"><button class="btn-ghost" @click="provForm = null">{{ t('common.cancel') }}</button><button class="btn-primary" @click="saveProvider">{{ t('common.save') }}</button></div>
      </div>
    </div>

    <!-- Alias modal -->
    <div v-if="aliasForm" class="fixed inset-y-0 right-0 left-[var(--sidebar-w,0px)] z-50 flex items-center justify-center overflow-y-auto bg-steel-950/50 p-4 backdrop-blur-sm" @click.self="aliasForm = null">
      <div class="card flex h-[88vh] w-[94vw] max-w-[1700px] flex-col animate-fade-up !p-0 shadow-glow">
        <h2 class="heading border-b border-steel-200/70 px-5 py-4 text-sm dark:border-steel-800">{{ aliasForm.id ? t('common.edit') : t('common.add') }} · {{ t('models.aliasesTab') }}</h2>
        <div class="flex-1 space-y-3 overflow-auto p-5">
        <div><label class="label">{{ t('models.aliasName') }}</label><input v-model="aliasForm.alias" class="input" /></div>
        <div>
          <label class="label">{{ t('models.targetChain') }}</label>
          <div v-for="(tg, i) in aliasForm.targets" :key="i" class="mb-2 flex gap-2">
            <select v-model.number="tg.provider_id" class="input flex-1"><option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option></select>
            <input v-model="tg.model" class="input flex-1" :placeholder="t('models.modelName')" />
            <input v-model="tg.lb_group" class="input w-24" :placeholder="t('models.lbGroup')" :title="t('models.lbHint')" />
            <button class="btn-ghost !px-2" :disabled="aliasForm.targets.length <= 1" @click="rmTarget(i)">−</button>
          </div>
          <button class="btn-ghost" @click="addTarget"><WaIcon name="plus" :size="14" />{{ t('models.addFallback') }}</button>
          <p class="mt-2 text-xs text-steel-400">{{ t('models.lbHint') }}</p>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="label">{{ t('models.lbStrategy') }}</label>
            <select v-model="aliasForm.params.lb_strategy" class="input">
              <option value="weighted">{{ t('models.stratWeighted') }}</option>
              <option value="round_robin">{{ t('models.stratRoundRobin') }}</option>
              <option value="least_conn">{{ t('models.stratLeastConn') }}</option>
              <option value="least_vram">{{ t('models.stratLeastVram') }}</option>
            </select>
          </div>
          <div>
            <label class="label">{{ t('models.pinGpu') }}</label>
            <input v-model="aliasForm.params.pin_gpu" class="input" :placeholder="t('models.pinGpuPh')" />
          </div>
        </div>
        <p class="text-xs text-steel-400">{{ t('models.lbStrategyHint') }} {{ t('models.pinGpuHint') }}</p>
        <div><label class="label">{{ t('models.fallbackText') }}</label><input v-model="aliasForm.fallback_text" class="input" /></div>
        <p v-if="msg" class="text-sm text-aka-600">{{ msg }}</p>
        </div>
        <div class="flex justify-end gap-2 border-t border-steel-200/70 px-5 py-3 dark:border-steel-800"><button class="btn-ghost" @click="aliasForm = null">{{ t('common.cancel') }}</button><button class="btn-primary" @click="saveAlias">{{ t('common.save') }}</button></div>
      </div>
    </div>

    <!-- Ollama model management modal -->
    <div v-if="modelsFor" class="fixed inset-y-0 right-0 left-[var(--sidebar-w,0px)] z-50 flex items-center justify-center overflow-y-auto bg-steel-950/50 p-4 backdrop-blur-sm" @click.self="modelsFor = null">
      <div class="card my-auto w-[92vw] max-w-[1100px] animate-fade-up space-y-3 shadow-glow">
        <h2 class="heading text-sm"><WaIcon name="models" :size="18" /> {{ t('models.manageModels') }} · {{ modelsFor.name }}</h2>
        <p class="text-xs text-steel-400">{{ t('models.pullHint') }}</p>
        <div class="flex gap-2">
          <input v-model="pullName" class="input flex-1 font-mono text-xs" :placeholder="t('models.pullPlaceholder')" @keydown.enter="pullModel" />
          <button class="btn-primary" :disabled="modelBusy || !pullName.trim()" @click="pullModel">
            <WaIcon :name="modelBusy ? 'spinner' : 'download'" :size="14" :class="modelBusy && 'animate-spin'" />{{ t('models.pull') }}
          </button>
        </div>
        <div v-if="modelBusy && (pullPct !== null || pullStatus)" class="space-y-1">
          <div class="flex justify-between text-[10px] text-steel-400">
            <span class="font-mono">{{ pullStatus }}</span>
            <span v-if="pullPct !== null" class="stat">{{ pullPct }}%</span>
          </div>
          <div class="h-1.5 w-full overflow-hidden rounded-full bg-steel-200 dark:bg-steel-800">
            <div class="h-full rounded-full bg-accent-grad transition-all" :style="{ width: (pullPct ?? 0) + '%' }"></div>
          </div>
        </div>
        <p v-if="modelMsg" class="text-xs" :class="modelMsg.startsWith('❌') ? 'text-aka-600' : 'text-steel-500'">{{ modelMsg }}</p>
        <div class="max-h-[55vh] space-y-1.5 overflow-auto">
          <div v-for="m in localModels" :key="m.name" class="flex items-center justify-between rounded-lg border border-steel-200/70 px-3 py-2 text-sm dark:border-steel-800">
            <div><span class="font-mono">{{ m.name }}</span><span class="ml-2 text-xs text-steel-400">{{ m.size_mb }} MB</span></div>
            <button class="btn-danger !px-2" @click="delModel(m.name)"><WaIcon name="trash" :size="13" /></button>
          </div>
          <div v-if="!localModels.length" class="py-6 text-center text-steel-400">{{ t('models.noLocalModels') }}</div>
        </div>
        <div class="flex justify-end"><button class="btn-ghost" @click="modelsFor = null">{{ t('common.close') }}</button></div>
      </div>
    </div>
  </div>
</template>
