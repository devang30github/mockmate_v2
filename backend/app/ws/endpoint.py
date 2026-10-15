import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.db.session import open_session
from app.domain.question_queue import build_question_queue
from app.domain.stt_hints import extract_terms
from app.services import analysis as analysis_svc
from app.services import auth as auth_service
from app.services import interviews as interviews_svc
from app.services import planning as planning_svc
from app.ws.protocol import server_message
from app.ws.room import Room

log = logging.getLogger("mockmate.ws")
router = APIRouter()


async def _reject(ws: WebSocket, close_code: int, code: str, message: str) -> None:
    await ws.send_json(server_message("error", code=code, message=message, retryable=False))
    await ws.close(code=close_code)


@router.websocket("/ws/interviews/{interview_id}")
async def interview_socket(ws: WebSocket, interview_id: uuid.UUID) -> None:
    await ws.accept()
    token = ws.cookies.get(get_settings().session_cookie_name)
    plan_row = None
    hints: list[str] = []
    async with open_session() as s:
        user = await auth_service.get_user_for_token(s, token) if token else None
        found = await interviews_svc.get_interview(s, user, interview_id) if user else None
        if found is not None:
            planned = await planning_svc.get_plan(s, user, interview_id)
            plan_row = planned[0] if planned else None
            analysis_row = await analysis_svc.get_analysis(s, user, interview_id)
            if analysis_row is not None:
                hints = extract_terms(analysis_row.result)
    if user is None:
        await _reject(ws, 4401, "unauthorized", "Please log in.")
        return
    if found is None:
        await _reject(ws, 4404, "not_found", "Interview not found.")
        return

    queue = build_question_queue(plan_row.result) if plan_row is not None else []
    room = Room(ws, interview_id, ws.app.state.providers, queue, hints)
    try:
        await room.run()
    except WebSocketDisconnect:
        pass
    except RuntimeError:  # sending after the client went away
        log.info("socket closed while sending")