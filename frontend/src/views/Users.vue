<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import WaIcon from '../components/WaIcon.vue'
import api from '../api/client'
import { useAuthStore } from '../stores/auth'

const { t } = useI18n()
const auth = useAuthStore()
const users = ref<any[]>([])
const showForm = ref(false)
const form = ref({ username: '', password: '', role: 'user' })
const err = ref('')

async function load() { users.value = (await api.get('/api/users')).data }
onMounted(load)

function isSelf(u: any) { return auth.username === u.username }
function fail(e: any) { alert(e.response?.data?.detail || t('users.opFailed')) }

async function create() {
  err.value = ''
  try {
    await api.post('/api/users', form.value)
    showForm.value = false
    form.value = { username: '', password: '', role: 'user' }
    await load()
  } catch (e: any) { err.value = e.response?.data?.detail || 'error' }
}
async function setRole(u: any, role: string) {
  try { await api.patch(`/api/users/${u.id}`, { role }); await load() } catch (e) { fail(e); await load() }
}
async function toggleActive(u: any) {
  try { await api.patch(`/api/users/${u.id}`, { is_active: !u.is_active }); await load() } catch (e) { fail(e); await load() }
}
async function changePw(u: any) {
  const pw = prompt(t('users.newPwFor', { u: u.username }))
  if (!pw) return
  try { await api.patch(`/api/users/${u.id}`, { password: pw }); alert(t('users.pwChanged')) } catch (e) { fail(e) }
}
async function remove(u: any) {
  if (!confirm(`${t('common.delete')} ${u.username}?`)) return
  try { await api.delete(`/api/users/${u.id}`); await load() } catch (e) { fail(e) }
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-center justify-between">
      <h1 class="heading"><WaIcon name="users" :size="22" class="text-ai-700 dark:text-kin-400" /> {{ t('users.title') }}</h1>
      <button class="btn-primary" @click="showForm = true"><WaIcon name="plus" :size="15" />{{ t('users.newUser') }}</button>
    </div>

    <div class="card overflow-x-auto p-0">
      <table class="w-full text-sm">
        <thead class="border-b border-steel-200/70 text-left dark:border-steel-800">
          <tr class="[&>th]:label [&>th]:px-4 [&>th]:py-3">
            <th>{{ t('users.username') }}</th><th>{{ t('users.role') }}</th>
            <th>{{ t('users.status') }}</th><th>{{ t('users.created') }}</th>
            <th class="text-right">{{ t('users.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id" class="border-b border-steel-100 last:border-0 dark:border-steel-800/60">
            <td class="px-4 py-3 font-medium">
              {{ u.username }}
              <span v-if="isSelf(u)" class="ml-1 chip bg-accent-grad-soft text-ai-700 dark:text-kin-300">{{ t('users.you') }}</span>
            </td>
            <td class="px-4 py-3">
              <select :value="u.role" class="input !w-auto !py-1 text-xs" @change="setRole(u, ($event.target as HTMLSelectElement).value)">
                <option value="admin">{{ t('users.roleAdmin') }}</option>
                <option value="user">{{ t('users.roleUser') }}</option>
              </select>
            </td>
            <td class="px-4 py-3">
              <span class="chip" :class="u.is_active ? 'bg-matcha-500/12 text-matcha-700 dark:text-matcha-400' : 'bg-steel-500/10 text-steel-400'">
                <span class="dot" :class="u.is_active ? 'bg-matcha-500' : 'bg-steel-400'"></span>{{ u.is_active ? t('users.active') : t('users.inactive') }}
              </span>
            </td>
            <td class="stat px-4 py-3 text-xs text-steel-400">{{ (u.created_at || '').slice(0, 10) }}</td>
            <td class="px-4 py-3">
              <div class="flex justify-end gap-2">
                <button class="btn-ghost" @click="toggleActive(u)"><WaIcon name="power" :size="14" />{{ u.is_active ? t('users.disable') : t('users.enable') }}</button>
                <button class="btn-ghost" @click="changePw(u)"><WaIcon name="lock" :size="14" />{{ t('users.changePw') }}</button>
                <button class="btn-danger" :disabled="isSelf(u)" @click="remove(u)"><WaIcon name="trash" :size="14" /></button>
              </div>
            </td>
          </tr>
          <tr v-if="!users.length"><td colspan="5" class="px-4 py-10 text-center text-steel-400">{{ t('users.empty') }}</td></tr>
        </tbody>
      </table>
    </div>
    <p class="font-mono text-[11px] text-steel-400">{{ t('users.guardHint') }}</p>

    <!-- create modal -->
    <div v-if="showForm" class="fixed inset-y-0 right-0 left-[var(--sidebar-w,0px)] z-50 flex items-center justify-center overflow-y-auto bg-steel-950/50 p-4 backdrop-blur-sm" @click.self="showForm = false">
      <div class="card my-auto w-[90vw] max-w-[820px] animate-fade-up space-y-3 shadow-glow">
        <h2 class="heading text-sm"><WaIcon name="users" :size="18" /> {{ t('users.createTitle') }}</h2>
        <div><label class="label">{{ t('users.username') }}</label><input v-model="form.username" class="input" /></div>
        <div><label class="label">{{ t('users.password') }}</label><input v-model="form.password" type="password" class="input" /></div>
        <div>
          <label class="label">{{ t('users.role') }}</label>
          <select v-model="form.role" class="input">
            <option value="user">{{ t('users.roleUser') }}</option>
            <option value="admin">{{ t('users.roleAdmin') }}</option>
          </select>
        </div>
        <p v-if="err" class="text-sm text-aka-600">{{ err }}</p>
        <div class="flex justify-end gap-2">
          <button class="btn-ghost" @click="showForm = false">{{ t('common.cancel') }}</button>
          <button class="btn-primary" @click="create">{{ t('common.create') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
