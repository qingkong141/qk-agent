<template>
  <n-layout has-sider style="height: 100vh">
    <n-layout-sider bordered :width="220" :native-scrollbar="false">
      <div class="logo">AI Agent</div>
      <n-menu
        :value="activeKey"
        :options="menuOptions"
        @update:value="handleMenu"
      />
      <div class="user-bar">
        <span>{{ auth.email }}</span>
        <n-button text type="error" size="small" @click="handleLogout">退出</n-button>
      </div>
    </n-layout-sider>
    <n-layout>
      <n-layout-content content-style="padding: 24px; height: 100%;">
        <router-view />
      </n-layout-content>
    </n-layout>
  </n-layout>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const activeKey = computed(() => route.path)

const menuOptions = [
  { label: '对话', key: '/chat' },
  { label: 'Agent 配置', key: '/agents' },
  { label: '知识库', key: '/knowledge' },
  { label: '监控面板', key: '/dashboard' },
]

function handleMenu(key: string) {
  router.push(key)
}

function handleLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.logo {
  padding: 20px 24px;
  font-size: 18px;
  font-weight: 700;
  color: #63e2b7;
}
.user-bar {
  position: absolute;
  bottom: 16px;
  left: 16px;
  right: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: #999;
}
</style>
