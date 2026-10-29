<template>
  <main class="mx-auto h-full w-full max-w-5xl overflow-y-auto p-6 text-gray-800">
    <div class="flex items-center justify-between gap-4">
      <div><h1 class="text-2xl font-semibold">持续归档</h1><p class="mt-2 text-sm text-gray-500">查询历史快照与之后采集的消息、图片和语音文字。</p></div>
      <NuxtLink to="/chat" class="text-green-700 underline">返回聊天</NuxtLink>
    </div>
    <section class="my-6 rounded-xl border border-gray-200 bg-gray-50 p-4" aria-live="polite">
      <p>{{ statusText }}</p>
      <p class="mt-2 text-sm">新增或变化记录 {{ info.overlayMessages || 0 }} 条 · 已发现 {{ info.sourceTables || info.tables || 0 }} 张消息表 · 近一天已核对 {{ info.reconciledTables || 0 }} 张</p>
      <p class="mt-2 text-sm text-gray-500">最近一轮完成：{{ date(info.lastPass) }}。历史核对会逐步补齐；软件关闭期间无法实时采集。</p>
      <p v-if="info.voiceModel" class="mt-2 text-sm">本地语音模型：{{ info.voiceModel }} · GPU</p>
      <p v-if="info.unmappedTables" class="mt-2 text-amber-700">{{ info.unmappedTables }} 张表的会话名称待解析，记录继续保留。</p>
      <p v-for="job in info.media || []" :key="job.kind + job.status" class="mt-1 text-sm">{{ job.kind === 'image' ? '图片' : '语音' }} · {{ job.status === 'ready' ? '已保存' : job.status === 'retry' ? '等待重试' : '排队中' }}：{{ job.count }}</p>
      <p v-if="info.mediaError" class="mt-2 text-red-700">媒体任务：{{ info.mediaError }}</p>
      <p v-for="(item, index) in info.mediaErrors || []" :key="index" class="mt-1 text-sm text-amber-700">{{ item.kind === 'voice' ? '语音' : '图片' }}等待重试（{{ item.count }} 项）：{{ item.error }}</p>
    </section>
    <form class="flex gap-3" @submit.prevent="runSearch(true)">
      <label for="archive-query" class="sr-only">归档搜索关键词</label>
      <input id="archive-query" v-model="query" class="min-w-0 flex-1 rounded-lg border px-4 py-3" placeholder="搜索消息、链接或语音文字" maxlength="200" required>
      <button class="rounded-lg bg-green-700 px-6 py-3 text-white disabled:opacity-50" :disabled="loading">{{ loading ? '搜索中…' : '搜索' }}</button>
    </form>
    <p v-if="error" role="alert" class="my-4 text-red-700">{{ error }}</p>
    <p v-if="searched && !loading && !hits.length" class="my-6 text-gray-500">没有找到符合关键词的归档记录。</p>
    <ol class="mt-6 divide-y divide-gray-200">
      <li v-for="hit in hits" :key="hit.archiveId || hit.id" class="py-5">
        <p class="text-sm text-gray-500">{{ hit.username }} · {{ date(hit.createTime) }} · {{ hit.renderType }}</p>
        <p class="mt-2 whitespace-pre-wrap break-words">{{ hit.title || hit.content }}</p>
        <p v-if="hit.voiceTranscript" class="mt-2 whitespace-pre-wrap">语音文字：{{ hit.voiceTranscript }}</p>
        <template v-for="item in hit.archiveMedia || []" :key="item.kind">
          <img v-if="item.kind === 'image' && item.path" :src="mediaUrl(item.path)" class="mt-3 max-h-96 max-w-full rounded-lg" alt="归档图片" loading="lazy">
          <audio v-if="item.kind === 'voice' && item.path" :src="mediaUrl(item.path)" controls class="mt-3" />
          <p v-if="item.status !== 'ready'" class="mt-2 text-sm text-amber-700">{{ item.kind === 'voice' && item.path ? '音频已保存，文字转写等待重试' : '媒体等待保存或重试' }}</p>
          <p v-if="item.status === 'ready' && item.error" class="mt-2 text-sm text-amber-700">{{ item.error }}</p>
        </template>
      </li>
    </ol>
    <button v-if="hasMore" class="my-6 rounded-lg border px-5 py-3" :disabled="loading" @click="runSearch(false)">加载更多</button>
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
useHead({ title: '持续归档 - 微信数据分析' })
const api = useApiBase()
const info = ref({})
const query = ref('')
const hits = ref([])
const loading = ref(false)
const searched = ref(false)
const hasMore = ref(false)
const error = ref('')
let activeQuery = ''
let nextOffset = 0
let timer
const statusText = computed(() => {
  if (info.value.error) return `采集异常，将自动重试：${info.value.error}`
  if (info.value.heartbeat && Date.now() / 1000 - info.value.heartbeat > 120) return '归档进度已超过两分钟未更新，请检查后台进程。'
  return info.value.status === 'scanning' ? '正在采集与核对消息…' : info.value.status === 'watching' ? '后台采集运行中，正在等待下一轮检查。' : '等待归档进程启动…'
})
const date = value => value ? new Date(Number(value) * 1000).toLocaleString() : '尚未完成首轮'
const mediaUrl = name => `${api}/archive/media/${encodeURIComponent(name)}`
async function refresh() {
  try {
    info.value = await $fetch(`${api}/archive/status`)
    const keys = hits.value.filter(hit => hit.archiveMediaKey && hit.archiveMedia?.some(item => item.status !== 'ready')).slice(0, 100).map(hit => hit.archiveMediaKey)
    if (keys.length) {
      const data = await $fetch(`${api}/archive/media/status`, { method: 'POST', body: { keys } })
      hits.value = hits.value.map(hit => {
        const items = data.items[hit.archiveMediaKey]
        if (!items) return hit
        return { ...hit, archiveMedia: items, voiceTranscript: items.find(item => item.kind === 'voice')?.transcript || hit.voiceTranscript }
      })
    }
  }
  catch (e) { error.value = e?.data?.detail || e.message }
}
async function runSearch(reset) {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    if (reset) { activeQuery = query.value; nextOffset = 0 }
    const data = await $fetch(`${api}/archive/search`, { query: { q: activeQuery, offset: nextOffset, limit: 50 }, timeout: 60000 })
    nextOffset += data.hits.length
    const seen = new Set()
    hits.value = (reset ? data.hits : [...hits.value, ...data.hits]).filter(hit => {
      const key = hit.archiveId || hit.id
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
    hasMore.value = data.hasMore
    searched.value = true
  } catch (e) { error.value = e?.data?.detail || e.message }
  finally { loading.value = false }
}
onMounted(() => { refresh(); timer = setInterval(refresh, 5000) })
onUnmounted(() => clearInterval(timer))
</script>
