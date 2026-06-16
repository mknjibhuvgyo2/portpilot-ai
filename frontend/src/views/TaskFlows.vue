<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import WaIcon from '../components/WaIcon.vue'
import api from '../api/client'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const router = useRouter()
const auth = useAuthStore()
const ports = ref<any[]>([])

onMounted(async () => { ports.value = (await api.get('/api/ports')).data })

// Build a normalized task list for a port: explicit extra.tasks, else a single
// task synthesized from model_alias / system_prompt.
function tasksOf(p: any): Array<{ name: string; alias: string; prompt: string; mode: string }> {
  const tk = p?.extra?.tasks
  if (Array.isArray(tk) && tk.length) {
    return tk.map((x: any) => ({ name: x.name || '', alias: x.alias || '', prompt: x.prompt || '',
      mode: x.mode === 'pool' ? 'pool' : 'fixed' }))
  }
  return [{ name: '', alias: p.model_alias || '', prompt: p.system_prompt || '', mode: 'fixed' }]
}

const rows = computed(() =>
  ports.value.map((p) => ({ ...p, tasks: tasksOf(p) })))

function edit(p: any) { router.push({ path: '/ports', query: { edit: p.id } }) }
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="heading"><WaIcon name="sliders" :size="22" class="text-ai-700 dark:text-kin-400" /> {{ t('taskflows.title') }}</h1>
        <p class="mt-1 max-w-3xl text-xs leading-relaxed text-steel-400">{{ t('taskflows.subtitle') }}</p>
      </div>
    </div>

    <div v-if="!rows.length" class="card py-12 text-center text-steel-400">{{ t('taskflows.empty') }}</div>

    <div class="grid gap-4 md:grid-cols-2">
      <div v-for="p in rows" :key="p.id" class="card space-y-3">
        <div class="flex items-center justify-between gap-2">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <span class="truncate font-semibold">{{ p.name }}</span>
              <span class="chip" :class="p.status === 'running' ? 'bg-matcha-500/12 text-matcha-600' : 'bg-steel-500/10 text-steel-400'">
                <span class="dot" :class="p.status === 'running' ? 'bg-matcha-500' : 'bg-steel-400'"></span>{{ p.status }}
              </span>
            </div>
            <div class="mt-0.5 font-mono text-[11px] text-steel-400">:{{ p.port }} · {{ p.app_type }} · {{ p.tasks.length }} {{ p.tasks.length > 1 ? t('taskflows.steps') : t('taskflows.single') }}</div>
          </div>
          <button v-if="auth.isAdmin" class="btn-ghost shrink-0" @click="edit(p)"><WaIcon name="edit" :size="14" />{{ t('taskflows.edit') }}</button>
        </div>

        <!-- pipeline -->
        <div class="flex flex-wrap items-stretch gap-2">
          <template v-for="(tk, i) in p.tasks" :key="i">
            <span v-if="Number(i) > 0" class="self-center shrink-0 text-base text-steel-300 dark:text-steel-600">→</span>
            <div class="flex min-w-[140px] flex-col gap-1 rounded-lg border border-steel-200/70 bg-steel-50/50 p-2.5 dark:border-steel-800 dark:bg-steel-800/30">
              <div class="flex items-center gap-1.5">
                <span class="chip bg-accent-500/12 text-accent-600 dark:text-accent-300">{{ t('taskflows.stage') }} {{ Number(i) + 1 }}</span>
                <span class="truncate text-xs font-medium text-steel-600 dark:text-steel-300">{{ tk.name || '—' }}</span>
                <span class="chip ml-auto" :class="tk.mode === 'pool' ? 'bg-kin-400/15 text-kin-600 dark:text-kin-400' : 'bg-steel-500/10 text-steel-400'">
                  {{ tk.mode === 'pool' ? t('ports.taskflow.pool') : t('ports.taskflow.fixed') }}
                </span>
              </div>
              <div class="flex items-center gap-1 font-mono text-[11px]"
                :class="tk.alias ? 'text-ai-700 dark:text-kin-300' : 'text-aka-500'">
                <WaIcon name="models" :size="12" />{{ tk.alias || t('taskflows.noModel') }}
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
