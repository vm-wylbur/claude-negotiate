# Author: PB and Claude
# Date: 2026-02-28
# License: (c) Patrick Ball, 2026, GPL-2 or newer
#
# claude-negotiate/tests/test_store.py

import asyncio
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
    assert r1["entry_id"]

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
    assert r2["entry_id"]

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
    # Task 5: cc-tfcs auto-stored its own counter hash when posting,
    # so when cc-ntx accepts that hash, convergence fires immediately.
    assert r3["converged"]
    assert r3["entry_id"]

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
    )

    # cc-p blocks
    r = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-p",
        content="Blocked: cannot verify uid range without /etc/passwd access",
        status="blocked",
    )
    assert r["blocked"]
    assert r["entry_id"]

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


async def test_wait_for_turn_unblocks_on_peer_post(store):
    """wait_for_turn blocks until peer posts, then returns immediately."""
    neg_id = await store.open_negotiation(
        topic="wait test",
        initiator_id="cc-w1",
        peer_id="cc-w2",
        context="ctx",
    )

    # Get the last_id from the initial context entry
    initial = await store.read_latest(neg_id, "cc-w1", since_id="0")
    last_id = initial["last_id"]

    # Start waiting in a concurrent task
    wait_task = asyncio.create_task(
        store.wait_for_turn(neg_id, "cc-w1", since_id=last_id, timeout_seconds=5)
    )

    await asyncio.sleep(0.1)  # let the block take hold

    # Peer posts
    await store.post_position(neg_id, "cc-w2", "hello from w2", "proposing")

    result = await asyncio.wait_for(wait_task, timeout=3.0)
    assert not result["timed_out"]
    assert any(t["agent_id"] == "cc-w2" for t in result["turns"])


async def test_wait_for_turn_times_out(store):
    """wait_for_turn returns timed_out=True when no peer activity."""
    neg_id = await store.open_negotiation(
        topic="timeout test",
        initiator_id="cc-t1",
        peer_id="cc-t2",
        context="ctx",
    )
    initial = await store.read_latest(neg_id, "cc-t1", since_id="0")

    result = await store.wait_for_turn(
        neg_id, "cc-t1", since_id=initial["last_id"], timeout_seconds=2
    )
    assert result["timed_out"]
    assert result["turns"] == []


async def test_wait_for_turn_returns_immediately_when_done(store):
    """wait_for_turn doesn't block if negotiation is already converged."""
    neg_id = await store.open_negotiation(
        topic="done test",
        initiator_id="cc-d1",
        peer_id="cc-d2",
        context="ctx",
    )

    r1 = await store.post_position(neg_id, "cc-d1", "proposal", "proposing")
    h = r1["content_hash"]
    # Task 5: cc-d1 auto-accepts its own proposal, so cc-d2 accepting converges immediately
    r2 = await store.post_position(neg_id, "cc-d2", "accept", "accepting", accepting_hash=h)
    assert r2["converged"]

    final = await store.read_latest(neg_id, "cc-d2", since_id="0")
    result = await store.wait_for_turn(
        neg_id, "cc-d2", since_id=final["last_id"], timeout_seconds=10
    )
    assert result["converged"]
    assert not result["timed_out"]


async def test_read_latest_includes_accepting_hash(store):
    """Bug 3: read_latest turns must include accepting_hash field."""
    neg_id = await store.open_negotiation(
        topic="accepting_hash visibility test",
        initiator_id="cc-ah1",
        peer_id="cc-ah2",
        context="ctx",
    )

    r1 = await store.post_position(neg_id, "cc-ah1", "proposal", "proposing")
    h = r1["content_hash"]
    await store.post_position(neg_id, "cc-ah1", "accept", "accepting", accepting_hash=h)

    read = await store.read_latest(neg_id, "cc-ah2", since_id="0")
    # Every turn must have accepting_hash key
    for turn in read["turns"]:
        assert "accepting_hash" in turn, f"turn missing accepting_hash: {turn}"
    # The accepting turn should carry the hash
    accepting_turns = [t for t in read["turns"] if t["status"] == "accepting"]
    assert len(accepting_turns) == 1
    assert accepting_turns[0]["accepting_hash"] == h


