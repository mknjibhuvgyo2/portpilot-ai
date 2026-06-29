<script setup lang="ts">
// Reusable per-stage task-flow editor. Mutates the passed `tasks` array in place
// (each stage = {name, alias, mode, pool, prompt, io}). Used by the port editor's
// task-flow tab and by each endpoint route's submenu (per-path task flow).
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import WaIcon from './WaIcon.vue'

const { t } = useI18n()
const props = defineProps<{
  tasks: any[]
  aliases: any[]
  stages?: Array<{ name: string; default_prompt: string; description?: string }>
}>()

const ioDefaults = () => ({
  temperature: '', top_p: '', max_tokens: '', sampling: 'both',
  image_detail: 'high', image_source: 'data_url', video_frames: '', force_two_stage: false,
  fixed_n: '', msg_max_chars: '', strict_json: true,
})
const newTask = () => ({ name: '', alias: '', prompt: '', mode: 'fixed', pool: [] as string[], io: ioDefaults() })

function addTask() { props.tasks.push(newTask()) }
function removeTask(i: number) { if (props.tasks.length > 1) props.tasks.splice(i, 1) }
const poolDraft = ref<Record<number, string>>({})
function addPoolVal(i: number, val: string) {
  val = (val || '').trim()
  if (val && !props.tasks[i].pool.includes(val)) props.tasks[i].pool.push(val)
}
function addPool(i: number) { addPoolVal(i, poolDraft.value[i] || ''); poolDraft.value[i] = '' }
function aliasesNotIn(pool: string[]) { return props.aliases.filter((a: any) => !pool.includes(a.alias)) }
const advOpen = ref<Record<number, boolean>>({})
function toggleAdv(i: number) { advOpen.value[i] = !advOpen.value[i] }
function stageDefault(i: number): string { return props.stages?.[i]?.default_prompt || '' }
</script>

