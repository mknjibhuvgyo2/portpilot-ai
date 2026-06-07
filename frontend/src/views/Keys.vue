<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import WaIcon from '../components/WaIcon.vue'
import api from '../api/client'

const { t } = useI18n()
const tab = ref<'keys' | 'usage' | 'pricing'>('keys')

// --- keys ---
const keys = ref<any[]>([])
const ports = ref<any[]>([])
const showForm = ref(false)
const form = ref({ name: '', port_id: null as number | null, quota_tokens: 0 })
const newKey = ref('')
const err = ref('')

// --- usage ---
const range = ref<'today' | '7d' | '30d' | 'all'>('7d')
const summary = ref<any>(null)

// --- pricing ---
const pricing = ref<any>({ currency: 'USD', default: { in: 0, out: 0 }, models: {} })
const modelRates = ref<{ name: string; in: number; out: number }[]>([])
const saveMsg = ref('')

const CUR: Record<string, string> = { USD: '$', CNY: '¥', JPY: '¥', EUR: '€', GBP: '£' }
function sym() { return CUR[summary.value?.currency || pricing.value.currency] || '' }
function fmtCost(n: number) { return sym() + (n ?? 0).toFixed(4) }
function fmtNum(n: number) { return (n ?? 0).toLocaleString() }

async function loadKeys() {
  keys.value = (await api.get('/api/usage/keys')).data
  ports.value = (await api.get('/api/ports')).data
}
async function loadSummary() { summary.value = (await api.get('/api/usage/summary', { params: { rng: range.value } })).data }
async function loadPricing() {
  const p = (await api.get('/api/usage/pricing')).data
  pricing.value = { currency: p.currency || 'USD', default: p.default || { in: 0, out: 0 }, models: p.models || {} }
  modelRates.value = Object.entries(p.models || {}).map(([name, r]: any) => ({ name, in: r.in, out: r.out }))
}
onMounted(loadKeys)
watch(tab, (v) => { if (v === 'usage') loadSummary(); if (v === 'pricing') loadPricing() })
watch(range, () => { if (tab.value === 'usage') loadSummary() })

async function create() {
  err.value = ''
  try {
    const { data } = await api.post('/api/keys', form.value)
    newKey.value = data.key
    showForm.value = false
    form.value = { name: '', port_id: null, quota_tokens: 0 }
    await loadKeys()
  } catch (e: any) { err.value = e.response?.data?.detail || 'error' }
}
async function toggle(k: any) { await api.patch(`/api/keys/${k.id}`); await loadKeys() }
async function remove(k: any) {
  if (!confirm(`${t('keys.revoke')} ${k.name}?`)) return
  await api.delete(`/api/keys/${k.id}`); await loadKeys()
}
function copyKey() { navigator.clipboard?.writeText(newKey.value) }

function addModelRate() { modelRates.value.push({ name: '', in: 0, out: 0 }) }
function rmModelRate(i: number) { modelRates.value.splice(i, 1) }
async function savePricing() {
  const models: Record<string, any> = {}
  for (const r of modelRates.value) if (r.name) models[r.name] = { in: Number(r.in) || 0, out: Number(r.out) || 0 }
  const body = { currency: pricing.value.currency, default: { in: Number(pricing.value.default.in) || 0, out: Number(pricing.value.default.out) || 0 }, models }
  await api.put('/api/usage/pricing', body)
  saveMsg.value = t('keys.saved'); setTimeout(() => (saveMsg.value = ''), 2000)
}

function quotaPct(k: any) { return k.quota_tokens ? Math.min(100, Math.round((k.used_tokens / k.quota_tokens) * 100)) : 0 }

// --- CSV export ---
async function downloadCsv(scope: 'summary' | 'keys') {
  const res = await api.get('/api/usage/export.csv', { params: { rng: range.value, scope }, responseType: 'blob' })
  const url = URL.createObjectURL(res.data)
  const a = document.createElement('a')
  a.href = url
  a.download = scope === 'keys' ? 'usage_keys.csv' : `usage_${range.value}.csv`
  document.body.appendChild(a); a.click(); a.remove()
  URL.revokeObjectURL(url)
}