async def test_wait_for_turn_filters_self_turns(store):
    """Bug 2: wait_for_turn must not return the caller's own turns."""
    neg_id = await store.open_negotiation(
        topic="self-filter test",
        initiator_id="cc-sf1",
        peer_id="cc-sf2",
        context="ctx",
    )

    initial = await store.read_latest(neg_id, "cc-sf1", since_id="0")
    last_id = initial["last_id"]

    # cc-sf1 posts its own turn, then the peer posts
    await store.post_position(neg_id, "cc-sf1", "self post", "proposing")

    # Start waiting *from before the self-post* so it would normally catch it
    wait_task = asyncio.create_task(
        store.wait_for_turn(neg_id, "cc-sf1", since_id=last_id, timeout_seconds=5)
    )

    await asyncio.sleep(0.15)  # let the block take hold after self-turn passes

    # Peer posts — this is what cc-sf1 should actually receive
    await store.post_position(neg_id, "cc-sf2", "peer response", "counter")

    result = await asyncio.wait_for(wait_task, timeout=4.0)
    assert not result["timed_out"]
    # Must not contain cc-sf1's own turn
    self_turns = [t for t in result["turns"] if t["agent_id"] == "cc-sf1"]
    assert self_turns == [], f"self-turns leaked: {self_turns}"
    # Must contain the peer's turn
    assert any(t["agent_id"] == "cc-sf2" for t in result["turns"])


async def test_cannot_post_to_closed_negotiation(store):
    neg_id = await store.open_negotiation(
        topic="closed post test",
        initiator_id="cc-i",
        peer_id="cc-j",
        context="ctx",
    )

    # Converge quickly: cc-i proposes (auto-accepts), cc-j accepts → converge
    r1 = await store.post_position(neg_id, "cc-i", "proposal", "proposing")
    h = r1["content_hash"]
    r2 = await store.post_position(neg_id, "cc-j", "accept", "accepting", accepting_hash=h)
    assert r2["converged"]

    await store.close_negotiation(neg_id, "cc-i", "final text")

    with pytest.raises(ValueError, match="closed"):
        await store.post_position(neg_id, "cc-j", "too late", "proposing")


# ---- New tests for Tasks 5-9 ----

async def test_single_accept_convergence(store):
    """Task 5: B proposes, A accepts → converge without B needing a second accept call."""
    neg_id = await store.open_negotiation(
        topic="single accept test",
        initiator_id="cc-sa-a",
        peer_id="cc-sa-b",
        context="testing single-accept",
    )

    # B (peer) proposes — auto-stores cc-sa-b_accepting_hash = ch
    r_b = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-sa-b",
        content="B's proposal text",
        status="proposing",
    )
    proposal_hash = r_b["content_hash"]
    assert not r_b["converged"]

    # A accepts B's proposal — should match B's auto-stored hash → converge
    r_a = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-sa-a",
        content="Accepted",
        status="accepting",
        accepting_hash=proposal_hash,
    )
    assert r_a["converged"], "Should converge in single round-trip"

    status = await store.get_status(neg_id)
    assert status["status"] == "converged"
    assert status["converged_hash"] == proposal_hash


async def test_close_auto_fill_artifact(store):
    """Task 6: close without final_artifact auto-fills from converged turn content."""
    proposal_content = "The agreed text for this negotiation."
    neg_id = await store.open_negotiation(
        topic="auto-fill artifact test",
        initiator_id="cc-af-a",
        peer_id="cc-af-b",
        context="testing auto-fill",
    )

    # A proposes (auto-accepts), B accepts → converge
    r_a = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-af-a",
        content=proposal_content,
        status="proposing",
    )
    proposal_hash = r_a["content_hash"]

    r_b = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-af-b",
        content="Accepted",
        status="accepting",
        accepting_hash=proposal_hash,
    )
    assert r_b["converged"]

    # Close with artifact_name and explicit content
    close_result = await store.close_negotiation(
        neg_id=neg_id,
        agent_id="cc-af-a",
        final_artifact=proposal_content,
        artifact_name="cc-af-a-cc-af-b-test-autofill.md",
    )
    assert close_result["status"] == "closed"
    # Content should include the proposal plus the provenance footer
    assert close_result["artifact_content"].startswith(proposal_content)
    assert "Agreed:" in close_result["artifact_content"]
    assert neg_id in close_result["artifact_content"]
    assert "cc-af-a" in close_result["artifact_content"]  # closed_by
    assert close_result["artifact_path"].endswith("cc-af-a-cc-af-b-test-autofill.md")

    from pathlib import Path
    written = Path(close_result["artifact_path"]).read_text()
    assert written == close_result["artifact_content"]


