<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import WaIcon from '../components/WaIcon.vue'
import api from '../api/client'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const auth = useAuthStore()
const ports = ref<any[]>([])
const templates = ref<Record<string, any>>({})
type RouteRow = { path: string; handler: string; enabled: boolean; description: string }
const edits = ref<Record<number, { slug: string; path_alias: string; routes: RouteRow[] | null; busy?: boolean; msg?: string }>>({})
const copied = ref('')

const ENDPOINT_BY_TYPE: Record<string, string> = { embedding: '/v1/embeddings', rerank: '/v1/rerank' }

// the template's built-in handlers (route metadata), or null for generic templates
function templateRoutesOf(p: any): any[] | null {
  const tpl = templates.value[p.app_type]
  return tpl && Array.isArray(tpl.routes) && tpl.routes.length ? tpl.routes : null
}
function descByHandler(p: any): Record<string, string> {
  const m: Record<string, string> = {}
  for (const r of (templateRoutesOf(p) || [])) m[r.handler] = r.description || ''
  return m
}
function handlerLabel(p: any, handler: string): string {
  const f = (templateRoutesOf(p) || []).find((r: any) => r.handler === handler)
  return f ? `${f.method} · ${f.handler}` : handler
}

async function load() {
  const [pr, tpls] = await Promise.all([api.get('/api/ports'), api.get('/api/ports/templates')])
  ports.value = pr.data
  templates.value = Object.fromEntries((tpls.data || []).map((t: any) => [t.app_type, t]))
  for (const p of ports.value) {
    const tr = templateRoutesOf(p)
    let routes: RouteRow[] | null = null
    if (tr) {
      const configured = p.extra && Array.isArray(p.extra.routes) && p.extra.routes.length ? p.extra.routes : null
      const dh = descByHandler(p)
      routes = (configured || tr).map((r: any) => ({
        path: r.path, handler: r.handler, enabled: r.enabled !== false,
        description: r.description || dh[r.handler] || '',
      }))
    }
    edits.value[p.id] = { slug: p.slug, path_alias: p.path_alias || '', routes }
  }
}
onMounted(load)

const origin = computed(() => `${location.protocol}//${location.hostname}`)
function gwBase(p: any) { return `${location.protocol}//${location.host}/gw/${p.slug}` }
function directBase(p: any) { return `${origin.value}:${p.port}` }

// read-only path list (non-admin view, or generic templates): real configured /
// template routes when available, else the generic OpenAI-compatible fallback.
function displayPaths(p: any): { path: string; main?: boolean; custom?: boolean; desc?: string; disabled?: boolean }[] {
  const tr = templateRoutesOf(p)
  const configured = p.extra && Array.isArray(p.extra.routes) && p.extra.routes.length ? p.extra.routes : null
  if (configured || tr) {
    const dh = descByHandler(p)
    return (configured || tr).map((r: any) => ({
      path: r.path,
      main: !!(tr && tr.find((t: any) => t.handler === r.handler && t.main)),
      custom: !!configured,
      disabled: configured ? r.enabled === false : false,
      desc: r.description || dh[r.handler] || '',
    }))
  }
  const main = ENDPOINT_BY_TYPE[p.app_type] || '/v1/chat/completions'
  const list: { path: string; main?: boolean; custom?: boolean }[] = [{ path: main, main: true }]
  if (p.path_alias) list.push({ path: p.path_alias, custom: true })
  list.push({ path: '/v1/models' }, { path: '/health' }, { path: '/info' })
  return list
}

