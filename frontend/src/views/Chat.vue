<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import WaIcon from '../components/WaIcon.vue'
import api from '../api/client'

const { t } = useI18n()
const route = useRoute()

type Part =
  | { type: 'text'; text: string }
  | { type: 'image_url'; image_url: { url: string } }
  | { type: 'video_url'; video_url: { url: string } }
interface Msg { role: 'user' | 'assistant'; content: string | Part[]; display: string; dedup?: any }

const ports = ref<any[]>([])
const aliases = ref<any[]>([])
const mode = ref<'port' | 'model'>('model')
const portId = ref<number | null>(null)
const modelAlias = ref('')
const systemPrompt = ref('')
const streaming = ref(true)
const showParams = ref(false)
const params = ref<{ temperature: number | null; top_p: number | null; max_tokens: number | null }>({
  temperature: 0.7, top_p: 1, max_tokens: null,
})

function cleanParams() {
  const p: Record<string, number> = {}
  if (params.value.temperature != null && params.value.temperature !== ('' as any)) p.temperature = Number(params.value.temperature)
  if (params.value.top_p != null && params.value.top_p !== ('' as any)) p.top_p = Number(params.value.top_p)
  if (params.value.max_tokens != null && params.value.max_tokens !== ('' as any)) p.max_tokens = Number(params.value.max_tokens)
  return p
}

const messages = ref<Msg[]>([])
const input = ref('')
const images = ref<string[]>([]) // data URLs
const videos = ref<{ url: string; name: string }[]>([]) // data URLs + filename
const busy = ref(false)
const listEl = ref<HTMLElement | null>(null)

// voice
const recognizing = ref(false)
let recog: any = null
const voiceSupported = ref(false)

async function loadMeta() {
  ports.value = (await api.get('/api/ports')).data
  aliases.value = (await api.get('/api/models/aliases')).data
  if (aliases.value.length) modelAlias.value = aliases.value[0].alias
  const q = route.query.port
  if (q && ports.value.some((p) => String(p.id) === String(q))) {
    mode.value = 'port'; portId.value = Number(q)
  } else if (ports.value.length) {
    mode.value = 'port'; portId.value = ports.value[0].id
  }
}

onMounted(() => {
  loadMeta()
  const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
  if (SR) {
    voiceSupported.value = true
    recog = new SR()
    recog.lang = 'zh-CN'
    recog.continuous = false
    recog.interimResults = false
    recog.onresult = (e: any) => { input.value += e.results[0][0].transcript }
    recog.onend = () => { recognizing.value = false }
    recog.onerror = () => { recognizing.value = false }
  }
})

async function dedupMsg(m: Msg) {
  const text = typeof m.content === 'string' && m.content ? m.content : m.display
  const { data } = await api.post('/api/tools/dedup', { text })
  m.dedup = data
}
function copyText(s: string) { navigator.clipboard?.writeText(s) }

function toggleVoice() {
  if (!recog) return
  if (recognizing.value) { recog.stop(); recognizing.value = false }
  else { recognizing.value = true; recog.start() }
}

function onFile(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (!files) return
  for (const f of Array.from(files)) {
    const r = new FileReader()
    r.onload = () => images.value.push(r.result as string)
    r.readAsDataURL(f)
  }
}
function onVideoFile(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (!files) return
  for (const f of Array.from(files)) {
    if (f.size > 20 * 1024 * 1024) { alert(t('chat.videoTooBig')); continue }
    const r = new FileReader()
    r.onload = () => videos.value.push({ url: r.result as string, name: f.name })
    r.readAsDataURL(f)
  }
  ;(e.target as HTMLInputElement).value = ''
}

