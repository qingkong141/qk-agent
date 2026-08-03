import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import client from '@/api/client'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const email = ref(localStorage.getItem('email') || '')
  const userId = ref(localStorage.getItem('userId') || '')

  const isLoggedIn = computed(() => !!token.value)

  async function login(userEmail: string, password: string) {
    const { data } = await client.post('/auth/login', { email: userEmail, password })
    token.value = data.access_token
    email.value = userEmail
    userId.value = data.user_id
    localStorage.setItem('token', data.access_token)
    localStorage.setItem('email', userEmail)
    localStorage.setItem('userId', data.user_id)
  }

  async function register(userEmail: string, password: string) {
    await client.post('/auth/register', { email: userEmail, password })
    await login(userEmail, password)
  }

  function logout() {
    token.value = ''
    email.value = ''
    userId.value = ''
    localStorage.removeItem('token')
    localStorage.removeItem('email')
    localStorage.removeItem('userId')
  }

  return { token, email, userId, isLoggedIn, login, register, logout }
})
