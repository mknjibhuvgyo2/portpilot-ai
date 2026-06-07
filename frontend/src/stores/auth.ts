import { defineStore } from 'pinia'
import api from '../api/client'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    username: '',
    role: '',
  }),
  getters: {
    isAuthed: (s) => !!s.token,
    isAdmin: (s) => s.role === 'admin',
  },
  actions: {
    async login(username: string, password: string) {
      const form = new URLSearchParams()
      form.set('username', username)
      form.set('password', password)
      const { data } = await api.post('/api/auth/login', form)
      this.token = data.access_token
      this.username = data.username
      this.role = data.role
      localStorage.setItem('token', this.token)
    },
    async fetchMe() {
      const { data } = await api.get('/api/auth/me')
      this.username = data.username
      this.role = data.role
    },
    logout() {
      this.token = ''
      this.username = ''
      this.role = ''
      localStorage.removeItem('token')
    },
  },
})
