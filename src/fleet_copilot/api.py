"""HTTP boundary: bearer credentials select tenant/role; request bodies cannot override them."""

import json
import os
import secrets
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from .actions import Actions, Decision
from .agent import FleetAgent
from .analytics import Analytics
from .config import Settings
from .domain import FleetError, Principal, StrictModel
from .model import OpenAIModel
from .store import Store


class Ask(StrictModel):
    question: str = Field(min_length=1, max_length=4000)
    allow_proposals: bool = False


def create_app(store: Store, tokens: dict[str, Principal], model_factory: Callable = OpenAIModel) -> FastAPI:
    if not tokens or any(len(token) < 32 for token in tokens):
        raise ValueError(
            "Configure FLEET_API_TOKENS with independent bearer tokens of at least 32 characters"
        )
    app = FastAPI(
        title="Rayda Fleet Copilot",
        version="0.1.0",
        description="Tenant-scoped historical telemetry analysis. All approved actions are simulated.",
    )
    bearer = HTTPBearer(auto_error=False)
    actions = Actions(store, Analytics(store))

    def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials and credentials.scheme.casefold() == "bearer":
            for token, principal in tokens.items():
                if secrets.compare_digest(token, credentials.credentials):
                    return principal
        raise HTTPException(401, "Valid bearer token required", headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(FleetError)
    async def fleet_error_handler(_, exc):
        return JSONResponse(status_code=exc.status, content={"error": exc.code, "message": exc.message})

    @app.get("/health")
    def health():
        return {"status": "ok", "action_mode": "simulation"}

    @app.post("/v1/ask")
    def ask(body: Ask, principal: Principal = Depends(authenticate)):
        return FleetAgent(store, model_factory()).ask(principal, body.question, body.allow_proposals)

    @app.get("/v1/runs/{run_id}")
    def run(run_id: str, principal: Principal = Depends(authenticate)):
        return store.get_run(principal, run_id)

    @app.get("/v1/proposals")
    def proposals(principal: Principal = Depends(authenticate)):
        return actions.list(principal)

    @app.get("/v1/proposals/{proposal_id}")
    def proposal(proposal_id: str, principal: Principal = Depends(authenticate)):
        return actions.get(principal, proposal_id)

    @app.post("/v1/proposals/{proposal_id}/decision")
    def decide(proposal_id: str, body: Decision, principal: Principal = Depends(authenticate)):
        return actions.decide(principal, proposal_id, body)

    @app.get("/v1/audit")
    def audit(principal: Principal = Depends(authenticate)):
        return store.audit_log(principal)

    return app


def from_config():
    settings = Settings.load()
    try:
        raw = json.loads(os.getenv("FLEET_API_TOKENS", "{}"))
        tokens = {key: Principal.model_validate(value) for key, value in raw.items()}
    except (ValueError, AttributeError):
        raise ValueError("FLEET_API_TOKENS must map bearer tokens to company_id, actor_id and role") from None
    return create_app(settings.store(), tokens)
