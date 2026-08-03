<template>
  <div :class="['message', message.role]">
    <div class="avatar">{{ avatar }}</div>
    <div class="bubble">
      <div v-if="message.agentName" class="agent-badge">{{ message.agentName }}</div>
      <div v-if="message.toolName" class="tool-badge">{{ message.toolName }}</div>
      <div class="content" v-html="rendered" />
      <span v-if="message.streaming" class="cursor">|</span>
      <SourceCitation v-if="message.sources?.length && !message.streaming" :sources="message.sources" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import type { ChatMessage } from '@/stores/chat'
import SourceCitation from './SourceCitation.vue'

const props = defineProps<{ message: ChatMessage }>()

const md = new MarkdownIt({
  highlight(str: string, lang: string) {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(str, { language: lang }).value
    }
    return hljs.highlightAuto(str).value
  },
})

const avatar = computed(() => {
  if (props.message.role === 'user') return '我'
  if (props.message.role === 'tool') return '🔧'
  if (props.message.role === 'agent') return '🤖'
  return 'AI'
})

const rendered = computed(() => {
  if (props.message.role === 'user') return props.message.content
  if (!props.message.content) {
    return props.message.streaming ? '<span style="color:#888">思考中...</span>' : ''
  }
  if (props.message.role === 'agent') {
    return `<span style="color:#aaa;font-size:13px">${props.message.content}</span>`
  }
  return md.render(props.message.content)
})
</script>

<style scoped>
.message {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}
.message.user {
  flex-direction: row-reverse;
}
.message.agent .bubble {
  background: #1a2a1a;
  border: 1px dashed #3a5a3a;
}
.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #333;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  flex-shrink: 0;
}
.message.user .avatar {
  background: #2080f0;
}
.bubble {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 12px;
  background: #262629;
  line-height: 1.6;
}
.message.user .bubble {
  background: #1a3a5c;
}
.agent-badge {
  font-size: 11px;
  color: #70c0e8;
  margin-bottom: 4px;
}
.tool-badge {
  font-size: 11px;
  color: #63e2b7;
  margin-bottom: 4px;
}
.cursor {
  animation: blink 1s infinite;
}
@keyframes blink {
  50% { opacity: 0; }
}
</style>
