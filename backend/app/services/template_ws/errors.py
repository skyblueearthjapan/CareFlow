"""型の作業場の、利用者に日本語で見せて終わるエラー。"""

from __future__ import annotations


class TemplateError(Exception):
    """利用者に日本語で見せて終わるエラー（API では 422）。"""
