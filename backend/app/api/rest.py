from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import room_manager
from app.schemas import (
    ActionRequest,
    AIHealthResponse,
    AIPhaseRunResponse,
    AIRunToEndResponse,
    AgentStatusResponse,
    CreateAIRoomRequest,
    CreateRoomRequest,
    GenericMessage,
    HotSwapAgentRequest,
    JoinRoomRequest,
    RegisterAgentRequest,
    RegisterAgentGlobalRequest,
    ReplayResponse,
    RoomConfigResponse,
    RoomPlayerView,
    RoomStateResponse,
    RunToEndRequest,
    StartGameResponse,
    VoteRequest,
)

router = APIRouter(prefix="/api", tags=["werewolf"])


@router.post("/rooms", response_model=dict)
def create_room(req: CreateRoomRequest) -> dict:
    try:
        return room_manager.create_room(
            req.owner_nickname,
            player_count=req.player_count,
            custom_role_config=req.custom_role_config,
            admin_override=req.admin_override,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai/rooms", response_model=dict)
def create_ai_room(req: CreateAIRoomRequest) -> dict:
    try:
        return room_manager.create_ai_room(
            req.owner_nickname,
            player_count=req.player_count,
            custom_role_config=req.custom_role_config,
            admin_override=req.admin_override,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rooms/{room_id}/join", response_model=dict)
def join_room(room_id: str, req: JoinRoomRequest) -> dict:
    try:
        return room_manager.join_room(room_id, req.nickname)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai/rooms/{room_id}/agents/register", response_model=dict)
def register_agent(room_id: str, req: RegisterAgentRequest) -> dict:
    try:
        return room_manager.register_agent(
            room_id=room_id,
            player_id=req.player_id,
            ipc_endpoint=req.ipc_endpoint,
            model_type=req.model_type,
            timeout_sec=req.timeout_sec,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/agents/register", response_model=dict)
def register_agent_global(req: RegisterAgentGlobalRequest) -> dict:
    try:
        return room_manager.register_agent(
            room_id=req.room_id,
            player_id=req.player_id,
            ipc_endpoint=req.endpoint,
            model_type=req.model,
            timeout_sec=req.timeout_sec,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai/rooms/{room_id}/agents/hot-swap", response_model=dict)
def hot_swap_agent(room_id: str, req: HotSwapAgentRequest) -> dict:
    try:
        return room_manager.hot_swap_agent(
            room_id=room_id,
            player_id=req.player_id,
            ipc_endpoint=req.ipc_endpoint,
            model_type=req.model_type,
            timeout_sec=req.timeout_sec,
            reset_role_runtime_state=req.reset_role_runtime_state,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rooms/{room_id}/start", response_model=StartGameResponse)
def start_game(room_id: str, owner_id: str) -> StartGameResponse:
    try:
        room_manager.start_game(room_id, owner_id)
        state = room_manager.state(room_id)
        return StartGameResponse(room_id=room_id, phase=state["phase"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/rooms/{room_id}", response_model=RoomStateResponse)
def room_state(room_id: str) -> RoomStateResponse:
    try:
        state = room_manager.state(room_id)
        return RoomStateResponse(
            room_id=state["room_id"],
            owner_id=state["owner_id"],
            started=state["started"],
            phase=state["phase"],
            game_over=state["game_over"],
            winner=state["winner"],
            players=[RoomPlayerView(**p) for p in state["players"]],
            game_metadata=state.get("game_metadata"),
            round_no=state.get("round_no"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/rooms/{room_id}/config", response_model=RoomConfigResponse)
def room_config(room_id: str) -> RoomConfigResponse:
    try:
        return RoomConfigResponse(**room_manager.room_config(room_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/rooms/{room_id}/night-action", response_model=GenericMessage)
def night_action(room_id: str, req: ActionRequest) -> GenericMessage:
    try:
        room = room_manager.must_get_room(room_id)
        room.engine.submit_night_action(req.player_id, req.target_id, req.save)
        return GenericMessage(message="night action accepted")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rooms/{room_id}/vote", response_model=GenericMessage)
def day_vote(room_id: str, req: VoteRequest) -> GenericMessage:
    try:
        room = room_manager.must_get_room(room_id)
        room.engine.submit_vote(req.player_id, req.target_id)
        return GenericMessage(message="vote accepted")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rooms/{room_id}/hunter-shot", response_model=GenericMessage)
def hunter_shot(room_id: str, req: VoteRequest) -> GenericMessage:
    if not req.target_id:
        raise HTTPException(status_code=400, detail="target_id is required")
    try:
        room = room_manager.must_get_room(room_id)
        room.engine.submit_hunter_shot(req.player_id, req.target_id)
        return GenericMessage(message="hunter shot accepted")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rooms/{room_id}/advance", response_model=RoomStateResponse)
def advance(room_id: str) -> RoomStateResponse:
    try:
        room = room_manager.must_get_room(room_id)
        room.engine.timeout_autorun_current_phase()
        room_manager.remove_room_if_game_over(room_id)
        state = room.engine.public_state()
        return RoomStateResponse(
            room_id=state["room_id"],
            owner_id=state["owner_id"],
            started=state["started"],
            phase=state["phase"],
            game_over=state["game_over"],
            winner=state["winner"],
            players=[RoomPlayerView(**p) for p in state["players"]],
            game_metadata=state.get("game_metadata"),
            round_no=state.get("round_no"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai/rooms/{room_id}/run-phase", response_model=AIPhaseRunResponse)
async def ai_run_phase(room_id: str) -> AIPhaseRunResponse:
    try:
        result = await room_manager.ai_run_single_phase(room_id)
        return AIPhaseRunResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai/rooms/{room_id}/run-to-end", response_model=AIRunToEndResponse)
async def ai_run_to_end(room_id: str, req: RunToEndRequest) -> AIRunToEndResponse:
    try:
        result = await room_manager.ai_run_to_end(room_id, max_steps=req.max_steps)
        return AIRunToEndResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ai/rooms/{room_id}/agents/health", response_model=AIHealthResponse)
def ai_health(room_id: str) -> AIHealthResponse:
    try:
        result = room_manager.ai_health(room_id)
        return AIHealthResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/agents/status", response_model=AgentStatusResponse)
def agents_status(room_id: str) -> AgentStatusResponse:
    try:
        return AgentStatusResponse(**room_manager.agents_status(room_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/replay/{record_id}", response_model=ReplayResponse)
def replay_record(record_id: int) -> ReplayResponse:
    try:
        return ReplayResponse(record=room_manager.replay_record(record_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
