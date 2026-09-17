"""Multi-Agent System — sub-agent spawning, coordination, delegation."""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


class AgentRole(Enum):
    ORCHESTRATOR = "orchestrator"
    EMAIL = "email"
    BROWSER = "browser"
    SMART_HOME = "smart_home"
    CALENDAR = "calendar"
    FILE = "file"
    COMPUTER = "computer"


class AgentStatus(Enum):
    IDLE = "idle"
    WORKING = "working"
    WAITING_APPROVAL = "waiting_approval"
    ERROR = "error"
    COMPLETED = "completed"


@dataclass
class AgentTask:
    task_id: str
    agent_role: AgentRole
    description: str
    params: dict[str, Any] = field(default_factory=dict)
    status: AgentStatus = AgentStatus.IDLE
    result: Any = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    timeout: int = 300

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent_role": self.agent_role.value,
            "description": self.description,
            "params": self.params,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


@dataclass
class AgentResult:
    task_id: str
    success: bool
    data: Any = None
    error: str = ""
    agent_role: str = ""
    duration_ms: float = 0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "agent_role": self.agent_role,
            "duration_ms": self.duration_ms,
        }


class SubAgent:
    """A specialized sub-agent that handles one domain."""

    def __init__(self, role: AgentRole, handler: Optional[Callable] = None):
        self.role = role
        self.handler = handler
        self.status = AgentStatus.IDLE
        self._tasks_completed = 0
        self._errors = 0

    async def execute(self, task: AgentTask) -> AgentResult:
        self.status = AgentStatus.WORKING
        start = time.time()

        try:
            if self.handler:
                result = await self.handler(task)
            else:
                result = {"message": f"No handler for {self.role.value}"}

            duration = (time.time() - start) * 1000
            self._tasks_completed += 1
            self.status = AgentStatus.COMPLETED

            return AgentResult(
                task_id=task.task_id,
                success=True,
                data=result,
                agent_role=self.role.value,
                duration_ms=duration,
            )
        except Exception as e:
            duration = (time.time() - start) * 1000
            self._errors += 1
            self.status = AgentStatus.ERROR

            return AgentResult(
                task_id=task.task_id,
                success=False,
                error=str(e),
                agent_role=self.role.value,
                duration_ms=duration,
            )

    def stats(self) -> dict:
        return {
            "role": self.role.value,
            "status": self.status.value,
            "tasks_completed": self._tasks_completed,
            "errors": self._errors,
        }


class AgentOrchestrator:
    """Coordinates multiple sub-agents for complex tasks."""

    def __init__(self):
        self._agents: dict[AgentRole, SubAgent] = {}
        self._task_history: list[AgentResult] = []
        self._pending_tasks: dict[str, AgentTask] = {}

    def register_agent(self, role: AgentRole, handler: Optional[Callable] = None):
        self._agents[role] = SubAgent(role=role, handler=handler)

    def get_agent(self, role: AgentRole) -> Optional[SubAgent]:
        return self._agents.get(role)

    def create_task(self, role: AgentRole, description: str, params: Optional[dict] = None) -> AgentTask:
        task_id = str(uuid.uuid4())[:12]
        task = AgentTask(
            task_id=task_id,
            agent_role=role,
            description=description,
            params=params or {},
        )
        self._pending_tasks[task_id] = task
        return task

    async def execute_task(self, task: AgentTask) -> AgentResult:
        agent = self._agents.get(task.agent_role)
        if not agent:
            return AgentResult(
                task_id=task.task_id,
                success=False,
                error=f"No agent for role: {task.agent_role.value}",
            )

        result = await agent.execute(task)
        self._task_history.append(result)
        self._pending_tasks.pop(task.task_id, None)
        return result

    async def execute_parallel(self, tasks: list[AgentTask]) -> list[AgentResult]:
        results = await asyncio.gather(
            *[self.execute_task(t) for t in tasks],
            return_exceptions=True,
        )
        return [
            r if isinstance(r, AgentResult) else AgentResult(
                task_id="unknown", success=False, error=str(r)
            )
            for r in results
        ]

    def decompose_request(self, utterance: str) -> list[AgentTask]:
        tasks = []
        lower = utterance.lower()

        if any(w in lower for w in ["email", "mail", "inbox"]):
            tasks.append(self.create_task(AgentRole.EMAIL, utterance))

        if any(w in lower for w in ["open", "browse", "search", "website"]):
            tasks.append(self.create_task(AgentRole.BROWSER, utterance))

        if any(w in lower for w in ["light", "lamp", "thermostat", "lock", "fan"]):
            tasks.append(self.create_task(AgentRole.SMART_HOME, utterance))

        if any(w in lower for w in ["meeting", "calendar", "schedule", "event"]):
            tasks.append(self.create_task(AgentRole.CALENDAR, utterance))

        if any(w in lower for w in ["file", "folder", "document", "save"]):
            tasks.append(self.create_task(AgentRole.FILE, utterance))

        if any(w in lower for w in ["screenshot", "click", "type", "screen"]):
            tasks.append(self.create_task(AgentRole.COMPUTER, utterance))

        if not tasks:
            tasks.append(self.create_task(AgentRole.ORCHESTRATOR, utterance))

        return tasks

    def get_history(self, limit: int = 50) -> list[AgentResult]:
        return self._task_history[-limit:]

    def get_stats(self) -> dict:
        stats = {}
        for role, agent in self._agents.items():
            stats[role.value] = agent.stats()
        return stats

    def clear_history(self):
        self._task_history.clear()
