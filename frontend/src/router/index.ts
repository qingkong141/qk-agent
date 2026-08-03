import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/components/layout/AppLayout.vue'),
      children: [
        { path: '', redirect: '/chat' },
        { path: 'chat', name: 'Chat', component: () => import('@/views/ChatView.vue') },
        { path: 'agents', name: 'Agents', component: () => import('@/views/AgentSettingsView.vue') },
        { path: 'knowledge', name: 'Knowledge', component: () => import('@/views/KnowledgeBaseView.vue') },
        { path: 'dashboard', name: 'Dashboard', component: () => import('@/views/DashboardView.vue') },
      ],
    },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isLoggedIn) {
    return '/login'
  }
})

export default router
