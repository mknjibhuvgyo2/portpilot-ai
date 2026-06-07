<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import WaIcon from '../components/WaIcon.vue'
import api from '../api/client'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const auth = useAuthStore()

const aliases = ref<any[]>([])
const ports = ref<any[]>([])
const applyPort = ref<number | null>(null)
const applyMsg = ref('')
const alias = ref('')
const presets = ref<any[]>([])
const selected = ref<Record<string, any>>({})
const examples = ref<{ input: string; output: string; images: string[] }[]>([{ input: '', output: '', images: [] }])
const requirements = ref('')

const result = ref('')
const constraints = ref<string[]>([])
const busy = ref(false)
const err = ref('')

const testing = ref(false)
const testPairs = ref<{ input: string; images: string[]; expected: string; actual: string }[] | null>(null)
const saveMsg = ref('')

async function load() {
  aliases.value = (await api.get('/api/models/aliases')).data
  if (aliases.value.length) alias.value = aliases.value[0].alias
  presets.value = (await api.get('/api/promptlab/presets')).data
  if (auth.isAdmin) ports.value = (await api.get('/api/ports')).data
}
onMounted(load)

function addExample() { examples.value.push({ input: '', output: '', images: [] }) }
function rmExample(i: number) { examples.value.splice(i, 1) }
function onExampleImage(e: Event, ex: { images: string[] }) {
  const files = (e.target as HTMLInputElement).files
  if (!files) return
  if (!ex.images) ex.images = []
  for (const f of Array.from(files)) {
    const r = new FileReader()
    r.onload = () => ex.images.push(r.result as string)
    r.readAsDataURL(f)
  }
  ;(e.target as HTMLInputElement).value = ''
}
function pickSingle(cat: string, opt: string) {
  selected.value[cat] = selected.value[cat] === opt ? undefined : opt
}

async function generate() {
  err.value = ''; busy.value = true; testPairs.value = null
  try {
    const clean: Record<string, any> = {}
    for (const [k, v] of Object.entries(selected.value)) if (v) clean[k] = v
    const { data } = await api.post('/api/promptlab/infer', {
      alias: alias.value, examples: examples.value, requirements: requirements.value, presets: clean,
    })
    result.value = data.system_prompt
    constraints.value = data.constraints || []
  } catch (e: any) { err.value = e.response?.data?.detail || 'error' } finally { busy.value = false }
}

async function runTest() {
  err.value = ''; testing.value = true
  try {
    const used = examples.value.filter((e) => e.input.trim() || e.images?.length)
    const { data } = await api.post('/api/promptlab/test', {
      alias: alias.value, system_prompt: result.value,
      inputs: used.map((e) => ({ text: e.input, images: e.images || [] })),
    })
    testPairs.value = used.map((e, i) => ({ input: e.input, images: e.images || [], expected: e.output, actual: data.outputs[i] }))
  } catch (e: any) { err.value = e.response?.data?.detail || 'error' } finally { testing.value = false }
}

async function saveToLib() {
  const name = prompt(t('promptlab.saveName'), 'inferred-prompt')
  if (!name) return
  await api.post('/api/prompts', { name, content: result.value, format: 'txt' })
  saveMsg.value = t('promptlab.saved'); setTimeout(() => (saveMsg.value = ''), 2000)
}
function copyResult() { navigator.clipboard?.writeText(result.value); saveMsg.value = t('promptlab.copied'); setTimeout(() => (saveMsg.value = ''), 1500) }

async function applyToPort() {
  if (!applyPort.value || !result.value) return
  const { data } = await api.patch(`/api/ports/${applyPort.value}`, { system_prompt: result.value })
  applyMsg.value = data.hot_swapped ? t('promptlab.appliedHot') : t('promptlab.applied')
  setTimeout(() => (applyMsg.value = ''), 3500)
}
</script>

