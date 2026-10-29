import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Archive from '../pages/archive.vue'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('archive media completion', () => {
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
