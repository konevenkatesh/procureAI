"""R9.1 unit test — embedding pre-cache.

Verifies:
  1. _preload_embeddings batches multiple queries into ONE call.
  2. _embed_query checks cache before calling Vertex.
  3. _reset_embed_cache clears cache between runs.
  4. Cache hits avoid the network call entirely.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "services" / "m1-drafter"))

from app.workflow_v2 import (  # noqa: E402
    _preload_embeddings, _embed_query, _reset_embed_cache, _EMBED_CACHE,
)


FAKE_VEC = [0.1] * 768


def _fake_embed_batch(texts, **kwargs):
    return [FAKE_VEC for _ in texts]


def _fake_embed_single(text, **kwargs):
    return FAKE_VEC


def test_preload_batches_once():
    _reset_embed_cache()
    with patch("app.vertex_client.embed_texts_batch", side_effect=_fake_embed_batch) as m:
        n = _preload_embeddings(["q1", "q2", "q3", "q4", "q5"])
        assert n == 5, f"expected 5 cached, got {n}"
        assert m.call_count == 1, f"expected 1 batch call, got {m.call_count}"
        # All 5 queries should now be cache-resident
        for q in ("q1", "q2", "q3", "q4", "q5"):
            assert q in _EMBED_CACHE, f"q={q!r} missing from cache"
    print("  ✓ test_preload_batches_once")


def test_embed_query_uses_cache():
    _reset_embed_cache()
    _EMBED_CACHE["cached_query"] = FAKE_VEC
    with patch("app.vertex_client.embed_text") as m:
        v = _embed_query("cached_query")
        assert v == FAKE_VEC
        assert m.call_count == 0, "embed_text should NOT have been called for cached query"
    print("  ✓ test_embed_query_uses_cache")


def test_embed_query_falls_through_on_miss():
    _reset_embed_cache()
    with patch("app.vertex_client.embed_text", side_effect=_fake_embed_single) as m:
        v = _embed_query("uncached_query")
        assert v == FAKE_VEC
        assert m.call_count == 1
        # After the call, query is now cached
        assert "uncached_query" in _EMBED_CACHE
        # Second call: cache hit
        v2 = _embed_query("uncached_query")
        assert v2 == FAKE_VEC
        assert m.call_count == 1, "second call should hit cache, not embed_text"
    print("  ✓ test_embed_query_falls_through_on_miss")


def test_reset_clears_cache():
    _EMBED_CACHE["x"] = FAKE_VEC
    _EMBED_CACHE["y"] = FAKE_VEC
    _reset_embed_cache()
    assert len(_EMBED_CACHE) == 0
    print("  ✓ test_reset_clears_cache")


def test_preload_skips_already_cached():
    _reset_embed_cache()
    _EMBED_CACHE["already_here"] = FAKE_VEC
    with patch("app.vertex_client.embed_texts_batch", side_effect=_fake_embed_batch) as m:
        n = _preload_embeddings(["already_here", "new1", "new2"])
        assert n == 2, f"expected 2 new cached (new1, new2); already_here skipped"
        # The batch call should only include the uncached queries
        called_with = m.call_args.args[0]
        assert "already_here" not in called_with
        assert "new1" in called_with and "new2" in called_with
    print("  ✓ test_preload_skips_already_cached")


def test_preload_recovery_on_batch_failure():
    _reset_embed_cache()
    with patch("app.vertex_client.embed_texts_batch",
               side_effect=RuntimeError("API saturated")) as m:
        n = _preload_embeddings(["a", "b", "c"])
        assert n == 0, "batch failure should return 0 cached"
        assert len(_EMBED_CACHE) == 0, "no queries cached on batch failure"
    # Subsequent _embed_query calls fall through to per-call path
    with patch("app.vertex_client.embed_text", side_effect=_fake_embed_single) as m:
        v = _embed_query("a")
        assert v == FAKE_VEC
        assert m.call_count == 1
    print("  ✓ test_preload_recovery_on_batch_failure")


def main():
    print("R9.1 — embedding pre-cache unit tests")
    test_preload_batches_once()
    test_embed_query_uses_cache()
    test_embed_query_falls_through_on_miss()
    test_reset_clears_cache()
    test_preload_skips_already_cached()
    test_preload_recovery_on_batch_failure()
    print("\n=== ALL TESTS PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