<template>
  <div class="space-y-5">
    <div>
      <h1 class="heading"><WaIcon name="spark" :size="22" class="text-ai-700 dark:text-kin-400" /> {{ t('promptlab.title') }}</h1>
      <p class="mt-1 text-xs text-steel-400">{{ t('promptlab.subtitle') }}</p>
    </div>

    <div class="grid gap-5 lg:grid-cols-2">
      <!-- left: config -->
      <div class="space-y-4">
        <!-- alias -->
        <div class="card">
          <label class="label">{{ t('promptlab.alias') }}</label>
          <select v-model="alias" class="input">
            <option v-if="!aliases.length" value="">{{ t('promptlab.noAlias') }}</option>
            <option v-for="a in aliases" :key="a.id" :value="a.alias">{{ a.alias }}</option>
          </select>
          <p class="mt-1 text-[11px] text-steel-400">{{ t('promptlab.aliasHint') }}</p>
        </div>

        <!-- examples -->
        <div class="card space-y-3">
          <div class="flex items-center justify-between">
            <span class="label !mb-0">{{ t('promptlab.examples') }}</span>
            <button class="btn-ghost" @click="addExample"><WaIcon name="plus" :size="14" />{{ t('promptlab.addExample') }}</button>
          </div>
          <p class="text-[11px] text-steel-400">{{ t('promptlab.visionHint') }}</p>
          <div v-for="(ex, i) in examples" :key="i" class="rounded-lg border border-steel-200/70 p-2 dark:border-steel-800">
            <div class="mb-1 flex items-center justify-between">
              <span class="font-mono text-[10px] uppercase tracking-wider text-steel-400">#{{ i + 1 }}</span>
              <button v-if="examples.length > 1" class="text-steel-400 hover:text-aka-500" @click="rmExample(i)"><WaIcon name="trash" :size="13" /></button>
            </div>
            <div class="mb-1 flex flex-wrap items-center gap-1.5">
              <label class="btn-ghost cursor-pointer !py-1 !text-[11px]">
                <WaIcon name="image" :size="12" />{{ t('promptlab.addImage') }}
                <input type="file" accept="image/*" multiple class="hidden" @change="onExampleImage($event, ex)" />
              </label>
              <div v-for="(img, j) in ex.images" :key="j" class="relative">
                <img :src="img" class="h-9 w-9 rounded object-cover" />
                <button class="absolute -right-1 -top-1 rounded-full bg-aka-500 px-1 text-[9px] leading-none text-white" @click="ex.images.splice(j, 1)">×</button>
              </div>
            </div>
            <textarea v-model="ex.input" rows="2" class="input mb-1 text-xs" :placeholder="t('promptlab.inputPh')"></textarea>
            <textarea v-model="ex.output" rows="2" class="input text-xs" :placeholder="t('promptlab.outputPh')"></textarea>
          </div>
        </div>

        <!-- requirements + presets -->
        <div class="card space-y-3">
          <div>
            <label class="label">{{ t('promptlab.requirements') }}</label>
            <textarea v-model="requirements" rows="2" class="input text-xs" :placeholder="t('promptlab.requirementsPh')"></textarea>
          </div>
          <div>
            <label class="label">{{ t('promptlab.constraints') }}</label>
            <div class="space-y-2">
              <div v-for="p in presets" :key="p.id">
                <div class="mb-1 text-[11px] font-semibold text-steel-500 dark:text-steel-400">{{ t('promptlab.preset.' + p.id) }}</div>
                <div v-if="p.type === 'single'" class="flex flex-wrap gap-1.5">
                  <button v-for="o in p.options" :key="o.id" @click="pickSingle(p.id, o.id)"
                    class="chip cursor-pointer border transition-colors"
                    :class="selected[p.id] === o.id ? 'border-transparent bg-accent-grad text-steel-50' : 'border-steel-300/70 text-steel-500 hover:border-accent-400 dark:border-steel-700'">
                    {{ t('promptlab.opt.' + o.id) }}
                  </button>
                </div>
                <input v-else v-model="selected[p.id]" class="input !py-1.5 text-xs" :placeholder="t('promptlab.preset.' + p.id)" />
              </div>
            </div>
          </div>
          <button class="btn-primary w-full !py-2" :disabled="busy || !alias" @click="generate">
            <WaIcon :name="busy ? 'spinner' : 'spark'" :size="16" :class="busy && 'animate-spin'" />
            {{ busy ? t('promptlab.generating') : t('promptlab.generate') }}
          </button>
          <p v-if="err" class="text-sm text-aka-600">{{ err }}</p>
        </div>
      </div>

      <!-- right: result -->
      <div class="space-y-4">
        <div class="card space-y-3">
          <div class="flex items-center justify-between">
            <span class="label !mb-0">{{ t('promptlab.result') }}</span>
            <div v-if="result" class="flex items-center gap-2">
              <span v-if="saveMsg" class="text-[11px] text-matcha-600">{{ saveMsg }}</span>
              <button class="btn-ghost" @click="copyResult"><WaIcon name="copy" :size="13" />{{ t('common.copy') }}</button>
              <button class="btn-ghost" @click="saveToLib"><WaIcon name="save" :size="13" />{{ t('promptlab.save') }}</button>
            </div>
          </div>
          <textarea v-model="result" rows="12" class="input font-mono text-xs leading-relaxed" :placeholder="t('promptlab.resultPh')"></textarea>
          <div v-if="constraints.length" class="flex flex-wrap gap-1.5">
            <span v-for="(c, i) in constraints" :key="i" class="chip bg-kin-400/15 text-kin-700 dark:text-kin-300">{{ c }}</span>
          </div>
          <button class="btn-ghost w-full !py-2" :disabled="!result || testing" @click="runTest">
            <WaIcon :name="testing ? 'spinner' : 'play'" :size="15" :class="testing && 'animate-spin'" />
            {{ testing ? t('promptlab.testing') : t('promptlab.test') }}
          </button>

          <!-- apply to a running port (reuses hot-swap) -->
          <div v-if="result && auth.isAdmin" class="flex items-center gap-2 border-t border-steel-200/70 pt-3 dark:border-steel-800">
            <WaIcon name="ports" :size="15" class="shrink-0 text-steel-400" />
            <select v-model.number="applyPort" class="input !w-auto flex-1 !py-1.5 text-xs">
              <option :value="null">{{ t('promptlab.selectPort') }}</option>
              <option v-for="p in ports" :key="p.id" :value="p.id">{{ p.name }} :{{ p.port }} · {{ p.status }}</option>
            </select>
            <button class="btn-primary" :disabled="!applyPort" @click="applyToPort">{{ t('promptlab.applyToPort') }}</button>
          </div>
          <p v-if="applyMsg" class="text-[11px] text-matcha-600">{{ applyMsg }}</p>
        </div>

        <!-- test comparison -->
        <div v-if="testPairs" class="card space-y-3">
          <span class="label">{{ t('promptlab.testResults') }}</span>
          <div v-for="(p, i) in testPairs" :key="i" class="rounded-lg border border-steel-200/70 p-2 text-xs dark:border-steel-800">
            <div class="mb-1 font-mono text-[10px] uppercase tracking-wider text-steel-400">#{{ i + 1 }} · {{ p.input.slice(0, 60) || (p.images.length ? '🖼 image' : '') }}</div>
            <div v-if="p.images.length" class="mb-1 flex flex-wrap gap-1">
              <img v-for="(img, j) in p.images" :key="j" :src="img" class="h-10 w-10 rounded object-cover" />
            </div>
            <div class="grid grid-cols-2 gap-2">
              <div><div class="label !mb-0.5">{{ t('promptlab.expected') }}</div><div class="whitespace-pre-wrap text-steel-500">{{ p.expected }}</div></div>
              <div><div class="label !mb-0.5">{{ t('promptlab.actual') }}</div><div class="whitespace-pre-wrap">{{ p.actual }}</div></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
