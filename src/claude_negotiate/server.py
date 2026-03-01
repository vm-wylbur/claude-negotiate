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
    peer_id: str,
    context: str,
    artifact_path: str,
    max_rounds: int = 10,
) -> dict:
    """Start a new negotiation between two agents.

    context should include: your constraints, initial position, and relevant paths.
    artifact_path is where the agreed output file will be written on close.
    Returns negotiation_id — share this with your peer so they can join.
    """
    neg_id = await _store.open_negotiation(
        topic=topic,
        initiator_id=initiator_id,
        peer_id=peer_id,
        context=context,
        artifact_path=artifact_path,
        max_rounds=max_rounds,
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
      proposing  — new proposal
      counter    — counter-proposal responding to peer's last turn
      accepting  — accept a specific proposal; requires accepting_hash
      blocked    — you cannot proceed without human input; explain why in content

    accepting_hash: the content_hash from the turn you are accepting,
    as returned by read_latest. Required when status='accepting'.

    Returns content_hash of your post (for your peer to reference),
    and converged=True if both agents have accepted the same hash.
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

    Returns turns list, last_id (pass back next time), and status flags:
    converged, impasse, blocked.
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

    Returns the same shape as read_latest. If timed_out=True, no new turns
    arrived — call again to keep waiting. If converged or impasse, stop looping.

    Autonomous loop pattern:
        result = read_latest(neg_id, my_id, since_id="0")
        last_id = result["last_id"]
        while not result["converged"] and not result["impasse"]:
            # read and reason about result["turns"], then post your response
            post_position(neg_id, my_id, my_response, status)
            result = wait_for_turn(neg_id, my_id, since_id=last_id)
            last_id = result["last_id"]
        if result["converged"]:
            close_negotiation(neg_id, my_id, final_artifact)
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
    final_artifact: str,
) -> dict:
    """Close a converged negotiation and write the agreed artifact to disk.

    Only callable after convergence (both agents accepted the same hash).
    Idempotent: if already closed, returns status='already_closed'.
    final_artifact is the agreed content written to artifact_path.
    """
    return await _store.close_negotiation(
        neg_id=negotiation_id,
        agent_id=agent_id,
        final_artifact=final_artifact,
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="claude-negotiate MCP server")
    parser.add_argument("--port", type=int, default=7832)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
