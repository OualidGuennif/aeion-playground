"""FastAPI entrypoint for the real Æion ParcelDesk Mission Control POC."""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
import time
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import settings
from .evidence import EvidenceWriter
from .parceldesk import parceldesk_store
from .pricing import quote_or_error
from .real_engine import VERSION, engine
from .store import store

# The warehouse board reads live mission state, but its open-ticket count comes
# from the ParcelDesk store. Close a linked ticket when its mission is terminal.
engine.ticket_store = parceldesk_store

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
ASSET_VERSION = str(int(time.time()))


@asynccontextmanager
async def lifespan(_: FastAPI):
    engine.start()
    yield
    engine.stop()


app = FastAPI(
    title="Æion Mission Control — ParcelDesk POC",
    version=VERSION,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "cockpit", "host.docker.internal"])


@app.middleware("http")
async def same_origin_mutations(request: Request, call_next):
    """Prevent a foreign webpage from launching tasks or approving checkpoints."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if request.headers.get("sec-fetch-site") == "cross-site" or origin is not None and origin != expected:
            return JSONResponse({"ok": False, "error": "Cross-origin API mutation refused."}, status_code=403)
    return await call_next(request)


def page(request: Request, name: str, active: str, title: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        name,
        {
            "request": request,
            "snapshot": engine.snapshot(),
            "active": active,
            "title": title,
            "asset_version": ASSET_VERSION,
        },
    )


@app.get("/", response_class=HTMLResponse)
def mission_control(request: Request) -> HTMLResponse:
    return page(request, "mission_control.html", "mission_control", "Mission Control")


@app.get("/paperclip", response_class=HTMLResponse)
def paperclip_page(request: Request) -> HTMLResponse:
    return page(request, "paperclip.html", "paperclip", "Paperclip Workboard")


@app.get("/parceldesk", response_class=HTMLResponse)
def parceldesk(request: Request) -> HTMLResponse:
    return page(request, "parceldesk.html", "parceldesk", "ParcelDesk")


@app.get("/warehouse", response_class=HTMLResponse)
def warehouse(request: Request) -> HTMLResponse:
    return page(request, "warehouse.html", "parceldesk", "ParcelDesk Warehouse")


@app.get("/observability", response_class=HTMLResponse)
def observability(request: Request) -> HTMLResponse:
    return page(request, "observability.html", "observability", "Observability")


@app.get("/context", response_class=HTMLResponse)
def context(request: Request) -> HTMLResponse:
    return page(request, "context.html", "context", "Memory & Graph")


@app.get("/ops", response_class=HTMLResponse)
def ops(request: Request) -> HTMLResponse:
    return page(request, "ops.html", "ops", "OPS & CROA")


@app.get("/frameworks", response_class=HTMLResponse)
def frameworks(request: Request) -> HTMLResponse:
    return page(request, "frameworks.html", "frameworks", "Frameworks")


@app.get("/api/snapshot")
def api_snapshot() -> JSONResponse:
    return JSONResponse(engine.snapshot())


@app.get("/api/health")
def api_health() -> JSONResponse:
    return JSONResponse(engine.health())


def _decorate_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    mission = store.mission(ticket["mission_id"]) if ticket.get("mission_id") else None
    brief = None
    if mission:
        brief = {key: mission.get(key) for key in (
            "id", "status", "current_step", "progress", "gate_reason_code", "gate_reason", "outcome",
        )}
    return {**ticket, "mission": brief}


@app.get("/api/parceldesk/summary")
def api_parceldesk_summary() -> JSONResponse:
    return JSONResponse({"ok": True, **parceldesk_store.summary()})


@app.get("/api/parceldesk/orders")
def api_parceldesk_orders() -> JSONResponse:
    return JSONResponse({"ok": True, "orders": parceldesk_store.orders()})


@app.post("/api/parceldesk/orders/{order_id}/refund")
def api_parceldesk_refund(order_id: str, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    try:
        request = RefundRequest(**payload)
        refund = parceldesk_store.refund(order_id, request.reason)
    except ValidationError as error:
        return JSONResponse({"ok": False, "error": "Invalid refund: " + error.errors()[0]["msg"]}, status_code=422)
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=422)
    return JSONResponse({"ok": True, "refund": refund, "orders": parceldesk_store.orders(),
                         "summary": parceldesk_store.summary()})


@app.get("/api/parceldesk/tickets")
def api_parceldesk_tickets() -> JSONResponse:
    return JSONResponse({"ok": True, "tickets": [_decorate_ticket(ticket) for ticket in parceldesk_store.tickets()]})


@app.post("/api/parceldesk/tickets")
def api_parceldesk_ticket(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    try:
        data = TicketRequest(**payload).model_dump()
    except ValidationError as error:
        return JSONResponse({"ok": False, "error": "Invalid ticket: " + error.errors()[0]["msg"]}, status_code=422)
    try:
        ticket = parceldesk_store.create_ticket(**data)
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=422)
    try:
        mission = engine.launch_ticket(request_id=f"ticket-{ticket['id']}", **data)
    except ValueError as error:
        ticket = parceldesk_store.fail_ticket(ticket["id"], str(error))
        return JSONResponse({"ok": False, "ticket": _decorate_ticket(ticket), "error": str(error)}, status_code=422)
    except RuntimeError as error:
        ticket = parceldesk_store.fail_ticket(ticket["id"], str(error))
        return JSONResponse({"ok": False, "ticket": _decorate_ticket(ticket), "error": str(error)}, status_code=409)
    ticket = parceldesk_store.link_ticket(ticket["id"], mission["id"])
    return JSONResponse({"ok": True, "ticket": _decorate_ticket(ticket), "mission": mission})


@app.post("/api/parceldesk/tickets/local")
def api_parceldesk_ticket_local(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """Admit an operator-supplied product patch; the Gate still requires approval."""
    try:
        data = LocalTicketRequest(**payload).model_dump()
    except ValidationError as error:
        return JSONResponse({"ok": False, "error": "Invalid ticket: " + error.errors()[0]["msg"]}, status_code=422)
    patch = data.pop("patch")
    try:
        ticket = parceldesk_store.create_ticket(**data)
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=422)
    try:
        mission = engine.launch_ticket(request_id=f"local-{ticket['id']}", local_patch=patch, **data)
    except ValueError as error:
        ticket = parceldesk_store.fail_ticket(ticket["id"], str(error))
        return JSONResponse({"ok": False, "ticket": _decorate_ticket(ticket), "error": str(error)}, status_code=422)
    except RuntimeError as error:
        ticket = parceldesk_store.fail_ticket(ticket["id"], str(error))
        return JSONResponse({"ok": False, "ticket": _decorate_ticket(ticket), "error": str(error)}, status_code=409)
    ticket = parceldesk_store.link_ticket(ticket["id"], mission["id"])
    return JSONResponse({"ok": True, "ticket": _decorate_ticket(ticket), "mission": mission})


@app.get("/api/quote")
def api_quote(
    grams: int = Query(default=1_200, ge=0, le=30_000),
    buggy: bool = Query(default=False),
) -> JSONResponse:
    return JSONResponse(quote_or_error(grams, buggy=buggy))


class RefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)


class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=4, max_length=120)
    outcome: str = Field(min_length=10, max_length=500)
    area: str = Field(pattern=r"^(pricing|orders|refunds|warehouse|support|accessibility)$")
    risk: str = Field(pattern=r"^(low|medium|high)$")
    acceptance: str = Field(min_length=10, max_length=2000)
    constraints: str = Field(default="", max_length=1000)
    product: str = Field(default="legacy", pattern=r"^(legacy|product)$")


class LocalTicketRequest(TicketRequest):
    patch: str = Field(min_length=10, max_length=64_000)


class MissionRequest(BaseModel):
    fixture: str = "PD01"
    request_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class QueuedMissionRequest(MissionRequest):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class TestTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    title: str = Field(min_length=4, max_length=120)
    task: str = Field(min_length=20, max_length=4000)
    test_file: str = Field(pattern=r"^tests/test_[a-z][a-z0-9_]{0,72}\.py$")
    minimum_new_tests: int = Field(strict=True, ge=1, le=100)


@app.post("/api/tasks/tests")
def api_enqueue_test_task(payload: TestTaskRequest) -> JSONResponse:
    try:
        mission = engine.enqueue_test_task(**payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=409)
    return JSONResponse({"ok": True, "mission": mission})


@app.get("/api/missions/queue")
def api_mission_queue() -> JSONResponse:
    return JSONResponse({"missions": store.queued_missions(), "provider_retry_at": store.get_kv("provider_retry_at")})


@app.post("/api/missions/queue")
def api_enqueue_mission(payload: QueuedMissionRequest) -> JSONResponse:
    try:
        mission = engine.launch_mission(payload.fixture, request_id=payload.request_id, defer=True)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=409)
    return JSONResponse({"ok": True, "mission": mission})


@app.get("/api/missions/{mission_id}")
def api_mission(mission_id: str) -> JSONResponse:
    mission = store.mission(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return JSONResponse({"mission": mission, "steps": store.steps(mission_id), "runs": store.runs(mission_id),
                         "delivery": json.loads(store.get_kv(f"delivery:{mission_id}", "null")),
                         "delivery_error": store.get_kv(f"delivery_error:{mission_id}") or None})


@app.post("/api/missions")
def api_launch_mission(payload: MissionRequest | None = Body(default=None)) -> JSONResponse:
    try:
        mission = engine.launch_mission(payload.fixture if payload else None, request_id=payload.request_id if payload else None)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=409)
    return JSONResponse({"ok": True, "mission": mission, "snapshot": engine.snapshot()})


@app.post("/api/missions/{mission_id}/review/retry")
def api_retry_review(mission_id: str) -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "mission": engine.retry_review(mission_id)})
    except (ValueError, RuntimeError, BlockingIOError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/missions/{mission_id}/approve")
def api_approve(mission_id: str) -> JSONResponse:
    approved = engine.approve(mission_id)
    return JSONResponse(
        {
            "ok": approved,
            "error": None if approved else "Mission is not awaiting a human checkpoint.",
            "snapshot": engine.snapshot(),
        }
    )


@app.post("/api/sandbox/check")
def api_sandbox_check() -> JSONResponse:
    return JSONResponse({"ok": True, "result": engine.sandbox_check(), "snapshot": engine.snapshot()})


@app.post("/api/ops/check")
def api_ops_check() -> JSONResponse:
    return JSONResponse({"ok": True, "result": engine.ops_integrity(), "snapshot": engine.snapshot()})


@app.post("/api/croa/check")
def api_croa_check() -> JSONResponse:
    return JSONResponse({"ok": True, "result": engine.croa_audit(), "snapshot": engine.snapshot()})


@app.get("/api/evidence/{packet_id}")
def api_evidence(packet_id: str) -> JSONResponse:
    for packet in store.evidence(100):
        if packet["id"] == packet_id:
            return JSONResponse(packet["payload"]["payload"])
    raise HTTPException(status_code=404, detail="Evidence packet not found")


@app.get("/api/evidence/{packet_id}/verify")
def api_evidence_verify(packet_id: str) -> JSONResponse:
    for packet in store.evidence(100):
        if packet["id"] == packet_id:
            return JSONResponse(EvidenceWriter.verify(Path(packet["path"]), expected_digest=packet["digest"]))
    raise HTTPException(status_code=404, detail="Evidence packet not found")


@app.post("/api/reset")
def api_reset() -> JSONResponse:
    result = engine.reset()
    return JSONResponse(result, status_code=409)


@app.get("/api/config")
def api_config() -> JSONResponse:
    """Non-secret runtime configuration for the operator."""
    return JSONResponse(
        {
            "mode": settings.mode,
            "agent_runtime": settings.agent_runtime,
            "agent_model": settings.agent_model or None,
            "opencode_model": settings.opencode_model,
            "opencode_review_model": settings.opencode_review_model,
            "opencode_bin": str(settings.opencode_bin_path()),
            "opencode_credential": "env:OPENCODE_API_KEY" if settings.opencode_api_key else "none (free model)",
            "agent_timeout_seconds": settings.agent_timeout,
            "agent_max_turns": settings.agent_max_turns,
            "budget_usd": settings.budget_usd,
            "paperclip_base_url": settings.paperclip_base_url,
            "db_path": str(settings.db_path),
            "sandbox_dir": str(settings.sandbox_dir),
            "graph_path": str(settings.graph_path),
        }
    )
