"""Offline tests for smart output de-duplication."""
import json

from app.tools.dedup import dedup_text


def test_dedup_json_array_objects():
    text = json.dumps([
        {"name": "a", "score": 1},
        {"score": 1, "name": "a"},   # same object, different key order -> dup
        {"name": "b", "score": 2},
    ], ensure_ascii=False)
    r = dedup_text(text)
    assert r["format"] == "json"
    assert r["original_count"] == 3
    assert r["deduped_count"] == 2
    assert r["removed"] == 1
    out = json.loads(r["result"])
    assert out == [{"name": "a", "score": 1}, {"name": "b", "score": 2}]


def test_dedup_json_scalars():
    r = dedup_text('[1, 2, 2, "x", "x", 3]')
    assert r["deduped_count"] == 4
    assert json.loads(r["result"]) == [1, 2, "x", 3]


def test_dedup_markdown_list_prefixes():
    text = "- apple\n- Apple\n* banana\n1. apple\n- cherry"
    r = dedup_text(text)
    assert r["format"] == "lines"
    # apple/Apple/1. apple all collapse; banana, cherry stay -> 3 kept
    assert r["deduped_count"] == 3
    assert "banana" in r["result"] and "cherry" in r["result"]
    # original bullets preserved for the first occurrence
    assert r["result"].splitlines()[0] == "- apple"


def test_dedup_plain_lines_preserve_blanks():
    text = "foo\n\nfoo\nbar"
    r = dedup_text(text)
    assert r["deduped_count"] == 2     # foo, bar
    assert "\n\n" in r["result"]       # blank line kept


def test_dedup_no_duplicates():
    r = dedup_text("alpha\nbeta\ngamma")
    assert r["removed"] == 0
    assert r["deduped_count"] == 3
