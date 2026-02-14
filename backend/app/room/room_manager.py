from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from threading import RLock
from typing import Dict, Optional

import httpx

from app.agent.child_process_manager import ChildAgentProcessManager
from app.agent.god_orchestrator import GodOrchestrator
from app.core.game_config import default_game_config
from app.engine.game_engine import GameEngine
from app.storage.repository import SQLiteRepository


@dataclass(slots=True)
class Room:
    room_id: str
    engine: GameEngine
    orchestrator: GodOrchestrator
    sockets: Dict[str, object] = field(default_factory=dict)


class RoomManager:
    def __init__(self, repository: Optional[SQLiteRepository] = None) -> None:
        self._rooms: Dict[str, Room] = {}
        self._lock = RLock()
        self.repository = repository or SQLiteRepository("./backend/data/werewolf.db")
        self.child_agents = ChildAgentProcessManager()

    def create_room(
        self,
        owner_nickname: str,
        player_count: int = 12,
        custom_role_config: Optional[dict] = None,
        admin_override: bool = False,
    ) -> dict:
        with self._lock:
            room_id = self._new_room_id()
            owner_id = self._new_player_id()
            config = default_game_config(
                player_count=player_count,
                custom_role_config=custom_role_config,
                admin_override=admin_override,
            )
            engine = GameEngine(room_id=room_id, owner_id=owner_id, config=config)
            engine.add_player(owner_id, owner_nickname)
            room = Room(room_id=room_id, engine=engine, orchestrator=GodOrchestrator())
            self._rooms[room_id] = room
            return {
                "room_id": room_id,
                "owner_id": owner_id,
                "player_count": config.player_count,
                "role_distribution": config.role_distribution,
            }

    def create_ai_room(
        self,
        owner_nickname: str = "cat_01",
        player_count: int = 12,
        custom_role_config: Optional[dict] = None,
        admin_override: bool = False,
    ) -> dict:
        with self._lock:
            room_id = self._new_room_id()
            owner_id = "cat_01"
            config = default_game_config(
                player_count=player_count,
                custom_role_config=custom_role_config,
                admin_override=admin_override,
            )
            engine = GameEngine(room_id=room_id, owner_id=owner_id, config=config)
            room = Room(room_id=room_id, engine=engine, orchestrator=GodOrchestrator())
            self._rooms[room_id] = room

            for idx in range(1, config.total_players + 1):
                pid = f"cat_{idx:02d}"
                nickname = owner_nickname if idx == 1 else pid
                engine.add_player(pid, nickname)

            return {
                "room_id": room_id,
                "owner_id": owner_id,
                "player_count": config.player_count,
                "role_distribution": config.role_distribution,
                "players": [p.player_id for p in engine.snapshot.players.values()],
            }

    def join_room(self, room_id: str, nickname: str) -> dict:
        room = self.get_room(room_id)
        if not room:
            raise ValueError("room not found")

        player_id = self._new_player_id()
        room.engine.add_player(player_id, nickname)
        return {"room_id": room_id, "player_id": player_id}

    def start_game(self, room_id: str, owner_id: str) -> None:
        room = self.must_get_room(room_id)
        room.engine.start_game(owner_id)

    def register_agent(
        self,
        room_id: str,
        player_id: str,
        nickname: Optional[str],
        ipc_endpoint: str,
        model_type: str,
        timeout_sec: int,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        cli_command: Optional[str] = None,
        cli_timeout_sec: int = 20,
        preflight_check: bool = False,
    ) -> dict:
        room = self.must_get_room(room_id)
        if player_id not in room.engine.snapshot.players:
            raise ValueError("player not found")
        self._assert_agent_endpoint(ipc_endpoint)
        if preflight_check:
            self._assert_agent_model_access(
                ipc_endpoint=ipc_endpoint,
                player_id=player_id,
                api_url=api_url,
                api_key=api_key,
                model_name=model_name,
                cli_command=cli_command,
                cli_timeout_sec=cli_timeout_sec,
            )
        if isinstance(nickname, str) and nickname.strip():
            room.engine.snapshot.players[player_id].nickname = nickname.strip()
        reg = room.orchestrator.scheduler.registry.register(
            player_id=player_id,
            ipc_endpoint=ipc_endpoint,
            model_type=model_type,
            timeout_sec=timeout_sec,
            api_url=api_url,
            api_key=api_key,
            model_name=model_name,
            cli_command=cli_command,
            cli_timeout_sec=cli_timeout_sec,
        )
        invoke_mode = (
            "cli"
            if reg.cli_command
            else ("api" if reg.api_url and reg.api_key else "mock")
        )
        return {
            "player_id": reg.player_id,
            "ipc_endpoint": reg.ipc_endpoint,
            "model_type": reg.model_type,
            "timeout_sec": reg.timeout_sec,
            "model_name": reg.model_name,
            "invoke_mode": invoke_mode,
            "online": reg.online,
        }

    def hot_swap_agent(
        self,
        room_id: str,
        player_id: str,
        ipc_endpoint: str,
        model_type: str,
        timeout_sec: int,
        api_url: Optional[str],
        api_key: Optional[str],
        model_name: Optional[str],
        cli_command: Optional[str],
        cli_timeout_sec: int,
        preflight_check: bool,
        reset_role_runtime_state: bool,
    ) -> dict:
        room = self.must_get_room(room_id)
        player = room.engine.snapshot.players.get(player_id)
        if not player:
            raise ValueError("player not found")
        self._assert_agent_endpoint(ipc_endpoint)
        if preflight_check:
            self._assert_agent_model_access(
                ipc_endpoint=ipc_endpoint,
                player_id=player_id,
                api_url=api_url,
                api_key=api_key,
                model_name=model_name,
                cli_command=cli_command,
                cli_timeout_sec=cli_timeout_sec,
            )

        reg = room.orchestrator.scheduler.registry.register(
            player_id=player_id,
            ipc_endpoint=ipc_endpoint,
            model_type=model_type,
            timeout_sec=timeout_sec,
            api_url=api_url,
            api_key=api_key,
            model_name=model_name,
            cli_command=cli_command,
            cli_timeout_sec=cli_timeout_sec,
        )
        player.online = True
        player.entrusted = False
        if reset_role_runtime_state:
            player.can_hunter_shoot = False

        invoke_mode = (
            "cli"
            if reg.cli_command
            else ("api" if reg.api_url and reg.api_key else "mock")
        )

        return {
            "player_id": reg.player_id,
            "ipc_endpoint": reg.ipc_endpoint,
            "model_type": reg.model_type,
            "timeout_sec": reg.timeout_sec,
            "model_name": reg.model_name,
            "invoke_mode": invoke_mode,
            "online": reg.online,
            "reset_role_runtime_state": reset_role_runtime_state,
        }

    def bootstrap_agents(
        self,
        room_id: str,
        host: str,
        start_port: int,
        startup_timeout_sec: float,
        model_type: str,
        timeout_sec: int,
        api_url: Optional[str],
        api_key: Optional[str],
        model_name: Optional[str],
        cli_command: Optional[str],
        cli_timeout_sec: int,
    ) -> dict:
        room = self.must_get_room(room_id)
        players = list(room.engine.snapshot.players.values())
        player_ids = [p.player_id for p in players]
        endpoints = self.child_agents.bootstrap_room(
            room_id=room_id,
            player_ids=player_ids,
            host=host,
            start_port=start_port,
            startup_timeout_sec=startup_timeout_sec,
        )

        return {
            "room_id": room_id,
            "bootstrap_defaults": {
                "model_type": model_type,
                "timeout_sec": timeout_sec,
                "api_url": bool(api_url),
                "api_key": bool(api_key),
                "model_name": model_name,
                "cli_command": bool(cli_command),
                "cli_timeout_sec": cli_timeout_sec,
            },
            "endpoints": endpoints,
            "child_processes": self.child_agents.status(room_id),
        }

    def teardown_agents(self, room_id: str) -> dict:
        self.child_agents.stop_room(room_id)
        return {"room_id": room_id, "stopped": True}

    def child_process_status(self, room_id: str) -> dict:
        self.must_get_room(room_id)
        return {
            "room_id": room_id,
            "child_processes": self.child_agents.status(room_id),
        }

    @staticmethod
    def _assert_agent_endpoint(ipc_endpoint: str) -> None:
        base = (ipc_endpoint or "").strip().rstrip("/")
        if not base:
            raise ValueError("ipc_endpoint is required")
        health_url = base + "/health"
        try:
            with httpx.Client(timeout=2.5) as client:
                resp = client.get(health_url)
            if resp.status_code >= 400:
                raise ValueError(f"agent endpoint unhealthy: {health_url} -> HTTP {resp.status_code}")
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"agent endpoint unreachable: {health_url} ({type(exc).__name__})") from exc

    @staticmethod
    def _assert_agent_model_access(
        *,
        ipc_endpoint: str,
        player_id: str,
        api_url: Optional[str],
        api_key: Optional[str],
        model_name: Optional[str],
        cli_command: Optional[str],
        cli_timeout_sec: int,
    ) -> None:
        if not (cli_command or (api_url and api_key)):
            return

        url = (ipc_endpoint or "").strip().rstrip("/") + "/act"
        payload = {
            "session_id": "bootstrap-preflight",
            "player_id": player_id,
            "role": "villager",
            "phase": "day_discuss",
            "visible_state": {
                "player_id": player_id,
                "player_name": "probe",
                "alive_players": ["probe", "test_target"],
                "alive_player_ids": [player_id, "test_target"],
            },
            "prompt_template": "返回JSON action+reasoning",
            "agent_config": {
                "api_url": api_url,
                "api_key": api_key,
                "model_name": model_name,
                "cli_command": cli_command,
                "cli_timeout_sec": cli_timeout_sec,
            },
        }
        timeout = httpx.Timeout(connect=5.0, read=45.0, write=10.0, pool=5.0)

        def _once() -> None:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload)
            if resp.status_code >= 400:
                detail = ""
                try:
                    data = resp.json() if resp.content else {}
                    detail = str((data or {}).get("detail") or "")
                except Exception:  # noqa: BLE001
                    detail = resp.text[:200] if resp.text else ""
                msg = f"agent preflight http={resp.status_code}"
                if detail:
                    msg += f" detail={detail}"
                raise ValueError(msg)

            data = resp.json() if resp.content else {}
            reasoning = str((data or {}).get("reasoning") or "")
            if reasoning.startswith("[api-error:"):
                raise ValueError("model api preflight failed: " + reasoning)
            if reasoning.startswith("[cli-error:"):
                raise ValueError("cli preflight failed: " + reasoning)

        try:
            _once()
        except httpx.ReadTimeout:
            _once()
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"agent preflight unreachable: {type(exc).__name__}") from exc

    async def ai_run_single_phase(self, room_id: str) -> dict:
        room = self.must_get_room(room_id)
        await room.orchestrator.run_single_phase(room.engine)
        self.remove_room_if_game_over(room_id)
        return {
            "state": room.engine.public_state(),
            "metrics": room.orchestrator.scheduler.metrics.to_dict(),
        }

    async def ai_run_to_end(self, room_id: str, max_steps: int = 500) -> dict:
        room = self.must_get_room(room_id)
        result = await room.orchestrator.run_to_game_over(room.engine, max_steps=max_steps)
        self.remove_room_if_game_over(room_id)
        return {
            "result": result,
            "state": room.engine.public_state(),
            "metrics": room.orchestrator.scheduler.metrics.to_dict(),
        }

    def ai_health(self, room_id: str) -> dict:
        room = self.must_get_room(room_id)
        agents = room.orchestrator.scheduler.registry.all()
        return {
            "all_ready": room.orchestrator.all_agents_ready(room.engine),
            "agents": {
                pid: {
                    "online": agent.online,
                    "entrusted": agent.entrusted,
                    "model_type": agent.model_type,
                    "model_name": agent.model_name,
                    "timeout_sec": agent.timeout_sec,
                    "failed_count": agent.failed_count,
                }
                for pid, agent in agents.items()
            },
            "metrics": room.orchestrator.scheduler.metrics.to_dict(),
        }

    def agents_status(self, room_id: str) -> dict:
        room = self.must_get_room(room_id)
        agents = room.orchestrator.scheduler.registry.all()
        return {
            "room_id": room_id,
            "agents": {
                pid: {
                    "online": agent.online,
                    "entrusted": agent.entrusted,
                    "model_type": agent.model_type,
                    "model_name": agent.model_name,
                    "invoke_mode": (
                        "cli"
                        if agent.cli_command
                        else ("api" if agent.api_url and agent.api_key else "mock")
                    ),
                    "timeout_sec": agent.timeout_sec,
                    "failed_count": agent.failed_count,
                    "last_heartbeat": agent.last_heartbeat.isoformat(),
                    "error_msg": agent.last_error,
                }
                for pid, agent in agents.items()
            },
            "metrics": room.orchestrator.scheduler.metrics.to_dict(),
        }

    def room_config(self, room_id: str) -> dict:
        room = self.must_get_room(room_id)
        config = room.engine.config
        return {
            "room_id": room_id,
            "player_count": config.player_count,
            "role_distribution": config.role_distribution,
            "night_order": config.night_order,
            "warnings": config.warnings,
        }

    def health_summary(self) -> dict:
        with self._lock:
            rooms = list(self._rooms.values())

        total_agents = 0
        online_agents = 0
        per_room: dict[str, dict] = {}
        for room in rooms:
            regs = room.orchestrator.scheduler.registry.all()
            total_agents += len(regs)
            online = sum(1 for reg in regs.values() if reg.online)
            online_agents += online
            per_room[room.room_id] = {
                "players": len(room.engine.snapshot.players),
                "agents_registered": len(regs),
                "agents_online": online,
                "phase": room.engine.snapshot.phase.value,
                "game_over": room.engine.snapshot.game_over,
            }

        return {
            "rooms": len(rooms),
            "agents_total": total_agents,
            "agents_online": online_agents,
            "rooms_detail": per_room,
        }

    def replay_record(self, record_id: int) -> dict:
        record = self.repository.get_game_record(record_id)
        if not record:
            raise ValueError("record not found")
        return record

    def get_room(self, room_id: str) -> Optional[Room]:
        with self._lock:
            return self._rooms.get(room_id)

    def must_get_room(self, room_id: str) -> Room:
        room = self.get_room(room_id)
        if not room:
            raise ValueError("room not found")
        return room

    def state(self, room_id: str) -> dict:
        room = self.must_get_room(room_id)
        return room.engine.public_state()

    def remove_room_if_game_over(self, room_id: str) -> None:
        with self._lock:
            room = self._rooms.get(room_id)
            if not room:
                return
            if room.engine.snapshot.game_over:
                self.child_agents.stop_room(room_id)
                snapshot = room.engine.snapshot
                self.repository.save_finished_game(
                    room_id=snapshot.room_id,
                    winner=snapshot.winner.value if snapshot.winner else "unknown",
                    rounds=snapshot.round_context.round_no,
                    payload={
                        "audit": snapshot.action_audit_log,
                        "agent_audit": room.orchestrator.scheduler.audit_logs,
                        "metrics": room.orchestrator.scheduler.metrics.to_dict(),
                        "players": [
                            {
                                "player_id": p.player_id,
                                "nickname": p.nickname,
                                "role": p.role.value if p.role else None,
                                "alive": p.alive,
                            }
                            for p in snapshot.players.values()
                        ],
                    },
                )

    @staticmethod
    def _new_room_id() -> str:
        return uuid.uuid4().hex[:8]

    @staticmethod
    def _new_player_id() -> str:
        return uuid.uuid4().hex
