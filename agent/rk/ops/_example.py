"""見本の操作「noop」（テストと形の確認用）。読むだけの GET /auth/me を呼ぶので本番でも何も変わらない。"""

from rk.opsbase import Op, register


@register
class Noop(Op):
    name = "noop"
    summary = "何もしない（見本）"

    def validate(self, op):
        return [] if op.get("label") else ["label がありません"]

    def request(self, op):
        return ("GET", "/auth/me", None)

    def snapshot(self, api, op):
        return {"note": op.get("note")}

    def inverse(self, op, before):
        return [{"op": "noop", "label": f"元に戻す: {op.get('label', '')}", "note": before.get("note")}]

    def verify(self, api, op):
        return []
