<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import WaIcon from '../components/WaIcon.vue'
import api from '../api/client'

const { t } = useI18n()
const router = useRouter()

const aliases = ref<any[]>([])
const modelAlias = ref('')
const code = ref('')
const strategy = ref('')
const extractionPrompt = ref('')
const showPrompt = ref(false)

const analyzing = ref(false)
const applying = ref(false)
const err = ref('')
const configText = ref('')      // editable generated JSON
const usedModel = ref('')
const result = ref<any | null>(null)

onMounted(async () => {
  const [g, a] = await Promise.all([
    api.get('/api/importer/guide'),
    api.get('/api/models/aliases'),
  ])
  strategy.value = g.data.strategy
  extractionPrompt.value = g.data.extraction_prompt
  aliases.value = a.data
  if (aliases.value.length) modelAlias.value = aliases.value[0].alias
})

async function analyze() {
  err.value = ''; result.value = null
  if (!code.value.trim()) { err.value = t('importer.needCode'); return }
  analyzing.value = true
  try {
    const { data } = await api.post('/api/importer/analyze', { code: code.value, model_alias: modelAlias.value })
    configText.value = JSON.stringify(data.config, null, 2)
    usedModel.value = data.model_used || ''
  } catch (e: any) {
    err.value = e.response?.data?.detail || 'error'
  } finally {
    analyzing.value = false
  }
}

async function apply() {
  err.value = ''; result.value = null
  let parsed: any
  try { parsed = JSON.parse(configText.value) } catch { err.value = t('importer.badJson'); return }
  applying.value = true
  try {
    const { data } = await api.post('/api/importer/apply', parsed)
    result.value = data
  } catch (e: any) {
    err.value = e.response?.data?.detail || 'error'
  } finally {
    applying.value = false
  }
}
</script>

<template>
  <div class="space-y-5">
    <div>
      <h1 class="heading"><WaIcon name="upload" :size="22" class="text-ai-700 dark:text-kin-400" /> {{ t('importer.title') }}</h1>
      <p class="mt-1 max-w-3xl text-xs leading-relaxed text-steel-400">{{ t('importer.subtitle') }}</p>
    </div>

    <div class="card space-y-3">
      <p class="rounded-lg bg-matcha-500/8 p-3 text-xs leading-relaxed text-steel-600 dark:text-steel-300">{{ strategy }}</p>

      <div class="flex flex-wrap items-end gap-3">
        <div class="min-w-[220px]">
          <label class="label">{{ t('importer.model') }}</label>
          <select v-model="modelAlias" class="input">
            <option v-for="a in aliases" :key="a.id" :value="a.alias">{{ a.alias }}</option>
          </select>
        </div>
        <p class="flex-1 text-[11px] text-steel-400">{{ t('importer.modelHint') }}</p>
        <button class="btn-ghost" @click="showPrompt = !showPrompt"><WaIcon name="file" :size="14" />{{ t('importer.viewPrompt') }}</button>
      </div>

      <pre v-if="showPrompt" class="max-h-60 overflow-auto rounded-lg bg-steel-100/70 p-3 text-[11px] leading-relaxed text-steel-600 dark:bg-steel-800/50 dark:text-steel-300">{{ extractionPrompt }}</pre>

      <textarea v-model="code" rows="14" class="input font-mono text-xs leading-relaxed" :placeholder="t('importer.codePh')"></textarea>

      <div class="flex items-center gap-3">
        <button class="btn-primary" :disabled="analyzing" @click="analyze">
          <WaIcon :name="analyzing ? 'spinner' : 'spark'" :size="15" :class="analyzing ? 'animate-spin' : ''" />
          {{ analyzing ? t('importer.analyzing') : t('importer.analyze') }}
        </button>
        <p v-if="err" class="text-sm text-aka-600">{{ err }}</p>
      </div>
    </div>

    <!-- generated config -->
    <div v-if="configText" class="card space-y-3">
      <div class="flex items-center justify-between">
        <label class="label !mb-0">{{ t('importer.generated') }}</label>
        <span v-if="usedModel" class="chip bg-steel-500/10 text-steel-400">{{ t('importer.usedModel') }}: {{ usedModel }}</span>
      </div>
      <textarea v-model="configText" rows="16" class="input font-mono text-[11px] leading-relaxed"></textarea>
      <button class="btn-primary" :disabled="applying" @click="apply">
        <WaIcon :name="applying ? 'spinner' : 'download'" :size="15" :class="applying ? 'animate-spin' : ''" />
        {{ applying ? t('importer.applying') : t('importer.apply') }}
      </button>
    </div>

    <!-- result -->
    <div v-if="result" class="card space-y-2">
      <div class="flex items-center justify-between">
        <h2 class="heading text-sm"><WaIcon name="power" :size="16" class="text-matcha-600" /> {{ t('importer.result') }}</h2>
        <button class="btn-ghost" @click="router.push('/ports')">{{ t('importer.goPorts') }} →</button>
      </div>
      <p class="text-xs text-steel-500">{{ t('importer.created') }}: {{ (result.created || []).length }}</p>
      <div class="flex flex-wrap gap-2">
        <span v-for="c in result.created" :key="c.id" class="chip bg-matcha-500/12 text-matcha-600">
          {{ c.name }} · :{{ c.port }} · {{ c.app_type }}
        </span>
      </div>
    </div>
  </div>
</template>
