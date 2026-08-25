"""core/cache.py 单元测试: TTLCache 与 MtimeCache。"""
import os
import tempfile
import time
import unittest

from core.cache import MtimeCache, TTLCache


class TTLCacheTests(unittest.TestCase):
    def test_get_or_set_caches_within_ttl(self) -> None:
        cache = TTLCache(ttl=60)
        calls = []

        def loader():
            calls.append(1)
            return {"a": 1}

        self.assertEqual(cache.get_or_set("k", loader), {"a": 1})
        self.assertEqual(cache.get_or_set("k", loader), {"a": 1})
        self.assertEqual(len(calls), 1)

    def test_expired_entry_triggers_reload(self) -> None:
        clock_value = [1000.0]
        cache = TTLCache(ttl=60, clock=lambda: clock_value[0])
        calls = []

        def loader():
            calls.append(1)
            return "v"

        cache.get_or_set("k", loader)
        clock_value[0] += 61  # 超过 TTL
        cache.get_or_set("k", loader)
        self.assertEqual(len(calls), 2)

    def test_loader_none_is_not_cached(self) -> None:
        cache = TTLCache(ttl=60)
        calls = []

        def loader():
            calls.append(1)
            return None

        self.assertIsNone(cache.get_or_set("k", loader))
        self.assertIsNone(cache.get_or_set("k", loader))
        self.assertEqual(len(calls), 2)  # None 不缓存, 每次重试

    def test_clear(self) -> None:
        cache = TTLCache(ttl=60)
        cache.set("k", 1)
        self.assertEqual(cache.clear(), 1)
        self.assertIsNone(cache.get("k"))

    def test_invalid_ttl_raises(self) -> None:
        with self.assertRaises(ValueError):
            TTLCache(ttl=0)


class MtimeCacheTests(unittest.TestCase):
    def test_same_mtime_hits_cache(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("one")
            path = f.name
        try:
            cache = MtimeCache()
            calls = []

            def loader(p):
                calls.append(1)
                return open(p).read()

            self.assertEqual(cache.get_or_load(path, loader), "one")
            self.assertEqual(cache.get_or_load(path, loader), "one")
            self.assertEqual(len(calls), 1)
        finally:
            os.unlink(path)

    def test_mtime_change_invalidates(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("one")
            path = f.name
        try:
            cache = MtimeCache()
            cache.get_or_load(path, lambda p: "first")
            # 强制更新文件内容与 mtime
            time.sleep(0.02)
            with open(path, "w") as f:
                f.write("two")
            os.utime(path, (time.time() + 10, time.time() + 10))
            self.assertEqual(cache.get_or_load(path, lambda p: "second"), "second")
        finally:
            os.unlink(path)

    def test_clone_protects_cached_object(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("x")
            path = f.name
        try:
            cache = MtimeCache()
            first = cache.get_or_load(path, lambda p: [1, 2], clone=lambda d: list(d))
            first.append(99)  # 修改返回值不应污染缓存
            second = cache.get_or_load(path, lambda p: [1, 2], clone=lambda d: list(d))
            self.assertEqual(second, [1, 2])
        finally:
            os.unlink(path)

    def test_missing_file_returns_none_without_caching(self) -> None:
        cache = MtimeCache()
        calls = []

        def loader(p):
            calls.append(1)
            return "v"

        self.assertIsNone(cache.get_or_load("/nonexistent/file.txt", loader))
        self.assertEqual(len(calls), 0)
        self.assertEqual(len(cache), 0)

    def test_loader_exception_returns_none(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("x")
            path = f.name
        try:
            cache = MtimeCache()

            def boom(p):
                raise RuntimeError("bad file")

            self.assertIsNone(cache.get_or_load(path, boom))
            self.assertEqual(len(cache), 0)
        finally:
            os.unlink(path)

    def test_custom_key_overrides_path_key(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("x")
            path = f.name
        try:
            cache = MtimeCache()
            a = cache.get_or_load(path, lambda p: "A", key="ka")
            b = cache.get_or_load(path, lambda p: "B", key="kb")
            self.assertEqual((a, b), ("A", "B"))  # 同一文件不同 key 各存一份
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
