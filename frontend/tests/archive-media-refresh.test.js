import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Archive from '../pages/archive.vue'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('archive media completion', () => {
  it('shows separate voice stages, storage reserve and paused transcription', async () => {
    vi.stubGlobal('useHead', vi.fn())
    vi.stubGlobal('useApiBase', () => '/api')
    vi.stubGlobal('$fetch', vi.fn(async () => ({
      status: 'watching', voiceAsrPaused: true, auditPendingTables: 12,
      storage: { freeBytes: 4 * 1073741824, reserveBytes: 5 * 1073741824 },
      voiceStages: [{ stage: 'capture', status: 'ready', count: 100 }, { stage: 'transcribe', status: 'pending', count: 80 }],
    })))
    const wrapper = mount(Archive, { global: { stubs: { NuxtLink: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('语音原始音频 · 已完成：100')
    expect(wrapper.text()).toContain('语音文字转写 · 排队中：80')
    expect(wrapper.text()).toContain('媒体写入暂停，消息采集继续')
    expect(wrapper.text()).toContain('文字转写暂时暂停')
    expect(wrapper.text()).toContain('待核对 12 张')
    wrapper.unmount()
  })

  it('keeps saved voice playable while transcription finishes separately', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('useHead', vi.fn())
    vi.stubGlobal('useApiBase', () => '/api')
    vi.stubGlobal('$fetch', vi.fn(async url => {
      if (url === '/api/archive/search') return { hits: [{ id: 'voice', archiveMediaKey: 'voice-key', content: '[语音]', archiveMedia: [{ kind: 'voice', status: 'pending', path: 'saved.mp3' }] }], hasMore: false }
      if (url === '/api/archive/media/status') return { items: { 'voice-key': [{ kind: 'voice', status: 'ready', path: 'saved.mp3', transcript: '后补的语音文字' }] } }
      return { status: 'watching' }
    }))
    const wrapper = mount(Archive, { global: { stubs: { NuxtLink: true } } })
    await flushPromises()
    await wrapper.get('input').setValue('语音')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('audio').attributes('src')).toBe('/api/archive/media/saved.mp3')
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(wrapper.text()).toContain('后补的语音文字')
    expect(wrapper.get('audio').attributes('src')).toBe('/api/archive/media/saved.mp3')
    wrapper.unmount()
  })

  it('displays an image completed after the search without resubmitting the query', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('useHead', vi.fn())
    vi.stubGlobal('useApiBase', () => '/api')
    const fetch = vi.fn(async url => {
      if (url === '/api/archive/search') return { hits: [{ id: 'one', archiveMediaKey: 'key', content: '[图片]', archiveMedia: [{ kind: 'image', status: 'pending', path: '' }] }], hasMore: false }
      if (url === '/api/archive/media/status') return { items: { key: [{ kind: 'image', status: 'ready', path: 'saved.webp' }] } }
      return { status: 'watching', media: [] }
    })
    vi.stubGlobal('$fetch', fetch)
    const wrapper = mount(Archive, { global: { stubs: { NuxtLink: true } } })
    await flushPromises()
    await wrapper.get('input').setValue('图片')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('img').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(wrapper.get('img').attributes('src')).toBe('/api/archive/media/saved.webp')
    expect(fetch.mock.calls.filter(([url]) => url === '/api/archive/search')).toHaveLength(1)
    wrapper.unmount()
  })
})
