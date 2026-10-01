from chat.rag.chunking import ChunkedText, chunk_content
from chat.rag.default_corpus import DEFAULT_FAQ_ENTRIES


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


# --- heading sections ----------------------------------------------------------------


def test_a_headed_entry_is_one_chunk_per_section_under_its_heading_path() -> None:
    content = "# Guide\n\n## Insurance\n\nWe accept plan A.\n\n## Medicines\n\nTell us."

    chunks = chunk_content(content)

    assert [c.chunk_text for c in chunks] == [
        "# Guide\n## Insurance\n\nWe accept plan A.",
        "# Guide\n## Medicines\n\nTell us.",
    ]


def test_a_heading_with_no_text_of_its_own_is_never_a_chunk() -> None:
    # A title followed straight by a subheading is part of the path, not a chunk - the
    # old window could end a chunk on a bare heading.
    chunks = chunk_content("# Guide\n\n## Only section\n\nBody.")

    assert [c.chunk_text for c in chunks] == ["# Guide\n## Only section\n\nBody."]


def test_text_before_the_first_heading_is_a_chunk_with_no_header() -> None:
    chunks = chunk_content("Intro line.\n\n# Section\n\nBody.")

    assert [c.chunk_text for c in chunks] == ["Intro line.", "# Section\n\nBody."]


def test_a_shallower_heading_closes_the_deeper_sections_above_it() -> None:
    content = "# G\n## A\n### A1\n\nDeep.\n\n## B\n\nShallow."

    chunks = chunk_content(content)

    assert [c.chunk_text for c in chunks] == [
        "# G\n## A\n### A1\n\nDeep.",
        "# G\n## B\n\nShallow.",
    ]


def test_a_long_section_splits_into_pieces_that_each_carry_its_header() -> None:
    paragraphs = [f"Paragraph {i} " + "word " * 60 for i in range(8)]
    content = "# Guide\n\n## Long\n\n" + "\n\n".join(paragraphs)

    chunks = chunk_content(content)

    assert len(chunks) > 1
    assert all(c.chunk_text.startswith("# Guide\n## Long\n\n") for c in chunks)
    assert all(len(c.chunk_text) <= 1000 for c in chunks)
    joined = "\n".join(c.chunk_text for c in chunks)
    assert all(f"Paragraph {i} " in joined for i in range(8))


def test_a_meaningless_section_is_dropped_though_it_has_a_heading() -> None:
    chunks = chunk_content("# G\n\n## Divider\n\n-----\n\n## Real\n\nText.")

    assert [c.chunk_text for c in chunks] == ["# G\n## Real\n\nText."]
    assert [c.chunk_index for c in chunks] == [0]


def test_a_hash_without_a_space_is_not_a_heading() -> None:
    text = "#1 priority is your health.\nCall us."

    assert chunk_content(text) == [ChunkedText(chunk_index=0, chunk_text=text)]


def test_every_question_and_answer_entry_is_still_its_own_single_chunk() -> None:
    # Headless entries are chunked exactly as before - the nine the golden set's
    # original labels rest on among them.
    headless = [e for e in DEFAULT_FAQ_ENTRIES if not e.startswith("#")]

    assert headless
    for entry in headless:
        assert chunk_content(entry) == [ChunkedText(chunk_index=0, chunk_text=entry)]


def test_every_chunk_of_a_starter_document_names_its_document() -> None:
    for entry in DEFAULT_FAQ_ENTRIES:
        if not entry.startswith("# "):
            continue
        title = entry.splitlines()[0]
        assert all(c.chunk_text.startswith(title) for c in chunk_content(entry))
