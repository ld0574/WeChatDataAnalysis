import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import patch

from wechat_decrypt_tool.routers import chat


def test_slow_search_does_not_block_event_loop():
    started = threading.Event()
    release = threading.Event()

    def slow_search(*args, **kwargs):
        started.set()
        if not release.wait(2):
            raise RuntimeError("event loop did not remain responsive")
        return {"status": "success", "hits": []}

    async def run():
        task = asyncio.create_task(chat.search_chat_messages(SimpleNamespace(), q="example"))
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set()
            assert not task.done()
        finally:
            release.set()
        result = await asyncio.wait_for(task, timeout=2)
        assert result["status"] == "success"

    with patch.object(chat, "_search_chat_messages_via_fts", side_effect=slow_search):
        asyncio.run(run())
