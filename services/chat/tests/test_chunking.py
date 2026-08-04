from chat.rag.chunking import ChunkedText, chunk_content


def test_short_content_is_a_single_chunk() -> None:
    text = "Visiting hours are Monday to Friday, 8am to 5pm."

    chunks = chunk_content(text)

    assert chunks == [ChunkedText(chunk_index=0, chunk_text=text)]


def test_long_content_is_split_into_overlapping_indexed_chunks() -> None:
    content = "".join(chr(ord("a") + (i % 26)) for i in range(2500))

    chunks = chunk_content(content)

    assert len(chunks) > 1
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].chunk_text[-50:] in chunks[1].chunk_text


def test_at_least_one_chunk_survives_degenerate_filtering() -> None:
    # A chunk boundary can isolate a meaningless divider (FR-017); real content must
    # still survive as its own chunk(s).
    content = "Real content here.\n\n" + ("-" * 2000) + "\n\nMore real content."

    chunks = chunk_content(content)

    assert len(chunks) >= 1
    assert any("Real content" in c.chunk_text for c in chunks)
