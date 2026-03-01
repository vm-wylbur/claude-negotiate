# Author: PB and Claude
# Date: 2026-02-28
# License: (c) Patrick Ball, 2026, GPL-2 or newer
#
# claude-negotiate/src/claude_negotiate/store.py

import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import redis.asyncio as aioredis

TTL = 2_592_000  # 30 days in seconds


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.strip().encode()).hexdigest()[:16]


def _topic_slug(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    return slug[:40]


class NegotiationStore:
    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._r: aioredis.Redis | None = None
        self._locks: dict[str, asyncio.Lock] = {}

    async def connect(self) -> None:
        self._r = aioredis.from_url(self._redis_url, decode_responses=True)

    async def disconnect(self) -> None:
        if self._r:
            await self._r.aclose()

    def _lock(self, neg_id: str) -> asyncio.Lock:
        if neg_id not in self._locks:
            self._locks[neg_id] = asyncio.Lock()
        return self._locks[neg_id]

    async def open_negotiation(
        self,
        topic: str,
        initiator_id: str,
        peer_id: str,
        context: str,
        max_rounds: int = 10,
    ) -> str:
        neg_id = f"neg-{uuid.uuid4().hex[:8]}"
        artifact_path = f"/var/lib/claude-negotiate/{neg_id}.md"
        self._locks[neg_id] = asyncio.Lock()

        async with self._r.pipeline() as pipe:
            pipe.hset(
                f"neg:{neg_id}:state",
                mapping={
                    "topic": topic,
                    "initiator_id": initiator_id,
                    "peer_id": peer_id,
                    "artifact_path": artifact_path,
                    "max_rounds": str(max_rounds),
                    "status": "open",
                    "created_at": _utcnow(),
                },
            )
            pipe.expire(f"neg:{neg_id}:state", TTL)
            pipe.sadd(f"pending:{initiator_id}", neg_id)
            pipe.sadd(f"pending:{peer_id}", neg_id)
            pipe.expire(f"pending:{initiator_id}", TTL)
            pipe.expire(f"pending:{peer_id}", TTL)
            pipe.xadd(
                f"neg:{neg_id}",
                {
                    "agent_id": initiator_id,
                    "content": context,
                    "content_hash": _content_hash(context),
                    "status": "context",
                    "posted_at": _utcnow(),
                },
            )
            pipe.expire(f"neg:{neg_id}", TTL)
            await pipe.execute()

        return neg_id

    async def post_position(
        self,
        neg_id: str,
        agent_id: str,
        content: str,
        status: Literal["proposing", "accepting", "counter", "blocked"],
        accepting_hash: str | None = None,
    ) -> dict:
        state_key = f"neg:{neg_id}:state"
        stream_key = f"neg:{neg_id}"

        state = await self._r.hgetall(state_key)
        if not state:
            raise ValueError(f"Negotiation {neg_id} not found")
        if state["status"] not in ("open", "blocked"):
            raise ValueError(f"Negotiation {neg_id} is {state['status']}, cannot post")

        ch = _content_hash(content)
        entry = {
            "agent_id": agent_id,
            "content": content,
            "content_hash": ch,
            "status": status,
            "accepting_hash": accepting_hash or "",
            "posted_at": _utcnow(),
        }

        if status == "blocked":
            entry_id = await self._r.xadd(stream_key, entry)
            await self._r.hset(
                state_key, mapping={"status": "blocked", "blocked_by": agent_id}
            )
            # Task 7: get round count
            async with self._r.pipeline() as pipe:
                pipe.xlen(stream_key)
                pipe.hget(state_key, "max_rounds")
                results = await pipe.execute()
            turns_used = results[0]
            max_turns = int(results[1]) * 2 if results[1] else int(state["max_rounds"]) * 2
            return {
                "content_hash": ch,
                "converged": False,
                "blocked": True,
                "turns_used": turns_used,
                "max_turns": max_turns,
                "entry_id": entry_id,
            }

        # Clear blocked status when the blocking agent resumes
        if state["status"] == "blocked" and state.get("blocked_by") == agent_id:
            await self._r.hset(state_key, "status", "open")

        converged = False
        entry_id = None
        if status == "accepting" and accepting_hash:
            async with self._lock(neg_id):
                entry_id = await self._r.xadd(stream_key, entry)
                await self._r.hset(
                    state_key, f"{agent_id}_accepting_hash", accepting_hash
                )
                initiator = state["initiator_id"]
                peer = state["peer_id"]
                other_id = peer if agent_id == initiator else initiator
                other_hash = await self._r.hget(
                    state_key, f"{other_id}_accepting_hash"
                )
                if other_hash and other_hash == accepting_hash:
                    await self._r.hset(
                        state_key,
                        mapping={
                            "status": "converged",
                            "converged_hash": accepting_hash,
                        },
                    )
                    converged = True
            if not converged:
                turn_count = await self._r.xlen(stream_key)
                if turn_count > int(state["max_rounds"]) * 2:
                    await self._r.hset(state_key, "status", "impasse")
        else:
            # Task 5: proposing/counter branch — auto-store self-accepting hash
            # and check if the other agent already accepted this same hash
            async with self._lock(neg_id):
                entry_id = await self._r.xadd(stream_key, entry)
                # Auto-store: proposer implicitly accepts their own proposal
                await self._r.hset(state_key, f"{agent_id}_accepting_hash", ch)
                initiator = state["initiator_id"]
                peer = state["peer_id"]
                other_id = peer if agent_id == initiator else initiator
                other_hash = await self._r.hget(
                    state_key, f"{other_id}_accepting_hash"
                )
                if other_hash and other_hash == ch:
                    await self._r.hset(
                        state_key,
                        mapping={
                            "status": "converged",
                            "converged_hash": ch,
                        },
                    )
                    converged = True
                else:
                    turn_count = await self._r.xlen(stream_key)
                    if turn_count > int(state["max_rounds"]) * 2:
                        await self._r.hset(state_key, "status", "impasse")

        # Task 7: atomically get turns_used and max_turns
        async with self._r.pipeline() as pipe:
            pipe.xlen(stream_key)
            pipe.hget(state_key, "max_rounds")
            results = await pipe.execute()
        turns_used = results[0]
        max_turns = int(results[1]) * 2 if results[1] else int(state["max_rounds"]) * 2

        return {
            "content_hash": ch,
            "converged": converged,
            "blocked": False,
            "turns_used": turns_used,
            "max_turns": max_turns,
            "entry_id": entry_id,
        }

    async def read_latest(
        self,
        neg_id: str,
        agent_id: str,
        since_id: str = "0",
    ) -> dict:
        state_key = f"neg:{neg_id}:state"
        stream_key = f"neg:{neg_id}"

        state = await self._r.hgetall(state_key)
        if not state:
            raise ValueError(f"Negotiation {neg_id} not found")

        if since_id == "0":
            entries = await self._r.xrange(stream_key)
        else:
            result = await self._r.xread({stream_key: since_id}, count=200)
            entries = result[0][1] if result else []

        turns = [
            {
                "id": entry_id,
                "agent_id": fields["agent_id"],
                "content": fields["content"],
                "content_hash": fields["content_hash"],
                "status": fields["status"],
                "accepting_hash": fields.get("accepting_hash", ""),
                "posted_at": fields.get("posted_at", ""),
            }
            for entry_id, fields in entries
        ]
        last_id = entries[-1][0] if entries else since_id

        turns_used = await self._r.xlen(stream_key)
        max_turns = int(state["max_rounds"]) * 2

        return {
            "turns": turns,
            "last_id": last_id,
            "negotiation_status": state["status"],
            "converged": state["status"] == "converged",
            "impasse": state["status"] == "impasse",
            "blocked": state["status"] == "blocked",
            "blocked_by": state.get("blocked_by", ""),
            "turns_used": turns_used,
            "max_turns": max_turns,
        }

    async def wait_for_turn(
        self,
        neg_id: str,
        agent_id: str,
        since_id: str,
        timeout_seconds: int = 60,
    ) -> dict:
        state_key = f"neg:{neg_id}:state"
        stream_key = f"neg:{neg_id}"

        state = await self._r.hgetall(state_key)
        if not state:
            raise ValueError(f"Negotiation {neg_id} not found")

        # Task 7: helper to get round count
        max_turns = int(state["max_rounds"]) * 2

        # If already done, return immediately without blocking
        if state["status"] not in ("open", "blocked"):
            turns_used = await self._r.xlen(stream_key)
            return {
                "turns": [],
                "last_id": since_id,
                "negotiation_status": state["status"],
                "converged": state["status"] == "converged",
                "impasse": state["status"] == "impasse",
                "timed_out": False,
                "turns_used": turns_used,
                "max_turns": max_turns,
            }

        current_since_id = since_id
        while True:
            # XREAD BLOCK: holds connection open until new entries arrive or timeout
            result = await self._r.xread(
                {stream_key: current_since_id},
                block=timeout_seconds * 1000,
                count=20,
            )

            if not result:
                # Timed out — re-read state in case it changed during the wait
                state = await self._r.hgetall(state_key)
                turns_used = await self._r.xlen(stream_key)
                return {
                    "turns": [],
                    "last_id": current_since_id,
                    "negotiation_status": state["status"],
                    "converged": state["status"] == "converged",
                    "impasse": state["status"] == "impasse",
                    "timed_out": True,
                    "turns_used": turns_used,
                    "max_turns": max_turns,
                }

            entries = result[0][1]

            # Bug 1 fix: if any returned entry has status=="accepting", re-read state
            # in a small retry loop to let the convergence write propagate.
            if any(fields.get("status") == "accepting" for _, fields in entries):
                for _ in range(3):
                    state = await self._r.hgetall(state_key)
                    if state.get("status") not in ("open", "blocked"):
                        break
                    await asyncio.sleep(0.05)
            else:
                # Re-read state after blocking — convergence may have been declared
                state = await self._r.hgetall(state_key)

            # Bug 2 fix: filter out self-turns from the returned list, but track
            # last_id across all entries (including self-turns) so we don't re-read.
            last_id = entries[-1][0]
            turns = [
                {
                    "id": entry_id,
                    "agent_id": fields["agent_id"],
                    "content": fields["content"],
                    "content_hash": fields["content_hash"],
                    "status": fields["status"],
                    "accepting_hash": fields.get("accepting_hash", ""),  # Bug 3 fix
                    "posted_at": fields.get("posted_at", ""),
                }
                for entry_id, fields in entries
                if fields["agent_id"] != agent_id
            ]

            # If all returned entries were self-turns, keep blocking rather than
            # returning empty — unless the negotiation is already done.
            if not turns and state["status"] in ("open", "blocked"):
                current_since_id = last_id
                continue

            turns_used = await self._r.xlen(stream_key)
            return {
                "turns": turns,
                "last_id": last_id,
                "negotiation_status": state["status"],
                "converged": state["status"] == "converged",
                "impasse": state["status"] == "impasse",
                "timed_out": False,
                "turns_used": turns_used,
                "max_turns": max_turns,
            }

    async def update_context(
        self, neg_id: str, agent_id: str, additional_context: str
    ) -> dict:
        state = await self._r.hgetall(f"neg:{neg_id}:state")
        if not state:
            raise ValueError(f"Negotiation {neg_id} not found")

        entry = {
            "agent_id": agent_id,
            "content": additional_context,
            "content_hash": _content_hash(additional_context),
            "status": "context_update",
            "posted_at": _utcnow(),
        }
        async with self._r.pipeline() as pipe:
            pipe.rpush(
                f"neg:{neg_id}:ctx",
                json.dumps(
                    {
                        "agent_id": agent_id,
                        "content": additional_context,
                        "posted_at": _utcnow(),
                    }
                ),
            )
            pipe.xadd(f"neg:{neg_id}", entry)
            await pipe.execute()

        return {"acknowledged": True}

    async def get_status(self, neg_id: str) -> dict:
        state = await self._r.hgetall(f"neg:{neg_id}:state")
        if not state:
            raise ValueError(f"Negotiation {neg_id} not found")
        turn_count = await self._r.xlen(f"neg:{neg_id}")
        return {**state, "turn_count": turn_count, "negotiation_id": neg_id}

    async def list_negotiations(self, agent_id: str) -> dict:
        neg_ids = await self._r.smembers(f"pending:{agent_id}")
        negotiations = []
        for neg_id in sorted(neg_ids):
            state = await self._r.hgetall(f"neg:{neg_id}:state")
            if state:
                negotiations.append(
                    {
                        "negotiation_id": neg_id,
                        "topic": state.get("topic", ""),
                        "status": state.get("status", ""),
                        "initiator_id": state.get("initiator_id", ""),
                        "peer_id": state.get("peer_id", ""),
                        "created_at": state.get("created_at", ""),
                    }
                )
        return {"negotiations": negotiations}

    async def get_transcript(self, neg_id: str) -> dict:
        state = await self._r.hgetall(f"neg:{neg_id}:state")
        if not state:
            raise ValueError(f"Negotiation {neg_id} not found")
        entries = await self._r.xrange(f"neg:{neg_id}")
        turns = [
            {
                "id": entry_id,
                "agent_id": fields["agent_id"],
                "status": fields["status"],
                "content": fields["content"],
                "content_hash": fields["content_hash"],
                "posted_at": fields.get("posted_at", ""),
            }
            for entry_id, fields in entries
        ]
        return {
            "negotiation_id": neg_id,
            "topic": state.get("topic", ""),
            "status": state.get("status", ""),
            "artifact_path": state.get("artifact_path", ""),
            "turns": turns,
        }

    async def human_inject(self, neg_id: str, content: str) -> dict:
        state = await self._r.hgetall(f"neg:{neg_id}:state")
        if not state:
            raise ValueError(f"Negotiation {neg_id} not found")
        ch = _content_hash(content)
        await self._r.xadd(
            f"neg:{neg_id}",
            {
                "agent_id": "human",
                "content": content,
                "content_hash": ch,
                "status": "human_inject",
                "posted_at": _utcnow(),
            },
        )
        return {"content_hash": ch, "acknowledged": True}

    async def close_negotiation(
        self,
        neg_id: str,
        agent_id: str,
        final_artifact: str | None = None,
        artifact_name: str | None = None,
    ) -> dict:
        state_key = f"neg:{neg_id}:state"
        async with self._lock(neg_id):
            state = await self._r.hgetall(state_key)
            if not state:
                raise ValueError(f"Negotiation {neg_id} not found")
            if state["status"] == "closed":
                artifact_path = state.get("artifact_path", "")
                artifact_content = ""
                p = Path(artifact_path)
                if p.exists():
                    artifact_content = p.read_text()
                return {
                    "status": "already_closed",
                    "artifact_path": artifact_path,
                    "artifact_content": artifact_content,
                }
            if state["status"] != "converged":
                raise ValueError(
                    f"Cannot close negotiation in status '{state['status']}'"
                )

            # Auto-fill content from converged turn if not provided
            if final_artifact is None:
                converged_hash = state.get("converged_hash", "")
                if not converged_hash:
                    raise ValueError("No converged_hash found; cannot auto-fill artifact")
                entries = await self._r.xrange(f"neg:{neg_id}")
                artifact_text = None
                for _entry_id, fields in entries:
                    if fields.get("content_hash") == converged_hash:
                        artifact_text = fields["content"]
                        break
                if artifact_text is None:
                    raise ValueError(
                        f"No stream entry found with content_hash={converged_hash}"
                    )
                final_artifact = artifact_text

            # Derive artifact path: explicit name > auto-generated slug > neg_id default
            if artifact_name:
                artifact_path = f"/var/lib/claude-negotiate/{artifact_name}"
            else:
                date = _utcnow()[:10].replace("-", "")
                slug = _topic_slug(state.get("topic", neg_id))
                init = state["initiator_id"].removeprefix("cc-")
                peer = state["peer_id"].removeprefix("cc-")
                artifact_path = f"/var/lib/claude-negotiate/{init}-{peer}-{slug}-{date}.md"

            # Append provenance footer
            closed_at = _utcnow()
            footer = (
                f"\n\n---\n"
                f"Agreed: {state['initiator_id']} × {state['peer_id']}\n"
                f"Negotiation: {neg_id}\n"
                f"Closed by: {agent_id}\n"
                f"Date: {closed_at}\n"
            )
            full_content = final_artifact + footer

            p = Path(artifact_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(full_content)

            await self._r.hset(
                state_key,
                mapping={
                    "status": "closed",
                    "closed_by": agent_id,
                    "closed_at": closed_at,
                    "artifact_path": artifact_path,
                },
            )
        return {
            "status": "closed",
            "artifact_path": artifact_path,
            "artifact_content": full_content,
        }

    async def get_artifact(self, neg_id: str) -> dict:
        """Read the agreed artifact. Works even when caller is on a different host."""
        state = await self._r.hgetall(f"neg:{neg_id}:state")
        if not state:
            raise ValueError(f"Negotiation {neg_id} not found")
        if state["status"] != "closed":
            return {"available": False, "status": state["status"], "artifact_path": ""}
        artifact_path = state.get("artifact_path", "")
        p = Path(artifact_path)
        if not p.exists():
            return {"available": False, "artifact_path": artifact_path, "reason": "file not written yet"}
        return {"available": True, "artifact_path": artifact_path, "content": p.read_text()}

    async def join_negotiation(self, neg_id: str, agent_id: str) -> dict:
        """Join an existing negotiation, returning full context and transcript."""
        state = await self._r.hgetall(f"neg:{neg_id}:state")
        if not state:
            raise ValueError(f"Negotiation {neg_id} not found")
        role = "initiator" if agent_id == state["initiator_id"] else "peer"
        entries = await self._r.xrange(f"neg:{neg_id}")
        turns = [
            {
                "id": entry_id,
                "agent_id": fields["agent_id"],
                "content": fields["content"],
                "content_hash": fields["content_hash"],
                "status": fields["status"],
                "accepting_hash": fields.get("accepting_hash", ""),
                "posted_at": fields.get("posted_at", ""),
            }
            for entry_id, fields in entries
        ]
        last_id = entries[-1][0] if entries else "0"
        return {
            "negotiation_id": neg_id,
            "your_agent_id": agent_id,
            "role": role,
            "topic": state["topic"],
            "negotiation_status": state["status"],
            "artifact_path": state.get("artifact_path", ""),
            "turns_used": len(entries),
            "max_turns": int(state["max_rounds"]) * 2,
            "converged_hash": state.get("converged_hash", ""),
            "last_id": last_id,
            "turns": turns,
        }
