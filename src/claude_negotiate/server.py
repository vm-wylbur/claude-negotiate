# Author: PB and Claude
# Date: 2026-02-28
# License: (c) Patrick Ball, 2026, GPL-2 or newer
#
# claude-negotiate/src/claude_negotiate/server.py

import os
from contextlib import asynccontextmanager
from typing import Literal

from fastmcp import FastMCP

from claude_negotiate.store import NegotiationStore

_store: NegotiationStore | None = None


@asynccontextmanager
async def lifespan(mcp: FastMCP):
    global _store
    redis_url = os.environ["REDIS_URL"]
    _store = NegotiationStore(redis_url)
    await _store.connect()
    yield
    await _store.disconnect()


mcp = FastMCP("claude-negotiate", lifespan=lifespan)


@mcp.tool()
async def open_negotiation(
    topic: str,
    initiator_id: str,
    participants: list[str],
    context: str,
    max_rounds: int = 10,
    references: list[str] | None = None,
    require_human_approval: bool = False,
) -> dict:
    """Start a new negotiation between N agents.

    participants: list of all non-initiator agent IDs (≥1 required).
      2-party: participants=["cc-hmon"]
      3-party: participants=["cc-hmon", "cc-ansible"]
    initiator_id is NOT included in participants — it is stored separately.

    context should include: your constraints, initial position, and relevant paths.
    The artifact is written to /var/lib/claude-negotiate/{neg_id}.md on the server.
    Returns negotiation_id — share this with all participants so they can join.

    Convergence requires ALL participants (including initiator) to accept the same hash.

    references: optional list of prior negotiation IDs this negotiation builds on.
    Stored and returned by get_status, join_negotiation, and list_negotiations.

    require_human_approval: if True, close_negotiation returns status='pending_human_approval'
    and shows the converged artifact preview until the human calls human_inject with content
    containing 'approve'. Use when the human must sign off before the artifact is written.
    """
    neg_id = await _store.open_negotiation(
        topic=topic,
        initiator_id=initiator_id,
        participants=participants,
        context=context,
        max_rounds=max_rounds,
        references=references,
        require_human_approval=require_human_approval,
    )
    return {"negotiation_id": neg_id, "status": "open"}


@mcp.tool()
async def post_position(
    negotiation_id: str,
    agent_id: str,
    content: str,
    status: Literal["proposing", "accepting", "counter", "blocked"],
    accepting_hash: str | None = None,
) -> dict:
    """Post a turn in a negotiation.

    status values:
      proposing  — new proposal (auto-accepted by you; peer sees it and can accept)
      counter    — counter-proposal responding to peer's last turn (also auto-accepted by you)
      accepting  — accept a specific proposal; requires accepting_hash
      blocked    — you cannot proceed without human input; explain why in content

    accepting_hash: the content_hash from the turn you are accepting,
    as returned by read_latest. Required when status='accepting'.

    Returns content_hash of your post (for your peer to reference),
    converged=True if both agents have accepted the same hash,
    turns_used and max_turns for round-count awareness.
    """
    return await _store.post_position(
        neg_id=negotiation_id,
        agent_id=agent_id,
        content=content,
        status=status,
        accepting_hash=accepting_hash,
    )


@mcp.tool()
async def read_latest(
    negotiation_id: str,
    agent_id: str,
    since_id: str = "0",
) -> dict:
    """Read turns since the last seen stream entry ID.

    On first call use since_id='0' to get the full history.
    On subsequent calls pass the last_id from the previous response
    to get only new turns.

    Returns turns list, last_id (pass back next time), status flags
    (converged, impasse, blocked), and round count (turns_used, max_turns).
    """
    return await _store.read_latest(
        neg_id=negotiation_id,
        agent_id=agent_id,
        since_id=since_id,
    )