// --- trend charts (cost bars) ---
function dayShort(d: string) { return (d || '').slice(5) }
function barPct(val: number, max: number) { return max > 0 ? Math.max(3, Math.round((val / max) * 100)) : 3 }

// --- per-key daily trend (expandable row) ---
const expandedKey = ref<number | null>(null)
const keyTrend = ref<any>(null)
const trendLoading = ref(false)
async function toggleTrend(k: any) {
  if (expandedKey.value === k.id) { expandedKey.value = null; keyTrend.value = null; return }
  expandedKey.value = k.id; keyTrend.value = null; trendLoading.value = true
  try { keyTrend.value = (await api.get(`/api/usage/keys/${k.id}/daily`, { params: { rng: '30d' } })).data }
  finally { trendLoading.value = false }
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-center justify-between">
      <h1 class="heading"><WaIcon name="keys" :size="22" class="text-ai-700 dark:text-kin-400" /> {{ t('keys.title') }}</h1>
      <div class="flex gap-1 rounded-lg border border-steel-200/70 p-1 dark:border-steel-800">
        <button v-for="x in (['keys','usage','pricing'] as const)" :key="x"
          class="rounded-md px-3 py-1 text-xs font-semibold transition-colors"
          :class="tab === x ? 'bg-accent-grad text-steel-50' : 'text-steel-500'" @click="tab = x">
          {{ t('keys.tab' + x.charAt(0).toUpperCase() + x.slice(1)) }}
        </button>
      </div>
    </div>

    <!-- ============ KEYS ============ -->
    <template v-if="tab === 'keys'">
      <div class="flex justify-end gap-2">
        <button class="btn-ghost" @click="downloadCsv('keys')"><WaIcon name="download" :size="15" />{{ t('keys.exportCsv') }}</button>
        <button class="btn-primary" @click="showForm = true"><WaIcon name="plus" :size="15" />{{ t('keys.newKey') }}</button>
      </div>

      <div v-if="newKey" class="card animate-fade-up border-kin-400/60 shadow-glow">
        <div class="label text-ai-700 dark:text-kin-400">{{ t('keys.oneTime') }}</div>
        <div class="mt-1 flex items-center gap-2">
          <code class="flex-1 break-all rounded-md bg-steel-100 px-2 py-1.5 font-mono text-xs dark:bg-steel-800">{{ newKey }}</code>
          <button class="btn-ghost" @click="copyKey"><WaIcon name="copy" :size="14" />{{ t('common.copy') }}</button>
          <button class="btn-ghost" @click="newKey = ''">{{ t('common.gotIt') }}</button>
        </div>
      </div>

      <div class="card overflow-x-auto p-0">
        <table class="w-full text-sm">
          <thead class="border-b border-steel-200/70 text-left dark:border-steel-800">
            <tr class="[&>th]:label [&>th]:px-4 [&>th]:py-3">
              <th>{{ t('keys.name') }}</th><th>{{ t('keys.scope') }}</th>
              <th>{{ t('keys.usedOfQuota') }}</th><th>{{ t('keys.estCost') }}</th>
              <th>{{ t('keys.status') }}</th><th class="text-right">{{ t('keys.actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="k in keys" :key="k.id">
            <tr class="border-b border-steel-100 last:border-0 dark:border-steel-800/60">
              <td class="px-4 py-3 font-medium">{{ k.name }}</td>
              <td class="px-4 py-3">{{ k.port_name || t('keys.allPorts') }}</td>
              <td class="px-4 py-3">
                <div class="stat text-xs">{{ fmtNum(k.used_tokens) }} / {{ k.quota_tokens ? fmtNum(k.quota_tokens) : '∞' }}</div>
                <div v-if="k.quota_tokens" class="mt-1 h-1.5 w-28 overflow-hidden rounded-full bg-steel-200 dark:bg-steel-800">
                  <div class="h-full rounded-full" :class="quotaPct(k) >= 100 ? 'bg-aka-500' : 'bg-accent-grad'" :style="{ width: quotaPct(k) + '%' }"></div>
                </div>
              </td>
              <td class="stat px-4 py-3">{{ fmtCost(k.cost) }}</td>
              <td class="px-4 py-3">
                <span class="chip" :class="k.enabled ? 'bg-matcha-500/12 text-matcha-600' : 'bg-steel-500/10 text-steel-400'">
                  {{ k.enabled ? t('keys.active') : t('keys.disabled') }}
                </span>
              </td>
              <td class="px-4 py-3">
                <div class="flex justify-end gap-2">
                  <button class="btn-ghost" :class="expandedKey === k.id ? 'text-ai-700 dark:text-kin-400' : ''" @click="toggleTrend(k)"><WaIcon name="activity" :size="14" />{{ t('keys.trend') }}</button>
                  <button class="btn-ghost" @click="toggle(k)"><WaIcon name="power" :size="14" />{{ k.enabled ? t('common.disable') : t('common.enable') }}</button>
                  <button class="btn-danger" @click="remove(k)"><WaIcon name="trash" :size="14" />{{ t('keys.revoke') }}</button>
                </div>
              </td>
            </tr>
            <tr v-if="expandedKey === k.id" class="border-b border-steel-100 dark:border-steel-800/60">
              <td colspan="6" class="bg-steel-50/60 px-4 py-4 dark:bg-steel-900/40">
                <div class="label !mb-2">{{ t('keys.keyTrend30') }}</div>
                <div v-if="trendLoading" class="text-xs text-steel-400">{{ t('common.loading') }}</div>
                <div v-else-if="keyTrend && keyTrend.daily.length" class="flex items-end gap-1 overflow-x-auto pb-5 pt-1" style="height:96px">
                  <div v-for="(d, i) in keyTrend.daily" :key="i" class="group relative flex w-5 shrink-0 flex-col items-center justify-end" style="height:72px">
                    <div class="w-3 rounded-t bg-accent-grad transition-all group-hover:opacity-80" :style="{ height: barPct(d.cost, Math.max(...keyTrend.daily.map((x:any)=>x.cost), 0.000001)) + '%' }"></div>
                    <div class="absolute -bottom-5 whitespace-nowrap text-[9px] text-steel-400">{{ dayShort(d.day) }}</div>
                    <div class="pointer-events-none absolute bottom-full mb-1 hidden whitespace-nowrap rounded bg-steel-900 px-1.5 py-0.5 text-[10px] text-steel-50 group-hover:block dark:bg-steel-700">{{ dayShort(d.day) }} · {{ fmtCost(d.cost) }} · {{ fmtNum(d.total_tokens) }}t</div>
                  </div>
                </div>
                <div v-else class="text-xs text-steel-400">{{ t('keys.noUsage') }}</div>
              </td>
            </tr>
            </template>
            <tr v-if="!keys.length"><td colspan="6" class="px-4 py-10 text-center text-steel-400">{{ t('keys.noKeys') }}</td></tr>
          </tbody>
        </table>
      </div>
      <p class="font-mono text-[11px] text-steel-400">{{ t('keys.usageHint') }}</p>
    </template>

    <!-- ============ USAGE ============ -->
    <template v-else-if="tab === 'usage'">
      <div class="flex items-center gap-2">
        <span class="label !mb-0">{{ t('keys.range') }}</span>
        <div class="flex gap-1 rounded-lg border border-steel-200/70 p-1 dark:border-steel-800">
          <button v-for="r in (['today','7d','30d','all'] as const)" :key="r"
            class="rounded-md px-2.5 py-1 text-xs font-medium transition-colors"
            :class="range === r ? 'bg-accent-grad text-steel-50' : 'text-steel-500'" @click="range = r">
            {{ t('keys.r' + (r === '7d' ? '7' : r === '30d' ? '30' : r.charAt(0).toUpperCase() + r.slice(1))) }}
          </button>
        </div>
        <button class="btn-ghost ml-auto" @click="downloadCsv('summary')"><WaIcon name="download" :size="15" />{{ t('keys.exportCsv') }}</button>
      </div>

      <div v-if="summary && summary.daily && summary.daily.length" class="card">
        <div class="label">{{ t('keys.dailyTrend') }}</div>
        <div class="mt-3 flex items-end gap-1.5 overflow-x-auto pb-5 pt-1" style="height:140px">
          <div v-for="(d, i) in summary.daily" :key="i" class="group relative flex shrink-0 flex-col items-center justify-end" style="height:112px;width:26px">
            <div class="w-4 rounded-t bg-accent-grad transition-all group-hover:opacity-80" :style="{ height: barPct(d.cost, Math.max(...summary.daily.map((x:any)=>x.cost), 0.000001)) + '%' }"></div>
            <div class="absolute -bottom-5 whitespace-nowrap text-[9px] text-steel-400">{{ dayShort(d.day) }}</div>
            <div class="pointer-events-none absolute bottom-full mb-1 hidden whitespace-nowrap rounded bg-steel-900 px-1.5 py-0.5 text-[10px] text-steel-50 group-hover:block dark:bg-steel-700">{{ dayShort(d.day) }} · {{ fmtCost(d.cost) }} · {{ fmtNum(d.total_tokens) }}t · {{ fmtNum(d.requests) }}req</div>
          </div>
        </div>
      </div>

      <div v-if="summary" class="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div class="card"><div class="label">{{ t('keys.totalRequests') }}</div><div class="stat text-2xl font-bold">{{ fmtNum(summary.totals.requests) }}</div></div>
        <div class="card"><div class="label">{{ t('keys.totalTokens') }}</div><div class="stat text-2xl font-bold">{{ fmtNum(summary.totals.total_tokens) }}</div></div>
        <div class="card"><div class="label">{{ t('keys.totalCost') }}</div><div class="stat text-2xl font-bold text-ai-700 dark:text-kin-400">{{ fmtCost(summary.totals.cost) }}</div></div>
        <div class="card"><div class="label">{{ t('keys.errors') }}</div><div class="stat text-2xl font-bold" :class="summary.totals.errors ? 'text-aka-500' : ''">{{ fmtNum(summary.totals.errors) }}</div></div>
      </div>

      <div class="grid gap-4 lg:grid-cols-2">
        <div class="card p-0">
          <div class="label px-4 pt-3">{{ t('keys.byPort') }}</div>
          <table class="w-full text-sm">
            <thead class="text-left"><tr class="[&>th]:label [&>th]:px-4 [&>th]:py-2"><th>{{ t('ports.name') }}</th><th class="text-right">{{ t('keys.requests') }}</th><th class="text-right">{{ t('keys.totalTokens') }}</th><th class="text-right">{{ t('keys.cost') }}</th></tr></thead>
            <tbody>
              <tr v-for="r in summary.by_port" :key="r.port_id" class="border-t border-steel-100 dark:border-steel-800/60">
                <td class="px-4 py-2 font-medium">{{ r.name }}</td>
                <td class="stat px-4 py-2 text-right">{{ fmtNum(r.requests) }}</td>
                <td class="stat px-4 py-2 text-right">{{ fmtNum(r.total_tokens) }}</td>
                <td class="stat px-4 py-2 text-right">{{ fmtCost(r.cost) }}</td>
              </tr>
              <tr v-if="!summary.by_port.length"><td colspan="4" class="px-4 py-6 text-center text-steel-400">{{ t('keys.noUsage') }}</td></tr>
            </tbody>
          </table>
        </div>
        <div class="card p-0">
          <div class="label px-4 pt-3">{{ t('keys.byModel') }}</div>
          <table class="w-full text-sm">
            <thead class="text-left"><tr class="[&>th]:label [&>th]:px-4 [&>th]:py-2"><th>{{ t('keys.model') }}</th><th class="text-right">{{ t('keys.requests') }}</th><th class="text-right">{{ t('keys.totalTokens') }}</th><th class="text-right">{{ t('keys.cost') }}</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in summary.by_model" :key="i" class="border-t border-steel-100 dark:border-steel-800/60">
                <td class="px-4 py-2 font-mono text-xs">{{ r.model }}</td>
                <td class="stat px-4 py-2 text-right">{{ fmtNum(r.requests) }}</td>
                <td class="stat px-4 py-2 text-right">{{ fmtNum(r.total_tokens) }}</td>
                <td class="stat px-4 py-2 text-right">{{ fmtCost(r.cost) }}</td>
              </tr>
              <tr v-if="!summary.by_model.length"><td colspan="4" class="px-4 py-6 text-center text-steel-400">{{ t('keys.noUsage') }}</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <!-- ============ PRICING ============ -->
    <template v-else>
      <div class="card max-w-2xl space-y-4">
        <div class="grid grid-cols-3 gap-3">
          <div><label class="label">{{ t('keys.currency') }}</label><input v-model="pricing.currency" class="input" /></div>
          <div><label class="label">{{ t('keys.priceDefault') }} · {{ t('keys.priceIn') }}</label><input v-model.number="pricing.default.in" type="number" step="0.01" class="input" /></div>
          <div><label class="label">{{ t('keys.priceDefault') }} · {{ t('keys.priceOut') }}</label><input v-model.number="pricing.default.out" type="number" step="0.01" class="input" /></div>
        </div>
        <div>
          <label class="label">{{ t('keys.priceModels') }} · {{ t('keys.perMillion') }}</label>
          <div v-for="(r, i) in modelRates" :key="i" class="mb-2 flex gap-2">
            <input v-model="r.name" class="input flex-1" :placeholder="t('keys.modelName')" />
            <input v-model.number="r.in" type="number" step="0.01" class="input w-28" :placeholder="t('keys.priceIn')" />
            <input v-model.number="r.out" type="number" step="0.01" class="input w-28" :placeholder="t('keys.priceOut')" />
            <button class="btn-ghost !px-2" @click="rmModelRate(i)">−</button>
          </div>
          <button class="btn-ghost" @click="addModelRate"><WaIcon name="plus" :size="14" />{{ t('keys.addModelPrice') }}</button>
        </div>
        <p class="text-xs text-steel-400">{{ t('keys.pricingHint') }}</p>
        <div class="flex items-center justify-end gap-3">
          <span v-if="saveMsg" class="text-xs text-matcha-600">{{ saveMsg }}</span>
          <button class="btn-primary" @click="savePricing"><WaIcon name="save" :size="14" />{{ t('keys.savePricing') }}</button>
        </div>
      </div>
    </template>

    <!-- create key modal -->
    <div v-if="showForm" class="fixed inset-y-0 right-0 left-[var(--sidebar-w,0px)] z-50 flex items-center justify-center overflow-y-auto bg-steel-950/50 p-4 backdrop-blur-sm" @click.self="showForm = false">
      <div class="card my-auto w-[90vw] max-w-[820px] animate-fade-up space-y-3 shadow-glow">
        <h2 class="heading text-sm"><WaIcon name="keys" :size="18" /> {{ t('keys.createTitle') }}</h2>
        <div><label class="label">{{ t('keys.name') }}</label><input v-model="form.name" class="input" /></div>
        <div>
          <label class="label">{{ t('keys.scopePort') }}</label>
          <select v-model="form.port_id" class="input">
            <option :value="null">{{ t('keys.allPorts') }}</option>
            <option v-for="p in ports" :key="p.id" :value="p.id">{{ p.name }} :{{ p.port }}</option>
          </select>
        </div>
        <div><label class="label">{{ t('keys.quotaTokens') }}</label><input v-model.number="form.quota_tokens" type="number" class="input" /></div>
        <p v-if="err" class="text-sm text-aka-600">{{ err }}</p>
        <div class="flex justify-end gap-2">
          <button class="btn-ghost" @click="showForm = false">{{ t('common.cancel') }}</button>
          <button class="btn-primary" @click="create">{{ t('common.create') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
