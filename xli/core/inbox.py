#!/usr/bin/env python3
"""
XLI Inbox - JSONL-based agent communication (like OpenCode)
Agents coordinate through team_inbox/<project>/<team>/<agent>.jsonl
"""

import json
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime

from xli.core.logger import StructuredLogger

logger = StructuredLogger("xli.inbox")


@dataclass
class InboxMessage:
    """Message between agents"""
    id: str
    from_agent: str
    to_agent: str
    text: str
    timestamp: str
    team: str = "default"
    project: str = "default"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "from": self.from_agent,
            "to": self.to_agent,
            "text": self.text,
            "timestamp": self.timestamp,
            "team": self.team,
            "project": self.project
        }


class TeamInbox:
    """Multi-agent communication hub"""

    def __init__(self, project: str = "default", team: str = "default"):
        self.project = project
        self.team = team
        self.base_dir = Path.home() / ".xli" / "team_inbox" / project / team
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._callbacks: Dict[str, List[callable]] = {}
        self._watches: Dict[str, asyncio.Task] = {}

    def _get_inbox_path(self, agent: str) -> Path:
        """Get inbox file for agent"""
        return self.base_dir / f"{agent}.jsonl"

    async def send(self, from_agent: str, to_agent: str, text: str) -> InboxMessage:
        """Send message to another agent"""
        msg = InboxMessage(
            id=f"{from_agent}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
            from_agent=from_agent,
            to_agent=to_agent,
            text=text,
            timestamp=datetime.now().isoformat(),
            team=self.team,
            project=self.project
        )

        # Write to inbox
        inbox_path = self._get_inbox_path(to_agent)
        with open(inbox_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")

        logger.log_structured("INFO", "inbox", 
                             f"Message from {from_agent} to {to_agent}",
                             {"text": text[:100]})

        # Trigger callbacks
        if to_agent in self._callbacks:
            for cb in self._callbacks[to_agent]:
                try:
                    await cb(msg)
                except Exception as e:
                    logger.log_error("inbox", "Callback failed", exc=e)

        return msg

    async def broadcast(self, from_agent: str, text: str, exclude: List[str] = None):
        """Broadcast message to all agents"""
        exclude = exclude or []

        # Find all agents
        agents = []
        for f in self.base_dir.glob("*.jsonl"):
            agent_name = f.stem
            if agent_name != from_agent and agent_name not in exclude:
                agents.append(agent_name)

        # Send to all
        for agent in agents:
            await self.send(from_agent, agent, text)

    def read_messages(self, agent: str, since: str = None, limit: int = 50) -> List[InboxMessage]:
        """Read messages for agent"""
        inbox_path = self._get_inbox_path(agent)
        if not inbox_path.exists():
            return []

        messages = []
        try:
            with open(inbox_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if since and data.get("timestamp", "") < since:
                            continue
                        messages.append(InboxMessage(**data))
                    except:
                        pass
        except:
            pass

        return messages[-limit:]

    def register_callback(self, agent: str, callback: callable):
        """Register callback for incoming messages"""
        if agent not in self._callbacks:
            self._callbacks[agent] = []
        self._callbacks[agent].append(callback)

    def unregister_callback(self, agent: str, callback: callable):
        """Unregister callback"""
        if agent in self._callbacks:
            self._callbacks[agent] = [cb for cb in self._callbacks[agent] if cb != callback]

    async def watch(self, agent: str, callback: callable, poll_interval: float = 1.0):
        """Watch inbox for new messages (polling for compatibility)"""
        last_count = 0

        while True:
            messages = self.read_messages(agent)
            if len(messages) > last_count:
                new_msgs = messages[last_count:]
                for msg in new_msgs:
                    await callback(msg)
                last_count = len(messages)

            await asyncio.sleep(poll_interval)

    def clear_inbox(self, agent: str):
        """Clear agent's inbox"""
        inbox_path = self._get_inbox_path(agent)
        if inbox_path.exists():
            inbox_path.unlink()


class AgentCoordinator:
    """High-level agent coordination"""

    def __init__(self, inbox: TeamInbox = None):
        self.inbox = inbox or TeamInbox()
        self.agents: Dict[str, Any] = {}
        self.active_sessions: Dict[str, bool] = {}

    def register_agent(self, agent_id: str, agent_instance: Any):
        """Register agent for coordination"""
        self.agents[agent_id] = agent_instance
        self.active_sessions[agent_id] = True

        # Register callback
        self.inbox.register_callback(agent_id, self._on_message)

    async def _on_message(self, msg: InboxMessage):
        """Handle incoming message"""
        logger.log_structured("INFO", "coordinator",
                             f"{msg.to_agent} received message from {msg.from_agent}")

        # Auto-wake if agent is idle
        if msg.to_agent in self.agents:
            if not self.active_sessions.get(msg.to_agent, False):
                await self.wake_agent(msg.to_agent, msg)

    async def wake_agent(self, agent_id: str, msg: InboxMessage):
        """Wake up idle agent with message"""
        logger.log_structured("INFO", "coordinator", f"Waking {agent_id}")
        self.active_sessions[agent_id] = True

        # Inject message into agent's context
        agent = self.agents.get(agent_id)
        if agent:
            # Add as synthetic user message
            synthetic = f"[MESSAGE FROM {msg.from_agent}]: {msg.text}"
            if hasattr(agent, 'history'):
                agent.history.append({"role": "user", "content": synthetic})

            # Trigger agent to process
            if hasattr(agent, 'think'):
                try:
                    response = await agent.think(synthetic)
                    # Send response back
                    await self.inbox.send(agent_id, msg.from_agent, response)
                except Exception as e:
                    logger.log_error("coordinator", f"Wake failed for {agent_id}", exc=e)

        self.active_sessions[agent_id] = False

    async def delegate_task(self, from_agent: str, to_agent: str, task: str) -> str:
        """Delegate task to another agent and wait for response"""
        # Send task
        await self.inbox.send(from_agent, to_agent, f"TASK: {task}")

        # Wait for response (with timeout)
        start_time = asyncio.get_event_loop().time()
        timeout = 120  # 2 minutes

        while True:
            messages = self.inbox.read_messages(from_agent)
            for msg in reversed(messages):
                if msg.from_agent == to_agent and "TASK:" not in msg.text:
                    return msg.text

            if asyncio.get_event_loop().time() - start_time > timeout:
                return "[TIMEOUT: No response from agent]"

            await asyncio.sleep(1)

    def get_team_status(self) -> Dict[str, Any]:
        """Get status of all agents"""
        return {
            agent_id: {
                "active": self.active_sessions.get(agent_id, False),
                "message_count": len(self.inbox.read_messages(agent_id))
            }
            for agent_id in self.agents
        }
