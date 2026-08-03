<template>
  <div>
    <div class="header">
      <h2>知识库管理</h2>
      <n-upload :custom-request="handleUpload" :show-file-list="false" accept=".pdf,.docx,.md,.txt">
        <n-button type="primary">上传文档</n-button>
      </n-upload>
    </div>
    <n-data-table :columns="columns" :data="documents" :loading="loading" />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import { NTag, useMessage, type UploadCustomRequestOptions } from 'naive-ui'
import client from '@/api/client'

interface Document {
  id: string
  filename: string
  file_type: string
  status: string
  chunk_count: number
  created_at: string
}

const documents = ref<Document[]>([])
const loading = ref(false)
const message = useMessage()

const statusMap: Record<string, { type: 'default' | 'info' | 'success' | 'warning'; label: string }> = {
  pending: { type: 'default', label: '等待中' },
  processing: { type: 'info', label: '处理中' },
  ready: { type: 'success', label: '就绪' },
  error: { type: 'warning', label: '失败' },
}

const columns = [
  { title: '文件名', key: 'filename' },
  { title: '类型', key: 'file_type', width: 80 },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row: Document) => {
      const s = statusMap[row.status] || { type: 'default' as const, label: row.status }
      return h(NTag, { type: s.type, size: 'small' }, () => s.label)
    },
  },
  { title: '分块数', key: 'chunk_count', width: 80 },
  { title: '上传时间', key: 'created_at', width: 180 },
]

async function fetchDocuments() {
  loading.value = true
  try {
    const { data } = await client.get<Document[]>('/documents')
    documents.value = data
  } finally {
    loading.value = false
  }
}

async function handleUpload({ file, onFinish, onError }: UploadCustomRequestOptions) {
  const formData = new FormData()
  formData.append('file', file.file as File)
  try {
    await client.post('/documents/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    message.success('上传成功，正在后台处理')
    await fetchDocuments()
    onFinish()
  } catch {
    message.error('上传失败')
    onError()
  }
}

onMounted(fetchDocuments)
</script>

<style scoped>
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
</style>