@mcp.tool()
async def wait_for_turn(
    negotiation_id: str,
    agent_id: str,
    since_id: str,
    timeout_seconds: int = 60,
) -> dict:
    """Block until the peer posts a new turn, then return it.

    Pass the last_id from your previous read_latest or wait_for_turn call.
    Blocks on the server side (Redis XREAD BLOCK) — no polling needed.

    Returns the same shape as read_latest, including turns_used and max_turns.
    If timed_out=True, no new turns arrived — call again to keep waiting.
    If converged or impasse, stop looping.

    Autonomous loop pattern:
        result = read_latest(neg_id, my_id, since_id="0")
        while not result["converged"] and not result["impasse"]:
            # read and reason about result["turns"], then post your response
            post = post_position(neg_id, my_id, my_response, status)
            result = wait_for_turn(neg_id, my_id, since_id=post["entry_id"])
        if result["converged"]:
            close_negotiation(neg_id, my_id)
    """
    return await _store.wait_for_turn(
        neg_id=negotiation_id,
        agent_id=agent_id,
        since_id=since_id,
        timeout_seconds=timeout_seconds,
    )


@mcp.tool()
async def update_context(
    negotiation_id: str,
    agent_id: str,
    additional_context: str,
) -> dict:
    """Add a context update mid-negotiation without consuming a round.

    Use this when you discover a new constraint (e.g. filesystem type,
    permission boundary, dependency version) that your peer needs to know
    before they can make a valid proposal.
    """
    return await _store.update_context(
        neg_id=negotiation_id,
        agent_id=agent_id,
        additional_context=additional_context,
    )


@mcp.tool()
async def get_status(negotiation_id: str) -> dict:
    """Get the current state of a negotiation."""
    return await _store.get_status(neg_id=negotiation_id)


@mcp.tool()
async def list_negotiations(agent_id: str) -> dict:
    """List all negotiations this agent is participating in.

    Use at session start to find pending negotiations:
      list_negotiations(agent_id='cc-ntx')
    Then call get_status on any open ones and respond.
    """
    return await _store.list_negotiations(agent_id=agent_id)


@mcp.tool()
async def get_transcript(negotiation_id: str) -> dict:
    """Get the full turn-by-turn transcript of a negotiation.

    Intended for human review. Returns all turns in order with
    agent_id, status, content, and content_hash for each.
    """
    return await _store.get_transcript(neg_id=negotiation_id)


@mcp.tool()
async def human_inject(negotiation_id: str, content: str) -> dict:
    """Inject a human message into a live negotiation.

    Both agents will see this as a 'human_inject' turn on their
    next read_latest call. Use to redirect, correct, or unblock.
    """
    return await _store.human_inject(neg_id=negotiation_id, content=content)


@mcp.tool()
async def close_negotiation(
    negotiation_id: str,
    agent_id: str,
    final_artifact: str | None = None,
    artifact_name: str | None = None,
) -> dict:
    """Close a converged negotiation and write the agreed artifact to disk.

    Only callable after convergence (both agents accepted the same hash).
    Idempotent: if already closed, returns status='already_closed'.

    final_artifact: the agreed content (clean, no preamble). If omitted,
    the server auto-fills from the converged turn's raw content.

    artifact_name: a human-readable filename, e.g. 'tfc-hmon-log-retention-20260301.md'.
    Written to /var/lib/claude-negotiate/{artifact_name}. If omitted, uses {neg_id}.md.

    The server always appends a provenance footer (agreed-by, neg_id, date).
    Returns artifact_content (including footer) so you can confirm what was written.
    """
    return await _store.close_negotiation(
        neg_id=negotiation_id,
        agent_id=agent_id,
        final_artifact=final_artifact,
        artifact_name=artifact_name,
    )


@mcp.tool()
async def get_artifact(negotiation_id: str) -> dict:
    """Read the agreed artifact from the server filesystem.

    Works even when you're on a different host than the server.
    Returns available=True and content if the artifact has been written,
    available=False with a reason if not yet available.
    """
    return await _store.get_artifact(neg_id=negotiation_id)


@mcp.tool()
async def join_negotiation(negotiation_id: str, agent_id: str) -> dict:
    """Join an existing negotiation.

    Returns your role (initiator/peer), full transcript, and last_id to pass
    to wait_for_turn. Use this instead of get_status when joining mid-flight.

    Provides: role, topic, negotiation_status, turns_used, max_turns,
    converged_hash, last_id, and the complete turns list.
    """
    return await _store.join_negotiation(neg_id=negotiation_id, agent_id=agent_id)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="claude-negotiate MCP server")
    parser.add_argument("--port", type=int, default=7832)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
