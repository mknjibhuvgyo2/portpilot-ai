import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  { path: '/login', component: () => import('../views/Login.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('../components/Layout.vue'),
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'dashboard', component: () => import('../views/Dashboard.vue') },
      { path: 'ports', component: () => import('../views/Ports.vue') },
      { path: 'taskflows', component: () => import('../views/TaskFlows.vue') },
      { path: 'models', component: () => import('../views/Models.vue') },
      { path: 'promptlab', component: () => import('../views/PromptLab.vue') },
      { path: 'keys', component: () => import('../views/Keys.vue') },
      { path: 'users', component: () => import('../views/Users.vue') },
      { path: 'chat', component: () => import('../views/Chat.vue') },
      { path: 'settings', component: () => import('../views/Settings.vue') },
    ],
  },
]

const router = createRouter({ history: createWebHashHistory(), routes })

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  // Session restored from localStorage holds only the token; recover the role
  // (and username) once so admin-only UI survives a page refresh.
  if (auth.isAuthed && !auth.role) {
    try { await auth.fetchMe() } catch { auth.logout() }
  }
  if (!to.meta.public && !auth.isAuthed) return '/login'
  if (to.path === '/login' && auth.isAuthed) return '/dashboard'
  return true
})

export default router
