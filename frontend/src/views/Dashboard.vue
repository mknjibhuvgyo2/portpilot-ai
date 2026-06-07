<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import WaIcon from '../components/WaIcon.vue'
import api from '../api/client'

const { t } = useI18n()
const router = useRouter()
const data = ref<any>({ summary: {}, ports: [], gpu: { available: false, gpus: [] } })
let timer: number | undefined

async function load() {
  try { data.value = (await api.get('/api/monitor/overview')).data } catch { /* ignore */ }
}
onMounted(() => { load(); timer = window.setInterval(load, 3000) })
onUnmounted(() => clearInterval(timer))

function dotColor(s: string) {
  return s === 'running' ? 'bg-matcha-500 shadow-[0_0_8px] shadow-matcha-500/60 animate-pulseglow'
    : s === 'error' ? 'bg-aka-500' : 'bg-steel-400'
}
function openPort(p: any) { router.push({ path: '/ports', query: { edit: p.id } }) }
function tempCls(t: number | null) {
  if (t == null) return 'text-steel-400'
  return t >= 85 ? 'text-aka-500' : t >= 70 ? 'text-kin-600 dark:text-kin-400' : 'text-matcha-600'
}
</script>

<template>
  <div class="space-y-7">
    <h1 class="heading"><WaIcon name="dashboard" :size="22" class="text-ai-700 dark:text-kin-400" /> {{ t('dashboard.title') }}</h1>

    <!-- Summary -->
    <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
      <div class="card flex items-center gap-3">
        <div class="flex h-10 w-10 items-center justify-center rounded-lg bg-accent-grad-soft text-ai-700 dark:text-kin-400"><WaIcon name="ports" :size="20" /></div>
        <div><div class="label !mb-0.5">{{ t('dashboard.portsTotal') }}</div><div class="stat text-2xl font-bold">{{ data.summary.ports_total ?? 0 }}</div></div>
      </div>
      <div class="card flex items-center gap-3">
        <div class="flex h-10 w-10 items-center justify-center rounded-lg bg-matcha-500/12 text-matcha-600"><WaIcon name="activity" :size="20" /></div>
        <div><div class="label !mb-0.5">{{ t('dashboard.running') }}</div><div class="stat text-2xl font-bold text-matcha-600">{{ data.summary.ports_running ?? 0 }}</div></div>
      </div>
      <div class="card flex items-center gap-3">
        <div class="flex h-10 w-10 items-center justify-center rounded-lg bg-accent-grad-soft text-ai-700 dark:text-kin-400"><WaIcon name="send" :size="18" /></div>
        <div><div class="label !mb-0.5">{{ t('dashboard.requestsTotal') }}</div><div class="stat text-2xl font-bold">{{ data.summary.requests_total ?? 0 }}</div></div>
      </div>
      <div class="card flex items-center gap-3">
        <div class="flex h-10 w-10 items-center justify-center rounded-lg bg-aka-500/10 text-aka-500"><WaIcon name="alert" :size="18" /></div>
        <div><div class="label !mb-0.5">{{ t('dashboard.errorsTotal') }}</div><div class="stat text-2xl font-bold" :class="data.summary.errors_total ? 'text-aka-500' : ''">{{ data.summary.errors_total ?? 0 }}</div></div>
      </div>
    </div>

    <!-- Ports -->
    <section>
      <h2 class="label mb-3 flex items-center gap-2">{{ t('dashboard.portStatus') }} · {{ t('dashboard.clickToEdit') }}</h2>
      <div v-if="!data.ports.length" class="card text-sm text-steel-400">{{ t('dashboard.noPorts') }}</div>
      <div v-else class="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        <button v-for="p in data.ports" :key="p.id" type="button" @click="openPort(p)"
          class="card card-hover group cursor-pointer text-left">
          <div class="flex items-center justify-between">
            <div class="font-semibold">{{ p.name }}</div>
            <span class="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-steel-400">
              <span class="dot" :class="dotColor(p.status)"></span>{{ p.status }}
            </span>
          </div>
          <div class="mt-1 font-mono text-[11px] text-steel-400">:{{ p.port }} · /{{ p.slug }} · {{ p.app_type }}</div>
          <!-- engine + GPU mapping -->
          <div v-if="p.engines && p.engines.length" class="mt-2 flex flex-wrap items-center gap-1.5">
            <span v-for="(e, i) in p.engines" :key="i"
              class="chip border" :class="e.primary ? 'border-accent-300/60 bg-accent-grad-soft text-ai-700 dark:text-kin-300' : 'border-steel-300/60 text-steel-400 dark:border-steel-700'"
              :title="e.provider + ' / ' + e.model">
              <WaIcon v-if="e.gpu_index" name="models" :size="11" />
              <span v-if="e.gpu_index" class="font-bold">GPU{{ e.gpu_index }}</span>
              <span class="font-mono lowercase">{{ e.kind }}</span>
              <span class="max-w-[88px] truncate font-mono">{{ e.model }}</span>
            </span>
          </div>
          <div class="mt-3 grid grid-cols-3 divide-x divide-steel-200/70 text-center dark:divide-steel-800">
            <div><div class="stat text-lg font-bold">{{ p.metrics.total }}</div><div class="label !mb-0">{{ t('dashboard.requests') }}</div></div>
            <div><div class="stat text-lg font-bold">{{ p.metrics.avg_latency_ms }}<span class="text-xs">ms</span></div><div class="label !mb-0">{{ t('dashboard.latency') }}</div></div>
            <div><div class="stat text-lg font-bold" :class="p.metrics.error_rate > 0 ? 'text-aka-500' : ''">{{ (p.metrics.error_rate * 100).toFixed(1) }}%</div><div class="label !mb-0">{{ t('dashboard.errorRate') }}</div></div>
          </div>
          <div class="mt-3 flex items-center justify-end gap-1 font-mono text-[10px] uppercase tracking-wider text-accent-500 opacity-0 transition group-hover:opacity-100">
            {{ t('dashboard.editArrow') }} <WaIcon name="arrow" :size="12" />
          </div>
        </button>
      </div>
    </section>

    <!-- GPU -->
    <section>
      <h2 class="label mb-3 flex items-center gap-2"><WaIcon name="models" :size="14" /> {{ t('dashboard.gpu') }}</h2>
      <div v-if="!data.gpu.available" class="card text-sm text-steel-400">{{ t('dashboard.noGpu') }}</div>
      <div v-else class="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div v-for="g in data.gpu.gpus" :key="g.index" class="card">
          <div class="font-mono text-sm font-semibold">GPU{{ g.index }} · {{ g.name }}</div>
          <div class="mt-3 grid grid-cols-4 divide-x divide-steel-200/70 text-center dark:divide-steel-800">
            <div><div class="stat text-base font-bold">{{ g.util_pct }}%</div><div class="label !mb-0">{{ t('dashboard.util') }}</div></div>
            <div><div class="stat text-base font-bold" :class="tempCls(g.temp_c)">{{ g.temp_c != null ? g.temp_c + '°' : '—' }}</div><div class="label !mb-0">{{ t('dashboard.temp') }}</div></div>
            <div><div class="stat text-base font-bold">{{ g.power_w != null ? g.power_w + 'W' : '—' }}</div><div class="label !mb-0">{{ t('dashboard.power') }}</div></div>
            <div><div class="stat text-base font-bold">{{ g.fan_pct != null ? g.fan_pct + '%' : '—' }}</div><div class="label !mb-0">{{ t('dashboard.fan') }}</div></div>
          </div>
          <div class="mt-3">
            <div class="mb-1 flex justify-between font-mono text-[11px] text-steel-400">
              <span>VRAM {{ g.mem_used_mb }} / {{ g.mem_total_mb }} MB</span><span>{{ g.mem_pct }}%</span>
            </div>
            <div class="h-2.5 w-full overflow-hidden rounded-full bg-steel-200 dark:bg-steel-800">
              <div class="h-full rounded-full bg-accent-grad transition-all duration-500" :style="{ width: g.mem_pct + '%' }"></div>
            </div>
          </div>
          <!-- services running on this GPU -->
          <div class="mt-3 border-t border-steel-200/70 pt-2 dark:border-steel-800">
            <div class="label !mb-1">{{ t('dashboard.runningHere') }}</div>
            <div v-if="g.services && g.services.length" class="flex flex-wrap gap-1.5">
              <button v-for="s in g.services" :key="s.id" type="button" @click="openPort(s)"
                class="chip bg-matcha-500/12 text-matcha-700 transition-colors hover:bg-matcha-500/20 dark:text-matcha-400">
                <span class="dot bg-matcha-500"></span>{{ s.name }}
              </button>
            </div>
            <div v-else class="font-mono text-[11px] text-steel-400">{{ t('dashboard.noServiceHere') }}</div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
