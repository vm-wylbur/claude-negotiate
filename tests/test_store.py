# Author: PB and Claude
# Date: 2026-02-28
# License: (c) Patrick Ball, 2026, GPL-2 or newer
#
# claude-negotiate/tests/test_store.py

import os

import pytest
import pytest_asyncio

from claude_negotiate.store import NegotiationStore

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@pytest_asyncio.fixture
async def store():
    s = NegotiationStore(REDIS_URL)
    await s.connect()
    yield s
    await s.disconnect()


async def test_full_negotiation(store):
    """Two agents open a negotiation, propose, counter, both accept, converge, close."""
    neg_id = await store.open_negotiation(
        topic="test ACL scheme",
        initiator_id="cc-ntx",
        peer_id="cc-tfcs",
        context="ntx needs read access to /data/shared",
        artifact_path="/tmp/claude-negotiate-test-acl.md",
    )
    assert neg_id.startswith("neg-")

    # cc-ntx proposes
    r1 = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-ntx",
        content="Proposal: setfacl -m u:tfcs-user:r-x /data/shared",
        status="proposing",
    )
    assert not r1["converged"]

    # cc-tfcs reads and counters
    read1 = await store.read_latest(neg_id, "cc-tfcs", since_id="0")
    assert any(t["agent_id"] == "cc-ntx" for t in read1["turns"])

    r2 = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-tfcs",
        content="Counter: need recursive r-x: setfacl -R -m u:tfcs-user:r-x /data/shared",
        status="counter",
    )
    counter_hash = r2["content_hash"]
    assert not r2["converged"]

    # cc-ntx reads counter and accepts it
    read2 = await store.read_latest(neg_id, "cc-ntx", since_id=read1["last_id"])
    counter_turn = next(t for t in read2["turns"] if t["agent_id"] == "cc-tfcs")

    r3 = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-ntx",
        content="Accepted",
        status="accepting",
        accepting_hash=counter_turn["content_hash"],
    )
    assert not r3["converged"]  # only one agent accepted so far

    # cc-tfcs also accepts its own counter (confirming the agreed text)
    r4 = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-tfcs",
        content="Confirmed",
        status="accepting",
        accepting_hash=counter_hash,
    )
    assert r4["converged"]

    # Verify status
    status = await store.get_status(neg_id)
    assert status["status"] == "converged"
    assert status["converged_hash"] == counter_hash

    # Close
    close_result = await store.close_negotiation(
        neg_id=neg_id,
        agent_id="cc-ntx",
        final_artifact="# Agreed ACL\n\nsetfacl -R -m u:tfcs-user:r-x /data/shared\n",
    )
    assert close_result["status"] == "closed"

    # Idempotent close — second call should not raise, just report already_closed
    close_again = await store.close_negotiation(
        neg_id=neg_id,
        agent_id="cc-tfcs",
        final_artifact="should not overwrite",
    )
    assert close_again["status"] == "already_closed"

    # Artifact written by first close should be intact
    from pathlib import Path
    assert Path("/tmp/claude-negotiate-test-acl.md").read_text().startswith("# Agreed ACL")


async def test_human_inject_visible_to_agents(store):
    neg_id = await store.open_negotiation(
        topic="inject test",
        initiator_id="cc-a",
        peer_id="cc-b",
        context="initial context",
        artifact_path="/tmp/claude-negotiate-test-inject.md",
    )

    result = await store.human_inject(neg_id, "Human says: focus on security")
    assert result["acknowledged"]

    transcript = await store.get_transcript(neg_id)
    human_turns = [t for t in transcript["turns"] if t["agent_id"] == "human"]
    assert len(human_turns) == 1
    assert "security" in human_turns[0]["content"]

    # Both agents see it in read_latest
    read = await store.read_latest(neg_id, "cc-a", since_id="0")
    assert any(t["agent_id"] == "human" for t in read["turns"])


async def test_list_negotiations_both_agents(store):
    neg_id = await store.open_negotiation(
        topic="listing test",
        initiator_id="cc-x",
        peer_id="cc-y",
        context="ctx",
        artifact_path="/tmp/claude-negotiate-test-list.md",
    )

    for agent_id in ("cc-x", "cc-y"):
        result = await store.list_negotiations(agent_id)
        ids = [n["negotiation_id"] for n in result["negotiations"]]
        assert neg_id in ids


async def test_blocked_and_resume(store):
    neg_id = await store.open_negotiation(
        topic="blocking test",
        initiator_id="cc-p",
        peer_id="cc-q",
        context="ctx",
        artifact_path="/tmp/claude-negotiate-test-blocked.md",
    )

    # cc-p blocks
    r = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-p",
        content="Blocked: cannot verify uid range without /etc/passwd access",
        status="blocked",
    )
    assert r["blocked"]

    status = await store.get_status(neg_id)
    assert status["status"] == "blocked"
    assert status["blocked_by"] == "cc-p"

    # cc-p resumes with new info
    r2 = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-p",
        content="Resumed: uid range confirmed as 1000-65534",
        status="proposing",
    )
    assert not r2["blocked"]

    status2 = await store.get_status(neg_id)
    assert status2["status"] == "open"


async def test_update_context_visible_in_stream(store):
    neg_id = await store.open_negotiation(
        topic="context update test",
        initiator_id="cc-m",
        peer_id="cc-n",
        context="initial",
        artifact_path="/tmp/claude-negotiate-test-ctx.md",
    )

    await store.update_context(
        neg_id=neg_id,
        agent_id="cc-m",
        additional_context="NFS mount discovered: POSIX ACLs won't work",
    )

    read = await store.read_latest(neg_id, "cc-n", since_id="0")
    ctx_turns = [t for t in read["turns"] if t["status"] == "context_update"]
    assert len(ctx_turns) == 1
    assert "NFS" in ctx_turns[0]["content"]


async def test_cannot_post_to_closed_negotiation(store):
    neg_id = await store.open_negotiation(
        topic="closed post test",
        initiator_id="cc-i",
        peer_id="cc-j",
        context="ctx",
        artifact_path="/tmp/claude-negotiate-test-closed.md",
    )

    # Converge quickly
    r1 = await store.post_position(neg_id, "cc-i", "proposal", "proposing")
    h = r1["content_hash"]
    await store.post_position(neg_id, "cc-i", "accept", "accepting", accepting_hash=h)
    r2 = await store.post_position(neg_id, "cc-j", "accept", "accepting", accepting_hash=h)
    assert r2["converged"]

    await store.close_negotiation(neg_id, "cc-i", "final text")

    with pytest.raises(ValueError, match="closed"):
        await store.post_position(neg_id, "cc-j", "too late", "proposing")