<template>
  <div class="space-y-3">
    <div v-for="(tk, i) in tasks" :key="i"
      class="space-y-2 rounded-lg border border-steel-200/70 p-3 dark:border-steel-800">
      <div class="flex items-center gap-2">
        <span class="chip shrink-0 bg-accent-500/12 text-accent-600 dark:text-accent-300">{{ t('ports.taskflow.stage') }} {{ i + 1 }}</span>
        <input v-model="tk.name" class="input !py-1.5 text-xs" :placeholder="t('ports.taskflow.taskNamePh')" />
        <button v-if="tasks.length > 1" class="btn-ghost !px-2 text-aka-500" @click="removeTask(i)"><WaIcon name="trash" :size="14" /></button>
      </div>
      <div class="flex items-center gap-2">
        <select v-model="tk.mode" class="input !w-32 shrink-0 !py-1.5 text-xs">
          <option value="fixed">{{ t('ports.taskflow.fixed') }}</option>
          <option value="pool">{{ t('ports.taskflow.pool') }}</option>
        </select>
        <select v-model="tk.alias" class="input">
          <option value="">{{ t('ports.selectAlias') }}</option>
          <option v-for="a in aliases" :key="a.id" :value="a.alias">{{ a.alias }}</option>
        </select>
      </div>
      <div v-if="tk.mode === 'pool'" class="space-y-2 rounded-md border border-accent-500/30 bg-accent-500/5 p-2">
        <p class="text-[11px] leading-relaxed text-steel-500 dark:text-steel-400">{{ t('ports.taskflow.poolHint') }}</p>
        <div v-if="tk.pool.length" class="flex flex-wrap gap-1">
          <span v-for="(m, mi) in tk.pool" :key="mi" class="chip gap-1 bg-accent-500/15 text-accent-700 dark:text-accent-300">
            {{ m }}<button class="opacity-60 hover:opacity-100" @click="tk.pool.splice(mi, 1)">✕</button>
          </span>
        </div>
        <div class="flex gap-1">
          <input v-model="poolDraft[i]" class="input !py-1.5 text-xs" :placeholder="t('ports.taskflow.poolAddPh')" @keyup.enter.prevent="addPool(i)" />
          <button class="btn-ghost shrink-0" @click="addPool(i)"><WaIcon name="plus" :size="13" />{{ t('ports.taskflow.poolAdd') }}</button>
        </div>
        <div v-if="aliasesNotIn(tk.pool).length" class="flex flex-wrap gap-1">
          <button v-for="a in aliasesNotIn(tk.pool)" :key="a.id" class="chip bg-steel-500/10 text-steel-500 hover:bg-accent-500/15 hover:text-accent-600" @click="addPoolVal(i, a.alias)">+ {{ a.alias }}</button>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <span class="text-[11px] text-steel-400">{{ t('ports.taskflow.prompt') }}</span>
        <button v-if="stageDefault(i)" type="button" class="btn-ghost ml-auto !py-0.5 text-[11px]" @click="tk.prompt = stageDefault(i)">
          <WaIcon name="download" :size="12" />{{ t('ports.ioFormat.useDefault') }}
        </button>
      </div>
      <textarea v-model="tk.prompt" rows="8" class="input font-mono text-[11px] leading-relaxed"
        :placeholder="stageDefault(i) || t('ports.taskflow.prompt')"></textarea>

      <button class="flex w-full items-center gap-1 text-[11px] font-medium text-steel-500 hover:text-accent-600 dark:text-steel-400" @click="toggleAdv(i)">
        <WaIcon :name="advOpen[i] ? 'chevron-down' : 'chevron-right'" :size="12" />{{ t('ports.taskflow.adv') }}
        <span class="text-steel-400">· {{ t('ports.taskflow.advHint') }}</span>
      </button>
      <div v-if="advOpen[i]" class="space-y-3 rounded-md border border-steel-200/70 bg-steel-500/5 p-2.5 dark:border-steel-800">
        <div>
          <p class="mb-1 text-[10px] font-semibold uppercase tracking-wider text-steel-400">{{ t('ports.taskflow.advGen') }}</p>
          <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <label class="block"><span class="block text-[10px] text-steel-400">temperature</span>
              <input v-model="tk.io.temperature" type="number" step="0.1" min="0" max="2" class="input !py-1 text-xs" placeholder="—" /></label>
            <label class="block"><span class="block text-[10px] text-steel-400">top_p</span>
              <input v-model="tk.io.top_p" type="number" step="0.05" min="0" max="1" class="input !py-1 text-xs" placeholder="—" /></label>
            <label class="block"><span class="block text-[10px] text-steel-400">max_tokens</span>
              <input v-model="tk.io.max_tokens" type="number" step="1" min="1" class="input !py-1 text-xs" placeholder="—" /></label>
            <label class="block"><span class="block text-[10px] text-steel-400">{{ t('ports.taskflow.advSampling') }}</span>
              <select v-model="tk.io.sampling" class="input !py-1 text-xs">
                <option value="both">temp+top_p</option><option value="temperature">temperature</option><option value="top_p">top_p</option>
              </select></label>
          </div>
        </div>
        <div>
          <p class="mb-1 text-[10px] font-semibold uppercase tracking-wider text-steel-400">{{ t('ports.taskflow.advInput') }}</p>
          <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <label class="block"><span class="block text-[10px] text-steel-400">{{ t('ports.taskflow.advDetail') }}</span>
              <select v-model="tk.io.image_detail" class="input !py-1 text-xs"><option value="high">high</option><option value="low">low</option><option value="auto">auto</option></select></label>
            <label class="block"><span class="block text-[10px] text-steel-400">{{ t('ports.taskflow.advImgSrc') }}</span>
              <select v-model="tk.io.image_source" class="input !py-1 text-xs"><option value="data_url">data_url</option><option value="remote_url">remote_url</option></select></label>
            <label class="block"><span class="block text-[10px] text-steel-400">{{ t('ports.taskflow.advFrames') }}</span>
              <input v-model="tk.io.video_frames" type="number" step="1" min="1" class="input !py-1 text-xs" placeholder="6" /></label>
            <label class="flex items-center gap-1.5 pt-4 text-[11px]"><input v-model="tk.io.force_two_stage" type="checkbox" class="accent-accent-500" />{{ t('ports.taskflow.advTwoStage') }}</label>
          </div>
        </div>
        <div>
          <p class="mb-1 text-[10px] font-semibold uppercase tracking-wider text-steel-400">{{ t('ports.taskflow.advOutput') }}</p>
          <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <label class="block"><span class="block text-[10px] text-steel-400">{{ t('ports.taskflow.advFixedN') }}</span>
              <input v-model="tk.io.fixed_n" type="number" step="1" min="1" class="input !py-1 text-xs" placeholder="—" /></label>
            <label class="block"><span class="block text-[10px] text-steel-400">{{ t('ports.taskflow.advMaxChars') }}</span>
              <input v-model="tk.io.msg_max_chars" type="number" step="1" min="1" class="input !py-1 text-xs" placeholder="—" /></label>
            <label class="flex items-center gap-1.5 pt-4 text-[11px]"><input v-model="tk.io.strict_json" type="checkbox" class="accent-accent-500" />{{ t('ports.taskflow.advStrictJson') }}</label>
          </div>
        </div>
      </div>
    </div>
    <button class="btn-ghost" @click="addTask"><WaIcon name="plus" :size="14" />{{ t('ports.taskflow.add') }}</button>
  </div>
</template>
