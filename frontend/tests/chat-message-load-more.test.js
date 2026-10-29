import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import MessageList from '../components/chat/MessageList.vue'

describe('chat history loading', () => {
  it('allows loading older messages without relying on a scroll event', async () => {
    const loadMoreMessages = vi.fn()
    const wrapper = mount(MessageList, {
      props: {
        state: {
          selectedContact: { username: 'example' },
          hasMoreMessages: true,
          isLoadingMessages: false,
          messages: [],
          messagesError: '',
          renderMessages: [],
          loadMoreMessages,
          onMessageScroll: vi.fn()
        }
      },
      global: { stubs: { MessageItem: true, ErrorNotice: true } }
    })

    const button = wrapper.get('button.message-list-load-more')
    await button.trigger('click')
    expect(loadMoreMessages).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})