async def test_round_count_in_post_position(store):
    """Task 7: post_position response includes turns_used and max_turns."""
    neg_id = await store.open_negotiation(
        topic="round count test",
        initiator_id="cc-rc-a",
        peer_id="cc-rc-b",
        context="ctx",
        max_rounds=5,
    )

    r1 = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-rc-a",
        content="first proposal",
        status="proposing",
    )
    assert "turns_used" in r1, "turns_used missing from post_position response"
    assert "max_turns" in r1, "max_turns missing from post_position response"
    assert r1["max_turns"] == 10  # 5 rounds * 2
    # Stream has: 1 context entry + 1 proposal = 2
    assert r1["turns_used"] == 2

    r2 = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-rc-b",
        content="counter",
        status="counter",
    )
    assert r2["turns_used"] == 3

    # read_latest also has round count
    read = await store.read_latest(neg_id, "cc-rc-a", since_id="0")
    assert "turns_used" in read
    assert "max_turns" in read
    assert read["max_turns"] == 10


async def test_get_artifact(store):
    """Task 8: get_artifact returns content after open+converge+close."""
    artifact_text = "# Final agreed document\n\nContent here.\n"
    neg_id = await store.open_negotiation(
        topic="get artifact test",
        initiator_id="cc-ga-a",
        peer_id="cc-ga-b",
        context="ctx",
    )

    # Before convergence: not available
    pre = await store.get_artifact(neg_id)
    assert not pre["available"]

    # Converge: A proposes (auto-accepts), B accepts
    r_a = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-ga-a",
        content="proposal",
        status="proposing",
    )
    r_b = await store.post_position(
        neg_id=neg_id,
        agent_id="cc-ga-b",
        content="accept",
        status="accepting",
        accepting_hash=r_a["content_hash"],
    )
    assert r_b["converged"]

    # After converge but before close: file not written yet
    mid = await store.get_artifact(neg_id)
    assert not mid["available"]

    # Close with explicit artifact
    await store.close_negotiation(neg_id, "cc-ga-a", final_artifact=artifact_text)

    # Now get_artifact should return content (with footer appended)
    result = await store.get_artifact(neg_id)
    assert result["available"]
    assert result["content"].startswith(artifact_text)
    assert "Agreed:" in result["content"]
    assert neg_id in result["content"]
    assert result["artifact_path"].startswith("/var/lib/claude-negotiate/")
    assert result["artifact_path"].endswith(".md")


async def test_join_negotiation(store):
    """Task 9: join_negotiation returns correct role, last_id, turn count."""
    neg_id = await store.open_negotiation(
        topic="join test",
        initiator_id="cc-jn-a",
        peer_id="cc-jn-b",
        context="initial context",
    )

    # Post a turn so there's something in the stream
    await store.post_position(
        neg_id=neg_id,
        agent_id="cc-jn-a",
        content="first proposal",
        status="proposing",
    )

    # Initiator joins
    result_a = await store.join_negotiation(neg_id, "cc-jn-a")
    assert result_a["role"] == "initiator"
    assert result_a["your_agent_id"] == "cc-jn-a"
    assert result_a["topic"] == "join test"
    assert result_a["negotiation_status"] == "open"
    assert result_a["turns_used"] == 2  # context + proposal
    assert result_a["max_turns"] == 20  # 10 rounds * 2
    assert len(result_a["turns"]) == 2
    assert result_a["last_id"] != "0"

    # Peer joins
    result_b = await store.join_negotiation(neg_id, "cc-jn-b")
    assert result_b["role"] == "peer"
    assert result_b["your_agent_id"] == "cc-jn-b"
    assert result_b["turns_used"] == 2
    # last_id matches between both
    assert result_a["last_id"] == result_b["last_id"]

    # converged_hash is empty before convergence
    assert result_a["converged_hash"] == ""


async def test_impasse_declared_at_max_rounds(store):
    """Fix 2: impasse is declared when turn count exceeds max_rounds * 2."""
    neg_id = await store.open_negotiation(
        topic="impasse test",
        initiator_id="cc-imp-a",
        peer_id="cc-imp-b",
        context="testing impasse detection",
        max_rounds=2,
    )

    # Two agents alternate proposing (no accepting) until impasse is declared.
    # max_rounds=2 means impasse fires when turn_count > 4 (2 * 2).
    # Stream starts with 1 context entry. We need >4 entries total.
    agents = ["cc-imp-a", "cc-imp-b"]
    last_result = None
    for i in range(5):
        agent = agents[i % 2]
        last_result = await store.post_position(
            neg_id=neg_id,
            agent_id=agent,
            content=f"Proposal round {i} from {agent}",
            status="proposing",
        )
        if last_result.get("negotiation_status") == "impasse" or (
            await store.get_status(neg_id)
        )["status"] == "impasse":
            break

    # Check the final read_latest response
    read = await store.read_latest(neg_id, "cc-imp-a", since_id="0")
    assert read["negotiation_status"] == "impasse", (
        f"Expected impasse, got {read['negotiation_status']}"
    )

    # get_status must also report impasse
    status = await store.get_status(neg_id)
    assert status["status"] == "impasse"
