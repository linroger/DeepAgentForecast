"""Tests for file_parser.split_text_into_chunks defaults (CHUNK-1)."""

import inspect

from app.utils.file_parser import split_text_into_chunks


def test_default_chunk_size_and_overlap_match_config_spec():
    """Defaults must match Config.DEFAULT_CHUNK_SIZE=2500 / OVERLAP=250 (CHUNK-1)."""
    sig = inspect.signature(split_text_into_chunks)
    assert sig.parameters["chunk_size"].default == 2500
    assert sig.parameters["overlap"].default == 250


def test_short_text_returns_single_chunk():
    text = "短文本，不需要切分。"
    assert split_text_into_chunks(text) == [text]


def test_empty_text_returns_empty_list():
    assert split_text_into_chunks("") == []
    assert split_text_into_chunks("   ") == []


def test_long_text_uses_2500_window():
    # ~6000 chars of sentences -> with a 2500 window we expect far fewer chunks
    # than the old 500 window would have produced (~12).
    sentence = "This is a fact about an actor and a relationship. "
    text = sentence * 130  # ~6500 chars
    chunks = split_text_into_chunks(text)
    assert len(chunks) >= 2
    # 5x larger window => roughly a 5x reduction in episode count vs chunk_size=500.
    chunks_small = split_text_into_chunks(text, chunk_size=500, overlap=50)
    assert len(chunks) < len(chunks_small)
    # Every chunk respects the window bound (allow slack for sentence-boundary seek).
    assert all(len(c) <= 2500 for c in chunks)


def test_explicit_args_still_honored():
    text = "abc. " * 400
    chunks = split_text_into_chunks(text, chunk_size=300, overlap=30)
    assert len(chunks) > 1
    assert all(len(c) <= 300 for c in chunks)