async function scrollDown() {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

function buildContent(): string | Part[] {
  if (!images.value.length && !videos.value.length) return input.value
  const parts: Part[] = [{ type: 'text', text: input.value }]
  for (const url of images.value) parts.push({ type: 'image_url', image_url: { url } })
  for (const v of videos.value) parts.push({ type: 'video_url', video_url: { url: v.url } })
  return parts
}

async function send() {
  if (busy.value || (!input.value.trim() && !images.value.length && !videos.value.length)) return
  const content = buildContent()
  const display = input.value
    + (images.value.length ? ` 🖼️×${images.value.length}` : '')
    + (videos.value.length ? ` 🎬×${videos.value.length}` : '')
  messages.value.push({ role: 'user', content, display })
  const wire = messages.value.map((m) => ({ role: m.role, content: m.content }))
  input.value = ''; images.value = []; videos.value = []
  busy.value = true
  const assistant: Msg = { role: 'assistant', content: '', display: '' }
  messages.value.push(assistant)
  await scrollDown()

  const body = {
    mode: mode.value, port_id: portId.value, model_alias: modelAlias.value,
    system_prompt: systemPrompt.value, messages: wire, stream: streaming.value, params: cleanParams(),
  }

  try {
    if (!streaming.value) {
      const { data } = await api.post('/api/chat', body)
      assistant.content = assistant.display = data.content
        ?? data.choices?.[0]?.message?.content ?? JSON.stringify(data)
    } else {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('token')}` },
        body: JSON.stringify(body),
      })
      const reader = resp.body!.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() || ''
        for (const line of lines) {
          const s = line.trim()
          if (!s.startsWith('data:')) continue
          const payload = s.slice(5).trim()
          if (payload === '[DONE]') continue
          try {
            const j = JSON.parse(payload)
            const delta = j.delta ?? j.choices?.[0]?.delta?.content ?? j.error
            if (delta) { assistant.display += delta; await scrollDown() }
          } catch { /* ignore partial */ }
        }
      }
      assistant.content = assistant.display
    }
  } catch (e: any) {
    assistant.display = '⚠️ ' + (e.message || '请求失败')
  } finally {
    busy.value = false
    await scrollDown()
  }
}
</script>

<template>
  <div class="flex h-full flex-col">
    <div class="mb-4 flex items-center justify-between">
      <h1 class="heading"><WaIcon name="chat" :size="22" class="text-ai-700 dark:text-kin-400" /> {{ t('chat.title') }}</h1>
      <button class="btn-ghost" @click="showParams = !showParams"><WaIcon name="sliders" :size="15" />{{ t('chat.params') }}</button>
    </div>

    <!-- Params panel -->
    <transition name="slide">
      <div v-if="showParams" class="card mb-3 grid grid-cols-3 gap-3">
        <div>
          <label class="label">temperature</label>
          <input v-model.number="params.temperature" type="number" step="0.1" min="0" max="2" class="input" />
        </div>
        <div>
          <label class="label">top_p</label>
          <input v-model.number="params.top_p" type="number" step="0.05" min="0" max="1" class="input" />
        </div>
        <div>
          <label class="label">{{ t('chat.maxTokensAuto') }}</label>
          <input v-model.number="params.max_tokens" type="number" min="1" class="input" placeholder="auto" />
        </div>
      </div>
    </transition>

    <!-- Controls -->
    <div class="card mb-4 flex flex-wrap items-end gap-3">
      <div>
        <label class="label">{{ t('chat.target') }}</label>
        <select v-model="mode" class="input w-36">
          <option value="model">{{ t('chat.modelDirect') }}</option>
          <option value="port">{{ t('chat.portService') }}</option>
        </select>
      </div>
      <div v-if="mode === 'port'">
        <label class="label">{{ t('chat.portService') }}</label>
        <select v-model.number="portId" class="input w-56">
          <option v-for="p in ports" :key="p.id" :value="p.id">{{ p.name }} :{{ p.port }} · {{ p.status }}</option>
        </select>
      </div>
      <div v-else>
        <label class="label">{{ t('chat.modelAlias') }}</label>
        <select v-model="modelAlias" class="input w-48">
          <option v-for="a in aliases" :key="a.id" :value="a.alias">{{ a.alias }}</option>
        </select>
      </div>
      <div class="flex-1 min-w-[160px]">
        <label class="label">{{ t('chat.systemPrompt') }}</label>
        <input v-model="systemPrompt" class="input" />
      </div>
      <label class="flex items-center gap-2 pb-2 text-sm"><input v-model="streaming" type="checkbox" class="accent-accent-600" />{{ t('chat.streaming') }}</label>
    </div>

    <!-- Messages -->
    <div ref="listEl" class="card flex-1 space-y-3 overflow-auto">
      <div v-if="!messages.length" class="py-10 text-center text-sm text-steel-400">
        {{ mode === 'port' ? t('chat.portTestHint') : t('chat.emptyHint') }}
      </div>
      <div v-for="(m, i) in messages" :key="i" class="flex flex-col" :class="m.role === 'user' ? 'items-end' : 'items-start'">
        <div class="max-w-[75%] whitespace-pre-wrap rounded px-4 py-2 text-sm"
          :class="m.role === 'user' ? 'bg-steel-800 text-steel-50 dark:bg-steel-200 dark:text-steel-900' : 'border border-steel-200 bg-steel-50 text-steel-900 dark:border-steel-800 dark:bg-steel-800/50 dark:text-steel-100'">
          {{ m.display || (m.role === 'assistant' && busy ? t('chat.thinking') : '') }}
        </div>
        <!-- one-click output dedup (assistant only) -->
        <div v-if="m.role === 'assistant' && m.display && !busy" class="mt-1 w-full max-w-[75%]">
          <button class="inline-flex items-center gap-1 text-[11px] text-accent-500 hover:underline" @click="dedupMsg(m)">
            <WaIcon name="copy" :size="12" />{{ t('chat.dedup') }}
          </button>
          <div v-if="m.dedup" class="mt-1 rounded-lg border border-kin-400/40 bg-kin-400/5 p-2">
            <div class="mb-1 flex items-center justify-between text-[11px] text-steel-500">
              <span>{{ t('chat.dedupResult', { f: m.dedup.format, o: m.dedup.original_count, d: m.dedup.deduped_count, r: m.dedup.removed }) }}</span>
              <button class="btn-ghost !py-0.5" @click="copyText(m.dedup.result)"><WaIcon name="copy" :size="12" />{{ t('common.copy') }}</button>
            </div>
            <pre class="max-h-60 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-steel-700 dark:text-steel-200">{{ m.dedup.result }}</pre>
          </div>
        </div>
      </div>
    </div>

    <!-- Image / video previews -->
    <div v-if="images.length || videos.length" class="mt-2 flex flex-wrap gap-2">
      <div v-for="(img, i) in images" :key="'i' + i" class="relative">
        <img :src="img" class="h-14 w-14 rounded object-cover" />
        <button class="absolute -right-1 -top-1 rounded-full bg-red-500 px-1 text-xs text-white" @click="images.splice(i, 1)">×</button>
      </div>
      <div v-for="(v, i) in videos" :key="'v' + i" class="flex items-center gap-1 rounded-md border border-steel-300/60 px-2 py-1 text-xs dark:border-steel-700">
        <WaIcon name="video" :size="13" class="text-accent-500" /><span class="max-w-[120px] truncate">{{ v.name }}</span>
        <button class="text-aka-500" @click="videos.splice(i, 1)">×</button>
      </div>
    </div>

    <!-- Input -->
    <div class="mt-3 flex items-end gap-2">
      <label class="btn-ghost cursor-pointer !px-2.5" :title="t('chat.image')">
        <WaIcon name="image" :size="16" /><input type="file" accept="image/*" multiple class="hidden" @change="onFile" />
      </label>
      <label class="btn-ghost cursor-pointer !px-2.5" :title="t('chat.video')">
        <WaIcon name="video" :size="16" /><input type="file" accept="video/*" multiple class="hidden" @change="onVideoFile" />
      </label>
      <button class="btn-ghost !px-2.5" :class="recognizing && '!border-red-400 !bg-red-500/10 !text-red-500'"
        :disabled="!voiceSupported" :title="voiceSupported ? t('chat.voice') : t('chat.voiceUnsupported')" @click="toggleVoice">
        <WaIcon name="mic" :size="16" :class="recognizing && 'animate-pulse'" />
      </button>
      <textarea v-model="input" rows="1" class="input flex-1 resize-none" :placeholder="t('chat.placeholder')"
        @keydown.enter.exact.prevent="send"></textarea>
      <button class="btn-primary" :disabled="busy" @click="send"><WaIcon name="send" :size="15" />{{ t('chat.send') }}</button>
    </div>
  </div>
</template>
