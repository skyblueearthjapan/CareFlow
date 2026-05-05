# W1-E Diff Engine 移植 — Codex Final Review

**Reviewer**: OpenAI Codex CLI v0.128.0 (gpt-5.5)
**Date**: 2026-05-05
**Scope**: Post-revise final QA review

---

**VERDICT:** Request changes. The revise commit is not ready.

**Summary:** `max_length=10_000_000` is added to both CSV fields, and `@limiter.limit("5/minute")` plus `Request` is added to `/api/v1/diff/compute`. But the claimed tempfile-to-`io.StringIO` refactor is incomplete: [engine.py](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/backend/app/services/diff/engine.py:842) and [engine.py](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/backend/app/services/diff/engine.py:882) still call `tempfile.NamedTemporaryFile`, and [engine.py](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/backend/app/services/diff/engine.py:853) / [engine.py](C:/Users/imaizumi.LINEWORKS-NET/Documents/CareLink/backend/app/services/diff/engine.py:903) still call `os.unlink`. The revise removed the `os` and `tempfile` imports, so the API path will raise `NameError` before diffing.

**Residual issues:** The new row helpers preserve behavior for the narrow `parse_optimized_csv()` Kaipoke-format delegation: both old and new paths consume the same `csv.reader` rows from `read_csv_auto_encoding()`, then apply the same header skip, `len(row) >= 18`, stripping, and field mapping. But this does not confirm endpoint behavior, because `compare_schedules_from_content()` was not refactored to those helpers.

**Performance + memory:** The 10M limit is per JSON string character, not a true 10MB byte limit. FastAPI/Pydantic must still receive and parse the full body before validation. At max size, two CSV strings, parsed row lists, `ScheduleEntry` objects, response edits, and summary all coexist. If fixed to `StringIO`, disk I/O improves, but row materialization stays O(file size). Matching also has potentially quadratic passes within user/date groups, so very large same-user schedules can still spike CPU.

**Security:** Auth and per-IP rate limiting are now present. No obvious SQL/shell injection path in the endpoint. CSV/formula injection remains relevant if returned corrections are later exported/opened in spreadsheets; user-controlled fields starting `=`, `+`, `-`, or `@` should be escaped at export time. Encoding is still a product constraint: JSON payloads are already decoded Unicode, so cp932/Shift-JIS auto-detection only applies to path-based file reads, not this API. BOM stripping needs explicit tests.

**What’s still missing:** Add regression tests for the broken content path, oversized payload 422, 6th request 429, BOM-prefixed payloads, and parse parity between tempfile and row/StringIO paths. I could not run pytest because the sandbox policy rejected the command.
