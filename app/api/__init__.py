"""The routing layer: every HTTP-facing route in the app lives under app/api/, one module per
domain (chat.py, orchestrator.py, auth.py, uploads.py, admin.py, health.py). Each module exports
a `router: APIRouter` with route declarations only - request validation, calling into the
relevant domain package (app/chat/, app/agents/orchestrator_agent/, app/auth/, app/uploads/,
app/agents/adc_inspection_agent/) for the actual business logic, and shaping the response.

app/main.py is the single place that assembles the app: creates it, adds middleware, and
`include_router()`s each module here - read it to see every API surface the app exposes, without
needing to know where each domain's logic happens to live. Adding a new endpoint means adding (or
extending) a module here and writing its logic in the appropriate domain package, not touching
app/main.py's own route count.
"""
