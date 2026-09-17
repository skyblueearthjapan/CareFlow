"""Prompt template and response schema for visit voice transcription + summary.

設計書 `docs/plans/visit-voice-record-design-2026-09-17.md` §2-5 / §10-4。

1 コールで「文字起こし」と「看護記録の要約」を同時に得る。出力は
`responseMimeType=application/json` ＋ `responseSchema` で JSON に固定する。

`PROMPT_VERSION` は録音レコードに保存し、テンプレ変更時に再生成できるようにする。
テンプレ本文・用語辞書・スキーマのいずれかを変えたら必ず上げること。
"""

from __future__ import annotations

from typing import Any

PROMPT_VERSION = "v1"

#: 看護用語辞書 (初版 30 語)。誤変換を抑えるためにプロンプトへ列挙する。
NURSING_TERMS: tuple[str, ...] = (
    "褥瘡",
    "褥瘡処置",
    "包交",
    "創部",
    "バイタル",
    "血圧",
    "体温",
    "脈拍",
    "SpO2",
    "血糖",
    "インスリン",
    "服薬",
    "排泄",
    "浮腫",
    "喀痰",
    "吸引",
    "経管栄養",
    "疼痛",
    "呼吸苦",
    "食欲",
    "水分",
    "転倒",
    "認知",
    "見当識",
    "不眠",
    "便秘",
    "下痢",
    "発熱",
    "清拭",
    "陰洗",
    "訪問看護指示書",
    "主治医",
    "ケアマネ",
    "家族",
    "次回",
)

#: 要約の見出し (render_summary_text と response_schema で共有)。
SUMMARY_SECTIONS: tuple[str, ...] = (
    "主訴・様子",
    "バイタル",
    "処置・ケア",
    "申し送り",
    "次回",
    "free",
)

#: バイタルの項目。
VITAL_KEYS: tuple[str, ...] = ("血圧", "体温", "脈拍", "SpO2", "血糖")

#: 話者ラベル。
SPEAKER_LABELS: tuple[str, ...] = ("看護師", "患者", "家族", "不明")

_UNKNOWN = "［不明］"


def _context_lines(patient_name: str | None, staff_name: str | None, visit_date: str | None) -> str:
    lines: list[str] = []
    if patient_name:
        lines.append(f"- 患者（利用者）: {patient_name}")
    if staff_name:
        lines.append(f"- 訪問した看護師: {staff_name}")
    if visit_date:
        lines.append(f"- 訪問日: {visit_date}")
    if not lines:
        return "- （患者・担当者・訪問日は不明。音声から推測して補わないこと）"
    return "\n".join(lines)


def build_transcribe_summary_prompt(
    patient_name: str | None = None,
    staff_name: str | None = None,
    visit_date: str | None = None,
) -> str:
    """Build the single-call transcription + nursing-summary prompt."""
    terms = "・".join(NURSING_TERMS)
    speakers = "／".join(f"{label}:" for label in SPEAKER_LABELS)
    context = _context_lines(patient_name, staff_name, visit_date)

    return f"""あなたは訪問看護の記録を補助するアシスタントです。
添付の音声は、日本の訪問看護師が利用者宅で行った訪問の録音です。
音声を文字起こしし、看護記録用に要約してください。

# 文脈
{context}

# 絶対に守るルール
- 音声に無いことを推測・創作しない。分からないことは書かない。
- 聞き取れない箇所は {_UNKNOWN} と書く（前後から補わない）。
- 数値（血圧・体温・脈拍・SpO2・血糖・服薬量など）は音声どおりに写す。丸めない・単位を足さない。
- 人名は音声で呼ばれているとおりの呼称のまま書く（フルネームに直さない・敬称を足さない）。
- 話者ラベルは {speakers} の 4 種類だけを使う。判断できなければ 不明: とする。
- 出力は JSON のみ。前置き・説明・コードフェンス（```）を付けない。

# 用語のヒント（訪問看護でよく使う語。音が近ければこの表記を優先する）
{terms}

# 出力する JSON の形
{{
  "transcript": "話者ラベル付きの全文。1 発話 1 行。句読点を付けて読みやすくする。例) 看護師: 今日の体調はいかがですか。",
  "transcript_segments": [
    {{"speaker": "看護師", "start_sec": 0, "text": "今日の体調はいかがですか。"}}
  ],
  "summary": {{
    "主訴・様子": ["利用者の訴え・全身状態・生活の様子を短い箇条書きで"],
    "バイタル": {{"血圧": "", "体温": "", "脈拍": "", "SpO2": "", "血糖": ""}},
    "処置・ケア": ["実施した処置・ケアを箇条書きで"],
    "申し送り": ["次の担当者・主治医・ケアマネに伝えるべきことを箇条書きで"],
    "次回": "次回訪問の予定や約束（音声に無ければ空文字）",
    "free": "上のどれにも入らない補足（無ければ空文字）"
  }}
}}

# 補足
- `transcript_segments[].start_sec` は分かる場合のみ秒数（数値）。分からなければ null。
- `summary` の配列は該当が無ければ空配列 []、文字列は空文字 "" にする。
- バイタルは測定値が語られた項目だけ埋める。語られていない項目は空文字にする（{_UNKNOWN} を入れない）。
"""


def response_schema() -> dict[str, Any]:
    """JSON Schema for Gemini `generationConfig.responseSchema`."""
    return {
        "type": "object",
        "properties": {
            "transcript": {"type": "string"},
            "transcript_segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "speaker": {"type": "string", "enum": list(SPEAKER_LABELS)},
                        "start_sec": {"type": "number", "nullable": True},
                        "text": {"type": "string"},
                    },
                    "required": ["speaker", "text"],
                },
            },
            "summary": {
                "type": "object",
                "properties": {
                    "主訴・様子": {"type": "array", "items": {"type": "string"}},
                    "バイタル": {
                        "type": "object",
                        "properties": {key: {"type": "string"} for key in VITAL_KEYS},
                        "required": list(VITAL_KEYS),
                    },
                    "処置・ケア": {"type": "array", "items": {"type": "string"}},
                    "申し送り": {"type": "array", "items": {"type": "string"}},
                    "次回": {"type": "string"},
                    "free": {"type": "string"},
                },
                "required": list(SUMMARY_SECTIONS),
            },
        },
        "required": ["transcript", "transcript_segments", "summary"],
    }