function addRoute(p: any) {
  const tr = templateRoutesOf(p) || []
  edits.value[p.id].routes!.push({ path: '', handler: tr[0]?.handler || '', enabled: true, description: '' })
}
function removeRoute(p: any, i: number) { edits.value[p.id].routes!.splice(i, 1) }
function resetRoutes(p: any) {
  const tr = templateRoutesOf(p) || []
  const dh = descByHandler(p)
  edits.value[p.id].routes = tr.map((r: any) => ({ path: r.path, handler: r.handler, enabled: true, description: r.description || dh[r.handler] || '' }))
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
    const payload: any = { slug: e.slug.trim(), path_alias: e.path_alias.trim() }
    if (e.routes) payload.routes = e.routes
    const { data } = await api.patch(`/api/ports/${p.id}`, payload)
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

const headCls = '[&>th]:font-serif [&>th]:text-[11px] [&>th]:font-semibold [&>th]:tracking-[0.18em] [&>th]:text-steel-500 dark:[&>th]:text-steel-400 [&>th]:px-3 [&>th]:py-2'
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

      <!-- editable route table (templates with route metadata): path + 用途 + enable + URLs -->
      <div v-if="auth.isAdmin && edits[p.id]?.routes" class="space-y-2">
        <div class="overflow-x-auto rounded-lg border border-steel-200/70 dark:border-steel-800">
          <table class="w-full text-xs">
            <thead class="border-b border-steel-200/70 text-left dark:border-steel-800">
              <tr :class="headCls">
                <th class="!w-12">{{ t('ports.routes.enabled') }}</th>
                <th>{{ t('paths.path') }}</th>
                <th>{{ t('paths.purpose') }}</th>
                <th>{{ t('paths.directUrl') }}</th>
                <th>{{ t('paths.gatewayUrl') }}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in edits[p.id].routes" :key="i" class="border-t border-steel-100 align-top first:border-0 dark:border-steel-800/60">
                <td class="px-3 py-2"><input v-model="r.enabled" type="checkbox" class="accent-accent-500" /></td>
                <td class="px-3 py-2">
                  <input v-model="r.path" class="input !py-1 font-mono text-xs" :placeholder="t('ports.routes.pathPh')" />
                  <select v-model="r.handler" class="input mt-1 !py-1 text-[11px]">
                    <option v-for="h in templateRoutesOf(p)" :key="h.handler" :value="h.handler">{{ h.method }} · {{ h.handler }}</option>
                  </select>
                </td>
                <td class="px-3 py-2"><input v-model="r.description" class="input !py-1 text-xs" :placeholder="t('ports.routes.descPh')" /></td>
                <td class="px-3 py-2">
                  <button class="group flex max-w-[220px] items-center gap-1.5 font-mono text-steel-500 hover:text-ai-700 dark:hover:text-kin-300" @click="copy(directBase(p) + r.path)">
                    <span class="truncate">{{ directBase(p) }}{{ r.path }}</span>
                    <WaIcon :name="copied === directBase(p) + r.path ? 'save' : 'copy'" :size="12" class="shrink-0 opacity-50 group-hover:opacity-100" />
                  </button>
                </td>
                <td class="px-3 py-2">
                  <button class="group flex max-w-[220px] items-center gap-1.5 font-mono text-matcha-700 hover:text-matcha-500 dark:text-matcha-400" @click="copy(gwBase(p) + r.path)">
                    <span class="truncate">/gw/{{ p.slug }}{{ r.path }}</span>
                    <WaIcon :name="copied === gwBase(p) + r.path ? 'save' : 'copy'" :size="12" class="shrink-0 opacity-50 group-hover:opacity-100" />
                  </button>
                </td>
                <td class="px-3 py-2"><button class="btn-ghost !px-2 text-aka-500" @click="removeRoute(p, i)"><WaIcon name="trash" :size="14" /></button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="flex items-center gap-2">
          <button class="btn-ghost" @click="addRoute(p)"><WaIcon name="plus" :size="14" />{{ t('ports.routes.add') }}</button>
          <button class="btn-ghost" @click="resetRoutes(p)"><WaIcon name="download" :size="14" />{{ t('ports.routes.reset') }}</button>
          <span class="text-[11px] text-steel-400">{{ t('paths.routesEditHint') }}</span>
        </div>
        <p class="flex items-start gap-1.5 rounded-lg border border-aka-400/40 bg-aka-500/5 p-2.5 text-[11px] leading-relaxed text-aka-700 dark:text-aka-300">
          <WaIcon name="spark" :size="13" class="mt-0.5 shrink-0" />{{ t('ports.routes.vtWarn') }}
        </p>
      </div>

      <!-- read-only endpoint table (generic templates without route metadata) -->
      <div v-else class="overflow-x-auto rounded-lg border border-steel-200/70 dark:border-steel-800">
        <table class="w-full text-xs">
          <thead class="border-b border-steel-200/70 text-left dark:border-steel-800">
            <tr :class="headCls">
              <th>{{ t('paths.path') }}</th>
              <th>{{ t('paths.directUrl') }}</th>
              <th>{{ t('paths.gatewayUrl') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ep in displayPaths(p)" :key="ep.path" class="border-t border-steel-100 first:border-0 dark:border-steel-800/60">
              <td class="px-3 py-2">
                <div class="flex flex-wrap items-center gap-1">
                  <span class="font-mono" :class="ep.disabled ? 'text-steel-400 line-through' : ''">{{ ep.path }}</span>
                  <span v-if="ep.main" class="chip bg-accent-500/12 text-accent-600 dark:text-accent-300">{{ t('paths.main') }}</span>
                  <span v-else-if="ep.custom" class="chip bg-kin-400/15 text-kin-600 dark:text-kin-400">{{ t('paths.custom') }}</span>
                  <span v-if="ep.disabled" class="chip bg-aka-500/10 text-aka-600">{{ t('paths.disabled') }}</span>
                </div>
                <p v-if="ep.desc" class="mt-0.5 text-[11px] text-steel-400">{{ ep.desc }}</p>
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
