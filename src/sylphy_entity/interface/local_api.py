"""Loopback-only HTTP interface; it exposes runtime state, not Brain policy."""

from __future__ import annotations

import secrets
from typing import Any, Protocol

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from sylphy_entity.contracts.events import EntityEvent


class RuntimeView(Protocol):
    def status(self) -> dict[str, Any]: ...

    def world_snapshot(self) -> dict[str, Any]: ...

    async def publish_event(self, event: EntityEvent) -> None: ...

    async def request_shutdown(self) -> None: ...

    async def pause_autonomy(self) -> dict[str, Any]: ...

    async def resume_autonomy(self) -> dict[str, Any]: ...

    async def revoke_permission(self, scope: str | None = None) -> dict[str, Any]: ...

    async def restore_permission(self, scope: str | None = None) -> dict[str, Any]: ...

    def voice_status(self) -> dict[str, Any]: ...

    def avatar_presentation_status(self) -> dict[str, Any]: ...

    def memory_status(self, limit: int = 50) -> dict[str, Any]: ...

    async def speak_text(self, text: str, voice: str, language: str, speed: float) -> dict[str, Any]: ...

    async def stop_voice(self) -> dict[str, Any]: ...

    async def chat(self, text: str, session_id: str, channel: str = "local-api") -> dict[str, Any]: ...

    def conversation_history(self, session_id: str, limit: int = 40) -> list[dict[str, Any]]: ...


class EventInput(BaseModel):
    event_type: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type", "source")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        if cleaned.startswith(("world.", "runtime.", "brain.", "action.", "execution.", "cognition.")):
            raise ValueError("world/runtime/brain/action/cognition event namespaces are reserved for Entity internals")
        return cleaned


class ChatInput(BaseModel):
    text: str = Field(min_length=1, max_length=16000)
    session_id: str = Field(default="default", min_length=1, max_length=128)

    @field_validator("text", "session_id")
    @classmethod
    def chat_fields_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned


class VoiceSpeakInput(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    voice: str = Field(default="af_heart", min_length=1, max_length=128)
    language: str = Field(default="en-us", min_length=2, max_length=16)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)

    @field_validator("text", "voice", "language")
    @classmethod
    def require_voice_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned


class PermissionScopeInput(BaseModel):
    scope: str | None = Field(default=None, min_length=1, max_length=2048)


class EventAccepted(BaseModel):
    event_id: str
    accepted: bool = True


def create_app(runtime: RuntimeView, control_token: str) -> FastAPI:
    app = FastAPI(
        title="SYLPHY Entity Runtime",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        snapshot = runtime.status()
        state = str(snapshot["lifecycle"])
        if state in {"error", "stopped", "stopping"}:
            raise HTTPException(status_code=503, detail=f"Entity is {state}")
        return {"status": "ok", "lifecycle": state}

    @app.get("/status")
    async def status() -> dict[str, Any]:
        return runtime.status()

    @app.get("/world")
    async def world() -> dict[str, Any]:
        return runtime.world_snapshot()

    def authorize(candidate: str | None) -> None:
        if candidate is None or not secrets.compare_digest(candidate, control_token):
            raise HTTPException(status_code=401, detail="Valid X-Sylphy-Control-Token required")

    @app.post("/events", response_model=EventAccepted, status_code=202)
    async def publish_event(
        body: EventInput,
        control_token_header: str | None = Header(default=None, alias="X-Sylphy-Control-Token"),
    ) -> EventAccepted:
        authorize(control_token_header)
        event = EntityEvent(
            event_type=body.event_type,
            source=body.source,
            payload=body.payload,
        )
        try:
            await runtime.publish_event(event)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return EventAccepted(event_id=event.event_id)

    @app.get("/conversation/history")
    async def conversation_history(session_id: str, limit: int = 40) -> list[dict[str, Any]]:
        try:
            return runtime.conversation_history(session_id, limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/chat", status_code=202)
    async def chat(
        body: ChatInput,
        control_token_header: str | None = Header(default=None, alias="X-Sylphy-Control-Token"),
    ) -> dict[str, Any]:
        authorize(control_token_header)
        try:
            return await runtime.chat(body.text, body.session_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/voice/status")
    async def voice_status() -> dict[str, Any]:
        return runtime.voice_status()

    @app.get("/avatar/presentation")
    async def avatar_presentation() -> dict[str, Any]:
        return runtime.avatar_presentation_status()

    @app.get("/memory/status")
    async def memory_status(limit: int = 50) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
        return runtime.memory_status(limit)

    @app.post("/voice/speak", status_code=202)
    async def voice_speak(
        body: VoiceSpeakInput,
        control_token_header: str | None = Header(default=None, alias="X-Sylphy-Control-Token"),
    ) -> dict[str, Any]:
        authorize(control_token_header)
        try:
            return await runtime.speak_text(body.text, body.voice, body.language, body.speed)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/voice/stop", status_code=200)
    async def voice_stop(
        control_token_header: str | None = Header(default=None, alias="X-Sylphy-Control-Token"),
    ) -> dict[str, Any]:
        authorize(control_token_header)
        return await runtime.stop_voice()

    @app.post("/autonomy/pause", status_code=200)
    async def pause_autonomy(
        control_token_header: str | None = Header(default=None, alias="X-Sylphy-Control-Token"),
    ) -> dict[str, Any]:
        authorize(control_token_header)
        return await runtime.pause_autonomy()

    @app.post("/autonomy/resume", status_code=200)
    async def resume_autonomy(
        control_token_header: str | None = Header(default=None, alias="X-Sylphy-Control-Token"),
    ) -> dict[str, Any]:
        authorize(control_token_header)
        try:
            return await runtime.resume_autonomy()
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/permissions/revoke", status_code=200)
    async def revoke_permission(
        body: PermissionScopeInput,
        control_token_header: str | None = Header(default=None, alias="X-Sylphy-Control-Token"),
    ) -> dict[str, Any]:
        authorize(control_token_header)
        try:
            return await runtime.revoke_permission(body.scope)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/permissions/restore", status_code=200)
    async def restore_permission(
        body: PermissionScopeInput,
        control_token_header: str | None = Header(default=None, alias="X-Sylphy-Control-Token"),
    ) -> dict[str, Any]:
        authorize(control_token_header)
        try:
            return await runtime.restore_permission(body.scope)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/shutdown", status_code=202)
    async def shutdown(
        background_tasks: BackgroundTasks,
        control_token_header: str | None = Header(default=None, alias="X-Sylphy-Control-Token"),
    ) -> dict[str, str]:
        authorize(control_token_header)
        # Starlette runs this only after the 202 response has been sent.
        background_tasks.add_task(runtime.request_shutdown)
        return {"status": "shutdown_requested"}

    return app
