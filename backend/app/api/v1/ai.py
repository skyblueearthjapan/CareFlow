"""AI interpret endpoints — D4 Phase E / Wave 4-B.

Implements `POST /api/v1/ai/interpret` (Gemini-backed natural-language
parser for the FAB modal) and `GET /api/v1/ai/logs` (admin/manager-only
audit-log listing). Every successful or failed Gemini call is persisted to
`ai_interpret_logs` so we can replay / investigate later.

The `context_type` and approximate `cost_usd` returned by the client live
inside the JSONB `response` column under the `_meta` wrapper key — see
`app/schemas/ai.py` for the rationale.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select

from app.core.deps import DbDep, require_role
from app.models.ai_interpret_log import AiInterpretLog
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.user import User
from app.schemas._pagination import Paginated
from app.schemas.ai import AiLogRead, InterpretRequest, InterpretResponse
from app.services.gemini_client import (
    GeminiAPIKeyMissing,
    GeminiClient,
    GeminiError,
    GeminiInvalidResponseError,
    GeminiModelNotFound,
    GeminiQuotaExceeded,
    GeminiRateLimitError,
    GeminiUnavailableError,
    InterpretResult,
    get_gemini_client,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers


_WEEKDAY_JA = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]


async def _build_default_context(db, supplied: dict[str, Any]) -> dict[str, Any]:
    """Auto-fill today / iso_week / staff_list / patient_list when missing.

    Caller-supplied values always win — the route handler can pre-pin a
    specific iso_week (e.g. when the user is browsing a past week) and we
    won't overwrite it.
    """
    ctx = dict(supplied) if supplied else {}
    today = date.today()
    ctx.setdefault("today", today.isoformat())
    iso = today.isocalendar()
    ctx.setdefault("iso_week", f"{iso.week:02d}")
    ctx.setdefault("weekday", _WEEKDAY_JA[today.weekday()])

    if "staff_list" not in ctx:
        # Cap at 200 active staff to keep the prompt small.
        result = await db.execute(
            select(Staff.code, Staff.name)
            .where(Staff.deleted_at.is_(None), Staff.status == "active")
            .order_by(Staff.name)
            .limit(200)
        )
        ctx["staff_list"] = [
            f"{name} ({code or '-'})" for code, name in result.all()
        ]
    if "patient_list" not in ctx:
        result = await db.execute(
            select(Patient.code, Patient.name)
            .where(Patient.deleted_at.is_(None))
            .order_by(Patient.name)
            .limit(200)
        )
        ctx["patient_list"] = [
            f"{name} ({code or '-'})" for code, name in result.all()
        ]
    return ctx


async def _persist_log(
    db,
    *,
    user_id: UUID | None,
    rendered_prompt: str,
    user_text: str,
    context_type: str,
    interpreted: dict[str, Any] | None,
    raw_response: str | None,
    model: str,
    latency_ms: int,
    cost_usd: float,
    confidence: float | None,
    error: str | None = None,
    error_type: str | None = None,
) -> AiInterpretLog:
    """Insert a single AiInterpretLog row.

    `response` packs both the structured Gemini payload and a `_meta`
    wrapper carrying context_type / cost / raw — the table has no dedicated
    columns for those today (see schemas/ai.py rationale). `error_type`
    records the Python exception class name (e.g. "GeminiModelNotFound")
    so operators can grep for misconfiguration without parsing the
    free-form `error` string.
    """
    response_payload: dict[str, Any] = {
        "interpreted": interpreted or {},
        "_meta": {
            "context_type": context_type,
            "cost_usd": cost_usd,
            "confidence": confidence,
            "raw_response": raw_response,
            "user_text": user_text,
            "error": error,
            "error_type": error_type,
        },
    }
    log = AiInterpretLog(
        prompt=rendered_prompt,
        response=response_payload,
        model=model,
        latency_ms=latency_ms,
        user_id=user_id,
    )
    db.add(log)
    await db.flush()
    await db.commit()
    await db.refresh(log)
    return log


def _to_log_read(log: AiInterpretLog) -> AiLogRead:
    """Project an ORM row into the enriched read schema."""
    meta = (log.response or {}).get("_meta", {}) if isinstance(log.response, dict) else {}
    return AiLogRead(
        id=log.id,
        prompt=log.prompt,
        response=log.response or {},
        model=log.model,
        latency_ms=log.latency_ms,
        user_id=log.user_id,
        created_at=log.created_at,
        updated_at=log.updated_at,
        context_type=meta.get("context_type") if isinstance(meta, dict) else None,
        cost_usd=meta.get("cost_usd") if isinstance(meta, dict) else None,
        confidence=meta.get("confidence") if isinstance(meta, dict) else None,
    )


# ---------------------------------------------------------------------------
# POST /ai/interpret


@router.post(
    "/interpret",
    response_model=InterpretResponse,
    summary="自然言語入力を Gemini で構造化 JSON に変換",
)
async def interpret(
    payload: InterpretRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
    client: Annotated[GeminiClient, Depends(get_gemini_client)],
) -> InterpretResponse:
    context = await _build_default_context(db, payload.context)

    try:
        result: InterpretResult = client.interpret(
            prompt=payload.prompt,
            context_type=payload.context_type,
            context=context,
        )
    except GeminiModelNotFound as exc:
        # 404 from upstream — the configured model id is retired or
        # never existed on this API version. Operator action required.
        await _persist_log(
            db,
            user_id=user.id,
            rendered_prompt=payload.prompt,
            user_text=payload.prompt,
            context_type=payload.context_type,
            interpreted=None,
            raw_response=None,
            model=client.model,
            latency_ms=0,
            cost_usd=0.0,
            confidence=None,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Configured Gemini model {client.model!r} is not available — "
                "contact admin to update the GEMINI_MODEL env var "
                "(e.g. gemini-2.0-flash, gemini-2.5-flash)."
            ),
        ) from exc
    except GeminiAPIKeyMissing as exc:
        # No API key configured → 503 (configured-out).
        await _persist_log(
            db,
            user_id=user.id,
            rendered_prompt=payload.prompt,
            user_text=payload.prompt,
            context_type=payload.context_type,
            interpreted=None,
            raw_response=None,
            model="(unavailable)",
            latency_ms=0,
            cost_usd=0.0,
            confidence=None,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Gemini API is not available: {exc}",
        ) from exc
    except GeminiUnavailableError as exc:
        # SDK missing or other availability failure → 503.
        await _persist_log(
            db,
            user_id=user.id,
            rendered_prompt=payload.prompt,
            user_text=payload.prompt,
            context_type=payload.context_type,
            interpreted=None,
            raw_response=None,
            model="(unavailable)",
            latency_ms=0,
            cost_usd=0.0,
            confidence=None,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Gemini API is not available: {exc}",
        ) from exc
    except GeminiRateLimitError as exc:
        # Covers GeminiQuotaExceeded too (subclass).
        await _persist_log(
            db,
            user_id=user.id,
            rendered_prompt=payload.prompt,
            user_text=payload.prompt,
            context_type=payload.context_type,
            interpreted=None,
            raw_response=None,
            model=client.model,
            latency_ms=0,
            cost_usd=0.0,
            confidence=None,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Gemini API rate-limited / quota exhausted",
            headers={"Retry-After": "60"},
        ) from exc
    except GeminiInvalidResponseError as exc:
        await _persist_log(
            db,
            user_id=user.id,
            rendered_prompt=payload.prompt,
            user_text=payload.prompt,
            context_type=payload.context_type,
            interpreted=None,
            raw_response=str(exc)[:2000],
            model=client.model,
            latency_ms=0,
            cost_usd=0.0,
            confidence=None,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Gemini returned an unparseable response: {exc}",
        ) from exc
    except GeminiError as exc:
        # Generic upstream failure (network, 5xx, parse) → 502.
        await _persist_log(
            db,
            user_id=user.id,
            rendered_prompt=payload.prompt,
            user_text=payload.prompt,
            context_type=payload.context_type,
            interpreted=None,
            raw_response=None,
            model=client.model,
            latency_ms=0,
            cost_usd=0.0,
            confidence=None,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gemini upstream call failed: {exc}",
        ) from exc

    log = await _persist_log(
        db,
        user_id=user.id,
        rendered_prompt=result.extra.get("rendered_prompt") or payload.prompt,
        user_text=payload.prompt,
        context_type=payload.context_type,
        interpreted=result.interpreted,
        raw_response=result.raw_response,
        model=result.model,
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
        confidence=result.confidence,
    )

    return InterpretResponse(
        interpreted=result.interpreted,
        confidence=result.confidence,
        raw_response=result.raw_response,
        log_id=log.id,
        model=result.model,
        latency_ms=result.latency_ms,
        cost_usd=result.cost_usd,
        context_type=payload.context_type,
    )


# ---------------------------------------------------------------------------
# GET /ai/logs (admin / manager only)


@router.get(
    "/logs",
    response_model=Paginated[AiLogRead],
    summary="List AI interpret logs (admin / manager)",
)
async def list_logs(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    since: Annotated[datetime | None, Query(description="created_at >= since")] = None,
    until: Annotated[datetime | None, Query(description="created_at <  until")] = None,
    model: Annotated[str | None, Query(description="Exact model id filter")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[AiLogRead]:
    conditions = []
    if since is not None:
        conditions.append(AiInterpretLog.created_at >= since)
    if until is not None:
        conditions.append(AiInterpretLog.created_at < until)
    if model is not None:
        conditions.append(AiInterpretLog.model == model)

    base = select(AiInterpretLog)
    count_stmt = select(func.count()).select_from(AiInterpretLog)
    if conditions:
        base = base.where(and_(*conditions))
        count_stmt = count_stmt.where(and_(*conditions))

    rows = (
        await db.scalars(
            base.order_by(AiInterpretLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    total = (await db.scalar(count_stmt)) or 0
    return Paginated[AiLogRead](
        items=[_to_log_read(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )
