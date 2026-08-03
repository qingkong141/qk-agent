<template>
  <div class="login-page">
    <n-card title="AI 智能体管理台" style="width: 400px">
      <n-tabs v-model:value="tab" type="segment">
        <n-tab-pane name="login" tab="登录">
          <n-form @submit.prevent="handleLogin">
            <n-form-item label="邮箱">
              <n-input v-model:value="email" placeholder="user@example.com" />
            </n-form-item>
            <n-form-item label="密码">
              <n-input v-model:value="password" type="password" show-password-on="click" />
            </n-form-item>
            <n-button type="primary" block :loading="loading" attr-type="submit">登录</n-button>
          </n-form>
        </n-tab-pane>
        <n-tab-pane name="register" tab="注册">
          <n-form @submit.prevent="handleRegister">
            <n-form-item label="邮箱">
              <n-input v-model:value="email" placeholder="user@example.com" />
            </n-form-item>
            <n-form-item label="密码">
              <n-input v-model:value="password" type="password" show-password-on="click" />
            </n-form-item>
            <n-button type="primary" block :loading="loading" attr-type="submit">注册</n-button>
          </n-form>
        </n-tab-pane>
      </n-tabs>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const message = useMessage()

const tab = ref('login')
const email = ref('')
const password = ref('')
const loading = ref(false)

async function handleLogin() {
  loading.value = true
  try {
    await auth.login(email.value, password.value)
    router.push('/chat')
  } catch (e: unknown) {
    message.error((e as Error).message || '登录失败')
  } finally {
    loading.value = false
  }
}

async function handleRegister() {
  loading.value = true
  try {
    await auth.register(email.value, password.value)
    router.push('/chat')
  } catch (e: unknown) {
    message.error((e as Error).message || '注册失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #101014;
}
</style>
