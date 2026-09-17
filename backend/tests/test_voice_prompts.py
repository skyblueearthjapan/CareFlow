"""Tests for the voice transcription/summary prompt and response schema."""

from __future__ import annotations

import json

from app.services.voice import prompts


def test_prompt_version_is_v1() -> None:
    assert prompts.PROMPT_VERSION == "v1"


def test_nursing_terms_cover_the_initial_dictionary() -> None:
    assert len(prompts.NURSING_TERMS) >= 30
    for term in ("褥瘡", "包交", "バイタル", "SpO2", "喀痰", "訪問看護指示書", "ケアマネ", "次回"):
        assert term in prompts.NURSING_TERMS


def test_prompt_contains_terms_and_context() -> None:
    text = prompts.build_transcribe_summary_prompt(
        patient_name="山田 太郎", staff_name="佐藤 花子", visit_date="2026-09-17"
    )
    # 用語辞書が全語入っている。
    for term in prompts.NURSING_TERMS:
        assert term in text
    # 文脈 (患者・担当・日付)。
    assert "山田 太郎" in text
    assert "佐藤 花子" in text
    assert "2026-09-17" in text
    # 規約。
    assert "推測" in text
    assert "［不明］" in text
    assert "JSON のみ" in text
    for label in prompts.SPEAKER_LABELS:
        assert f"{label}:" in text
    # 要約の見出し。
    for section in ("主訴・様子", "バイタル", "処置・ケア", "申し送り", "次回"):
        assert section in text


def test_prompt_without_context_says_do_not_guess() -> None:
    text = prompts.build_transcribe_summary_prompt(None, None, None)
    assert "山田" not in text
    assert "推測して補わない" in text


def test_response_schema_is_valid_json_schema_shape() -> None:
    schema = prompts.response_schema()
    # そのまま JSON にできる (Vertex の responseSchema へ載せられる)。
    assert json.loads(json.dumps(schema)) == schema

    assert schema["type"] == "object"
    assert set(schema["required"]) == {"transcript", "transcript_segments", "summary"}

    props = schema["properties"]
    assert props["transcript"]["type"] == "string"

    seg = props["transcript_segments"]
    assert seg["type"] == "array"
    seg_props = seg["items"]["properties"]
    assert seg_props["speaker"]["enum"] == list(prompts.SPEAKER_LABELS)
    assert seg_props["start_sec"]["type"] == "number"
    assert seg_props["start_sec"]["nullable"] is True
    assert set(seg["items"]["required"]) == {"speaker", "text"}

    summary = props["summary"]
    assert set(summary["required"]) == set(prompts.SUMMARY_SECTIONS)
    assert summary["properties"]["主訴・様子"]["type"] == "array"
    vitals = summary["properties"]["バイタル"]
    assert set(vitals["properties"]) == set(prompts.VITAL_KEYS)
    assert summary["properties"]["次回"]["type"] == "string"
    assert summary["properties"]["free"]["type"] == "string"


def test_response_schema_types_are_known_keywords() -> None:
    allowed = {"object", "array", "string", "number", "integer", "boolean"}

    def walk(node: dict) -> None:
        assert node["type"] in allowed
        for child in (node.get("properties") or {}).values():
            walk(child)
        if node.get("items"):
            walk(node["items"])

    walk(prompts.response_schema())
