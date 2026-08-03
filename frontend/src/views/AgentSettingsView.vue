<template>
  <div>
    <div class="header">
      <h2>Agent 配置</h2>
      <n-button type="primary" @click="openCreate">新建 Agent</n-button>
    </div>
    <n-data-table :columns="columns" :data="agents" :loading="loading" />

    <n-modal v-model:show="showModal" preset="dialog" :title="editingId ? '编辑 Agent' : '新建 Agent'">
      <n-form>
        <n-form-item label="名称">
          <n-input v-model:value="form.name" />
        </n-form-item>
        <n-form-item label="角色">
          <n-input v-model:value="form.role" />
        </n-form-item>
        <n-form-item label="能力描述">
          <n-input v-model:value="form.capabilities" type="textarea" />
        </n-form-item>
        <n-form-item label="设为默认">
          <n-switch v-model:value="form.is_default" />
        </n-form-item>
      </n-form>
      <template #action>
        <n-button @click="showModal = false">取消</n-button>
        <n-button type="primary" :loading="saving" @click="handleSave">保存</n-button>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import { NButton, NTag, useMessage, useDialog } from 'naive-ui'
import client from '@/api/client'

interface Agent {
  id: string
  name: string
  role: string
  capabilities: string
  is_default: boolean
}

const agents = ref<Agent[]>([])
const loading = ref(false)
const showModal = ref(false)
const saving = ref(false)
const editingId = ref('')
const message = useMessage()
const dialog = useDialog()
const form = ref({ name: '', role: '通用助手', capabilities: '', is_default: false })

const columns = [
  { title: '名称', key: 'name' },
  { title: '角色', key: 'role' },
  { title: '能力', key: 'capabilities', ellipsis: true },
  {
    title: '默认',
    key: 'is_default',
    width: 80,
    render: (row: Agent) => row.is_default ? h(NTag, { type: 'success', size: 'small' }, () => '是') : null,
  },
  {
    title: '操作',
    key: 'actions',
    width: 160,
    render: (row: Agent) => h('div', { style: 'display:flex;gap:8px' }, [
      h(NButton, { size: 'small', onClick: () => openEdit(row) }, () => '编辑'),
      h(NButton, { size: 'small', type: 'error', disabled: row.is_default, onClick: () => handleDelete(row) }, () => '删除'),
    ]),
  },
]

function openCreate() {
  editingId.value = ''
  form.value = { name: '', role: '通用助手', capabilities: '', is_default: false }
  showModal.value = true
}

function openEdit(row: Agent) {
  editingId.value = row.id
  form.value = { name: row.name, role: row.role, capabilities: row.capabilities, is_default: row.is_default }
  showModal.value = true
}

async function fetchAgents() {
  loading.value = true
  try {
    const { data } = await client.get<Agent[]>('/agents')
    agents.value = data
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  saving.value = true
  try {
    if (editingId.value) {
      await client.put(`/agents/${editingId.value}`, form.value)
    } else {
      await client.post('/agents', form.value)
    }
    showModal.value = false
    await fetchAgents()
    message.success('保存成功')
  } catch {
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}

function handleDelete(row: Agent) {
  dialog.warning({
    title: '确认删除',
    content: `确定删除 Agent「${row.name}」？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await client.delete(`/agents/${row.id}`)
        await fetchAgents()
        message.success('已删除')
      } catch {
        message.error('删除失败')
      }
    },
  })
}

onMounted(fetchAgents)
</script>

<style scoped>
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
</style>
