# API CHANGELOG

## 2026-05-05

- PATIENT API contract change: `weekly_pattern` and `special_week` now expect `dict | null` (previously `string | boolean` were silently ignored).
- External clients sending legacy types will receive 422.
