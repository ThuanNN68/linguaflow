"""The Assistant Agent (B-11): plan, execute, and stop for a human.

The second agent in the system, running alongside the Translation Agent and
sharing its infrastructure but nothing of its flow. Translation is automatic,
synchronous and delivered to everyone; this one is invoked on request, may take
seconds, and produces rows a person approves before anything happens.

`docs/architecture/assistant-admin.md` §1.1 draws the split; ADR-32 records why the human gate
uses LangGraph's `interrupt()` while `action_proposals` remains the durable
record of what is waiting.
"""

from src.agents.assistant.graph import build_assistant_graph
from src.agents.assistant.state import AssistantState, PlannedStep

__all__ = ["AssistantState", "PlannedStep", "build_assistant_graph"]
