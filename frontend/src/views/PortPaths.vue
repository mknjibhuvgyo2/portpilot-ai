<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import WaIcon from '../components/WaIcon.vue'
import api from '../api/client'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const auth = useAuthStore()
const ports = ref<any[]>([])
const edits = ref<Record<number, { slug: string; path_alias: string; busy?: boolean; msg?: string }>>({})
const copied = ref('')

const ENDPOINT_BY_TYPE: Record<string, string> = { embedding: '/v1/embeddings', rerank: '/v1/rerank' }

async function load() {
  ports.value = (await api.get('/api/ports')).data
  for (const p of ports.value) edits.value[p.id] = { slug: p.slug, path_alias: p.path_alias || '' }
}
onMounted(load)

const origin = computed(() => `${location.protocol}//${location.hostname}`)
function gwBase(p: any) { return `${location.protocol}//${location.host}/gw/${p.slug}` }
function directBase(p: any) { return `${origin.value}:${p.port}` }

// every callable path a port exposes (the "/xxxxx" after host:port)
function pathsOf(p: any): { path: string; main?: boolean; custom?: boolean }[] {
  const main = ENDPOINT_BY_TYPE[p.app_type] || '/v1/chat/completions'
  const list: { path: string; main?: boolean; custom?: boolean }[] = [{ path: main, main: true }]
  if (p.path_alias) list.push({ path: p.path_alias, custom: true })
  list.push({ path: '/v1/models' }, { path: '/health' }, { path: '/info' })
  return list
}

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text) } catch { /* ignore */ }
  copied.value = text
  setTimeout(() => { if (copied.value === text) copied.value = '' }, 1200)
}

async function save(p: any) {
  const e = edits.value[p.id]
  e.busy = true; e.msg = ''
  try {
    const { data } = await api.patch(`/api/ports/${p.id}`, { slug: e.slug.trim(), path_alias: e.path_alias.trim() })
    e.msg = data?.restart_needed ? t('paths.restartHint') : t('paths.saved')
    await load()
  } catch (err: any) {
    e.msg = '❌ ' + (err.response?.data?.detail || 'error')
  } finally { e.busy = false; setTimeout(() => { if (edits.value[p.id]) edits.value[p.id].msg = '' }, 4000) }
}

async function restart(p: any) {
  await api.post(`/api/ports/${p.id}/stop`)
  await api.post(`/api/ports/${p.id}/start`)
  await load()
}
</script>

<template>
  <div class="space-y-5">
    <div>
      <h1 class="heading"><WaIcon name="network" :size="22" class="text-ai-700 dark:text-kin-400" /> {{ t('paths.title') }}</h1>
      <p class="mt-1 max-w-3xl text-xs leading-relaxed text-steel-400">{{ t('paths.subtitle') }}</p>
    </div>

    <div v-if="!ports.length" class="card py-12 text-center text-steel-400">{{ t('paths.empty') }}</div>

    <div v-for="p in ports" :key="p.id" class="card space-y-3">
      <!-- header -->
      <div class="flex flex-wrap items-center gap-2">
        <span class="font-semibold">{{ p.name }}</span>
        <span class="chip" :class="p.status === 'running' ? 'bg-matcha-500/12 text-matcha-600' : 'bg-steel-500/10 text-steel-400'">
          <span class="dot" :class="p.status === 'running' ? 'bg-matcha-500' : 'bg-steel-400'"></span>{{ p.status }}
        </span>
        <span class="font-mono text-[11px] text-steel-400">:{{ p.port }} · {{ p.app_type }}</span>
      </div>

      <!-- editable slug + custom path (admin) -->
      <div v-if="auth.isAdmin" class="grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <div>
          <label class="label">{{ t('paths.slug') }}</label>
          <input v-model="edits[p.id].slug" class="input font-mono text-xs" placeholder="my-service" />
        </div>
        <div>
          <label class="label">{{ t('paths.customPath') }}</label>
          <input v-model="edits[p.id].path_alias" class="input font-mono text-xs" placeholder="/myapi" />
        </div>
        <div class="flex items-center gap-2">
          <button class="btn-primary" :disabled="edits[p.id].busy" @click="save(p)"><WaIcon name="save" :size="14" />{{ t('common.save') }}</button>
          <button v-if="p.status === 'running'" class="btn-ghost" @click="restart(p)"><WaIcon name="play" :size="14" />{{ t('paths.restart') }}</button>
        </div>
      </div>
      <p v-if="edits[p.id]?.msg" class="text-xs" :class="(edits[p.id]?.msg || '').startsWith('❌') ? 'text-aka-600' : 'text-kin-600 dark:text-kin-400'">{{ edits[p.id]?.msg }}</p>
      <p class="text-[11px] text-steel-400">{{ t('paths.customPathHint') }}</p>

      <!-- endpoint table: each callable path (the /xxxxx) with direct + gateway URLs -->
      <div class="overflow-x-auto rounded-lg border border-steel-200/70 dark:border-steel-800">
        <table class="w-full text-xs">
          <thead class="border-b border-steel-200/70 text-left dark:border-steel-800">
            <tr class="[&>th]:label [&>th]:px-3 [&>th]:py-2">
              <th>{{ t('paths.path') }}</th>
              <th>{{ t('paths.directUrl') }}</th>
              <th>{{ t('paths.gatewayUrl') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ep in pathsOf(p)" :key="ep.path" class="border-t border-steel-100 first:border-0 dark:border-steel-800/60">
              <td class="px-3 py-2">
                <span class="font-mono">{{ ep.path }}</span>
                <span v-if="ep.main" class="chip ml-1 bg-accent-500/12 text-accent-600 dark:text-accent-300">{{ t('paths.main') }}</span>
                <span v-else-if="ep.custom" class="chip ml-1 bg-kin-400/15 text-kin-600 dark:text-kin-400">{{ t('paths.custom') }}</span>
              </td>
              <td class="px-3 py-2">
                <button class="group flex max-w-full items-center gap-1.5 text-left font-mono text-steel-500 hover:text-ai-700 dark:hover:text-kin-300" @click="copy(directBase(p) + ep.path)">
                  <span class="truncate">{{ directBase(p) }}{{ ep.path }}</span>
                  <WaIcon :name="copied === directBase(p) + ep.path ? 'save' : 'copy'" :size="12" class="shrink-0 opacity-50 group-hover:opacity-100" />
                </button>
              </td>
              <td class="px-3 py-2">
                <button class="group flex max-w-full items-center gap-1.5 text-left font-mono text-matcha-700 hover:text-matcha-500 dark:text-matcha-400" @click="copy(gwBase(p) + ep.path)">
                  <span class="truncate">/gw/{{ p.slug }}{{ ep.path }}</span>
                  <WaIcon :name="copied === gwBase(p) + ep.path ? 'save' : 'copy'" :size="12" class="shrink-0 opacity-50 group-hover:opacity-100" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="text-[11px] text-steel-400">{{ t('paths.gatewayNote') }}</p>
    </div>
  </div>
</template>
