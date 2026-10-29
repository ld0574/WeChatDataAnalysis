import asyncio
import json
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


from wechat_decrypt_tool.routers import media as media_router  # noqa: E402  pylint: disable=wrong-import-position


class _FakeDisconnectingRequest:
    def __init__(self, disconnect_after: int):
        self._disconnect_after = disconnect_after
        self._calls = 0

    async def is_disconnected(self):
        self._calls += 1
        return self._calls >= self._disconnect_after


async def _read_sse_events(response) -> list[dict]:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))

    events = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


class TestMediaDecryptStreamCancel(unittest.TestCase):
    def test_scanning_is_cancellable_and_does_not_block_event_loop(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            entered = threading.Event()
            stopped = threading.Event()

            def slow_scan(_path, stop_event):
                entered.set()
                stop_event.wait(5)
                stopped.set()
                if False:
                    yield None

            class Request:
                disconnected = False

                async def is_disconnected(self):
                    return self.disconnected

            async def run():
                request = Request()
                with mock.patch.object(media_router, "_resolve_account_dir", return_value=root / "account"), \
                     mock.patch.object(media_router, "_resolve_account_wxid_dir", return_value=root), \
                     mock.patch.object(media_router, "_load_media_keys", return_value={"xor": 0xA5, "aes": ""}), \
                     mock.patch.object(media_router, "_iter_all_dat_files", side_effect=slow_scan), \
                     mock.patch.object(media_router, "_get_resource_dir", return_value=root / "resource"):
                    response = await media_router.decrypt_all_media_stream(request, account="demo")
                    iterator = response.body_iterator
                    self.assertIn('"type": "scanning"', await anext(iterator))
                    self.assertIn('"type": "start"', await anext(iterator))
                    pending = asyncio.create_task(anext(iterator))
                    await asyncio.sleep(0.1)
                    self.assertTrue(entered.is_set())
                    self.assertFalse(pending.done())
                    request.disconnected = True
                    with self.assertRaises(StopAsyncIteration):
                        await asyncio.wait_for(pending, 2)
                    self.assertTrue(await asyncio.to_thread(stopped.wait, 2))

            asyncio.run(run())

    def test_decrypt_starts_before_scan_completes(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            dat_path = root / "image.dat"
            dat_path.write_bytes(b"encrypted")
            release = threading.Event()

            def slow_scan(_path, stop_event):
                for _ in range(128):
                    yield dat_path, "abc123"
                release.wait(5)

            async def run():
                request = _FakeDisconnectingRequest(disconnect_after=9999)
                with mock.patch.object(media_router, "_resolve_account_dir", return_value=root / "account"), \
                     mock.patch.object(media_router, "_resolve_account_wxid_dir", return_value=root), \
                     mock.patch.object(media_router, "_load_media_keys", return_value={"xor": 0xA5, "aes": ""}), \
                     mock.patch.object(media_router, "_iter_all_dat_files", side_effect=slow_scan), \
                     mock.patch.object(media_router, "_get_resource_dir", return_value=root / "resource"), \
                     mock.patch.object(media_router, "_is_current_image_resource_source", return_value=False), \
                     mock.patch.object(media_router, "_decrypt_and_save_resource", return_value=(True, "ok")):
                    response = await media_router.decrypt_all_media_stream(request, account="demo")
                    iterator = response.body_iterator
                    try:
                        for _ in range(10):
                            chunk = await asyncio.wait_for(anext(iterator), 3)
                            event = json.loads(str(chunk).split('data: ', 1)[1])
                            if event['type'] == 'progress':
                                self.assertTrue(event['scanning'])
                                self.assertGreater(event['success_count'], 0)
                                break
                        else:
                            self.fail("No processing progress while the scan was still running")
                    finally:
                        release.set()
                        await iterator.aclose()

            asyncio.run(run())

    def test_stream_uses_default_concurrency(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            account_dir = root / "account"
            wxid_dir = root / "wxid"
            dat_path = wxid_dir / "image.dat"
            resource_dir = account_dir / "resource"
            wxid_dir.mkdir(parents=True, exist_ok=True)
            dat_path.write_bytes(b"encrypted")

            with mock.patch.object(media_router, "_resolve_account_dir", return_value=account_dir):
                with mock.patch.object(media_router, "_resolve_account_wxid_dir", return_value=wxid_dir):
                    with mock.patch.object(media_router, "_load_media_keys", return_value={"xor": 0xA5, "aes": ""}):
                        with mock.patch.object(media_router, "_iter_all_dat_files", return_value=iter([(dat_path, "abc123")])):
                            with mock.patch.object(media_router, "_get_resource_dir", return_value=resource_dir):
                                with mock.patch.object(media_router, "_try_find_decrypted_resource", return_value=None):
                                    with mock.patch.object(media_router, "_decrypt_and_save_resource", return_value=(True, "ok")):
                                        response = asyncio.run(
                                            media_router.decrypt_all_media_stream(
                                                request=_FakeDisconnectingRequest(disconnect_after=999),
                                                account="wxid_demo",
                                            )
                                        )
                                        events = asyncio.run(_read_sse_events(response))

            self.assertEqual(events[0].get("type"), "scanning")
            self.assertEqual(events[1].get("type"), "start")
            self.assertEqual(events[-1].get("type"), "complete")
            self.assertEqual({event["type"] for event in events}, {"scanning", "start", "scan_complete", "progress", "complete"})
            for event in events:
                if event.get("type") in {"start", "progress", "complete"}:
                    self.assertEqual(event.get("concurrency"), 10)

    def test_stream_uses_requested_concurrency(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            account_dir = root / "account"
            wxid_dir = root / "wxid"
            dat_path = wxid_dir / "image.dat"
            resource_dir = account_dir / "resource"
            wxid_dir.mkdir(parents=True, exist_ok=True)
            dat_path.write_bytes(b"encrypted")

            with mock.patch.object(media_router, "_resolve_account_dir", return_value=account_dir):
                with mock.patch.object(media_router, "_resolve_account_wxid_dir", return_value=wxid_dir):
                    with mock.patch.object(media_router, "_load_media_keys", return_value={"xor": 0xA5, "aes": ""}):
                        with mock.patch.object(media_router, "_iter_all_dat_files", return_value=iter([(dat_path, "abc123")])):
                            with mock.patch.object(media_router, "_get_resource_dir", return_value=resource_dir):
                                with mock.patch.object(media_router, "_try_find_decrypted_resource", return_value=None):
                                    with mock.patch.object(media_router, "_decrypt_and_save_resource", return_value=(True, "ok")):
                                        response = asyncio.run(
                                            media_router.decrypt_all_media_stream(
                                                request=_FakeDisconnectingRequest(disconnect_after=999),
                                                account="wxid_demo",
                                                concurrency=7,
                                            )
                                        )
                                        events = asyncio.run(_read_sse_events(response))

            for event in events:
                if event.get("type") in {"start", "progress", "complete"}:
                    self.assertEqual(event.get("concurrency"), 7)

    def test_stream_stops_processing_when_client_disconnects(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            account_dir = root / "account"
            wxid_dir = root / "wxid"
            dat_path = wxid_dir / "image.dat"
            resource_dir = account_dir / "resource"
            wxid_dir.mkdir(parents=True, exist_ok=True)
            dat_path.write_bytes(b"encrypted")

            request = _FakeDisconnectingRequest(disconnect_after=3)
            decrypt_mock = mock.Mock(return_value=(True, "ok"))

            with mock.patch.object(media_router, "_resolve_account_dir", return_value=account_dir):
                with mock.patch.object(media_router, "_resolve_account_wxid_dir", return_value=wxid_dir):
                    with mock.patch.object(media_router, "_load_media_keys", return_value={"xor": 0xA5, "aes": ""}):
                        with mock.patch.object(media_router, "_iter_all_dat_files", return_value=iter([(dat_path, "abc123")])):
                            with mock.patch.object(media_router, "_get_resource_dir", return_value=resource_dir):
                                    with mock.patch.object(media_router, "_try_find_decrypted_resource", return_value=None):
                                        with mock.patch.object(media_router, "_decrypt_and_save_resource", decrypt_mock):
                                            response = asyncio.run(
                                                media_router.decrypt_all_media_stream(
                                                    request=request,
                                                    account="wxid_demo",
                                                )
                                            )
                                            events = asyncio.run(_read_sse_events(response))

            self.assertEqual([event.get("type") for event in events], ["scanning", "start"])
            decrypt_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
