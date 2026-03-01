Author: PB and Claude
Date: 2026-02-28
License: (c) Patrick Ball, 2026, GPL-2 or newer

---
claude-negotiate/skills/negotiate/SKILL.md

# claude-negotiate skill

MCP server at `http://snowball:7832/mcp`. Register once per machine:
```
claude mcp add --transport http --scope user claude-negotiate http://snowball:7832/mcp
```

Install this skill per-repo (only that project sees it):
```
mkdir -p .claude/skills
cp -r /path/to/claude-negotiate/skills/negotiate .claude/skills/
```

Or user-wide (all projects on this machine see it):
```
cp -r /path/to/claude-negotiate/skills/negotiate ~/.claude/skills/
```

## Session start

Always check for pending negotiations at session start using the
**claude-negotiate MCP tool**:

```
list_negotiations(agent_id="cc-{your-repo-name}")
```

If any negotiations have status `open` or `blocked`, call `get_status` on each
and read the transcript with `read_latest`. Respond before doing anything else.

## Your agent_id

Use `cc-{repo-name}` — e.g., `cc-ntx`, `cc-tfcs`. Be consistent. Your peer
needs to match what you register.

## When to open a negotiation

Use `open_negotiation` when ALL of these are true:
- The problem requires knowledge from **both** repos to solve correctly
- A wrong decision would be hard to undo (filesystem layout, uid/gid, schema)
- The answer is verifiable (you can check the result with a command)

Do NOT open a negotiation for:
- Questions you can resolve yourself by reading your own repo
- Preferences or style choices
- Anything the human should decide

## Opening a negotiation

The human will tell you the topic and who your peer(s) are. They'll also tell the
peer(s) to join.

**STOP. Do NOT research your repo first.** Use the **claude-negotiate MCP tool**
to open immediately with a placeholder context. The human needs the neg-id NOW
so they can unblock your peers. Research happens AFTER you open and AFTER you
share the neg-id:

```
# 2-party
open_negotiation(
    topic="<human-readable description>",
    initiator_id="cc-{your-repo}",
    participants=["cc-{peer-repo}"],
    context="Opening — full position coming in first post_position.",
    max_rounds=10
)

# 3-party
open_negotiation(
    topic="<human-readable description>",
    initiator_id="cc-{your-repo}",
    participants=["cc-{peer1}", "cc-{peer2}"],
    context="Opening — full position coming in first post_position.",
    max_rounds=10
)
```

`participants` = all non-initiator agents. Convergence requires ALL participants
(including initiator) to accept the same hash.

The artifact filename includes all participants:
`{initiator}-{peer1}-{peer2}-{topic-slug}-{date}.md`

The artifact is automatically written to `/var/lib/claude-negotiate/{...}.md` on
the server when the negotiation is closed. You can read it with `get_artifact(neg_id)`.

Returns `negotiation_id`. **Your very next message to the human MUST be**:
"Opened neg-XXXXXXXX. Tell your peers to join with
`list_negotiations(agent_id='cc-{peer}')`."

Do not say anything else first. Do not research. Pass the neg-id immediately.

Then research your repo and post your real opening position with `post_position`
before calling `wait_for_turn`. Your peers will join and block waiting for your
first turn.

## Writing a good context field

The context is your opening statement. Include:
- **What you know**: relevant paths, current permissions, existing config
- **Your constraints**: what you cannot change and why
- **Your initial position**: a concrete proposal, not just a question
- **What you need from the peer**: specifically what information would help

Bad: "We need to agree on ACL settings."
Good: "Tree at /data/shared is owned by ntx:ntx (755). tfcs-user needs read
access. POSIX ACLs are available (ext4). My constraint: ntx processes write to
this tree as ntx-user and cannot change ownership. Initial proposal: `setfacl
-m u:tfcs-user:r-x /data/shared`."

## Autonomous loop (preferred)

Use `wait_for_turn` to run without human prompting. After posting, call
`wait_for_turn` instead of `read_latest` — it blocks on the server until your
peer responds, then returns the new turns automatically.

**When to use `read_latest` vs `wait_for_turn`:**
- Use `read_latest` when you've just joined and want the full history, or when
  you think a reply might have arrived while you were processing
- Use `wait_for_turn` when you've just posted and need to block for the peer's
  response
- Rule of thumb: `read_latest` to catch up, `wait_for_turn` to wait

```python
# First: read full history and post your opening position
result = read_latest(neg_id, "cc-{you}", since_id="0")
last_id = result["last_id"]
result = post_position(neg_id, "cc-{you}", my_opening, "proposing")
entry_id = result["entry_id"]   # use this as since_id to skip your own turn

# Loop until done
while True:
    result = wait_for_turn(neg_id, "cc-{you}", since_id=entry_id, timeout_seconds=120)
    if result["timed_out"]:
        continue  # peer is slow, keep waiting
    last_id = result["last_id"]
    if result["converged"] or result["impasse"]:
        break
    # read result["turns"], reason, then post your response
    result = post_position(neg_id, "cc-{you}", my_response, status, accepting_hash=...)
    entry_id = result["entry_id"]   # use this as since_id to skip your own turn

if result["converged"]:
    close_negotiation(neg_id, "cc-{you}")
```

The human does not need to prompt between turns. Each agent runs this loop
in a single conversation, blocking between turns until the peer responds.

## Joining as peer

When the human tells you to join an existing negotiation, use `join_negotiation`
as your entry point — not `read_latest`. It returns your role, the full
transcript, and `last_id` ready for `wait_for_turn` in one call.

```
join_negotiation(negotiation_id=neg_id, agent_id="cc-{you}")
```

After joining, post your opening position with `post_position`, then enter the
autonomous loop above (starting at the `while True` block — `join_negotiation`
already gives you `last_id`).

## Manual loop (fallback)

1. Call `read_latest(negotiation_id, "cc-{you}", since_id="0")` on first turn,
   then pass back the returned `last_id` on every subsequent call.

2. Read every turn including `context_update` and `human_inject` turns — they
   affect what's valid.

3. Reason about your peer's last position before responding. Consider:
   - Does their counter satisfy your constraints?
   - Does it satisfy theirs?
   - Is there a modification that satisfies both?

4. Post your response:
   ```
   post_position(
       negotiation_id=neg_id,
       agent_id="cc-{you}",
       content="<your full proposal — be specific, include commands/paths>",
       status="proposing" | "counter" | "accepting" | "blocked",
       accepting_hash="<hash from peer's turn>"  # only when accepting
   )
   ```

## Accepting a proposal

To accept, you must reference the **exact** `content_hash` of the turn you
are agreeing to — as returned in `read_latest`. You cannot accept a paraphrase.

```
post_position(
    negotiation_id=neg_id,
    agent_id="cc-{you}",
    content="Accepted",
    status="accepting",
    accepting_hash="<content_hash from the turn you accept>"
)
```

When you post `proposing` or `counter`, you automatically accept your own
proposal. If your peer then posts `accepting` with your `content_hash`,
convergence is declared immediately — you do NOT need to post a second
`accepting`.

When `post_position` returns `{"converged": true}`, call `close_negotiation`.

## Close coordination

When convergence is declared, both agents see `converged=True`. By convention:
- **The initiator closes.** The peer should wait briefly (a few seconds) and
  call `close_negotiation` only if the initiator hasn't closed yet.
- Pass `final_artifact` with just the agreed content — no preamble, no Q&A,
  no turn metadata ("Turn 3/20", questions to the peer). Extract the relevant
  section from the converged turn. If omitted, server auto-fills from the raw
  turn content (which may include conversational preamble).
- Pass `artifact_name` as a human-readable filename describing what was agreed,
  e.g. `tfc-hmon-ansible-tls-certs-20260301.md` (include all participants). Written to
  `/var/lib/claude-negotiate/{artifact_name}`. If omitted, auto-generated from all
  participants: `{p1}-{p2}-...-{topic-slug}-{date}.md`.

```
close_negotiation(
    negotiation_id=neg_id,
    agent_id="cc-{you}",
    final_artifact="<extracted agreement section only>",
    artifact_name="cc-tfc-cc-hmon-{topic-slug}-{YYYYMMDD}.md"
)
```

The server always appends a provenance footer (agreed-by, neg_id, date).
`artifact_content` in the response includes the footer — confirm what was written.

## Closing

```
close_negotiation(
    negotiation_id=neg_id,
    agent_id="cc-{you}"
)
```

The response always includes `artifact_content` — the text that was written.

Idempotent — safe if your peer closes first; you'll get `"already_closed"`.
After closing, implement what was agreed.

## Reading artifacts remotely

After a negotiation closes, the artifact lives on the server. If you're on a
different host (e.g. scott reading an artifact written on snowball), use
`get_artifact` — do not scp or rsync:

```
get_artifact(negotiation_id=neg_id)
```

Returns `available=True` and `content` once the negotiation is closed.
The `close_negotiation` response also includes a `tip` field with the exact
call to use.

## When to post blocked

Post `status="blocked"` when you cannot proceed without a fact you cannot
verify yourself:

```
post_position(
    negotiation_id=neg_id,
    agent_id="cc-{you}",
    content="Blocked: cannot verify whether /data/shared is on NFS — POSIX
             ACLs may not be supported. Need human to confirm filesystem type.",
    status="blocked"
)
```

Do NOT block just because you're uncertain. Block only when you'd have to
**guess** a system fact. Resume by posting a new `proposing` turn once you
have the information.

## When to update context

If you discover a new constraint mid-negotiation (not a counter-proposal,
just new information your peer needs):

```
update_context(
    negotiation_id=neg_id,
    agent_id="cc-{you}",
    additional_context="Discovered: /data/shared is bind-mounted from NFS.
                        POSIX ACLs will not persist across remounts."
)
```

Does not consume a round. Your peer sees it in their next `read_latest`.

## Impasse

If `max_rounds` is reached without convergence, the server writes an impasse
document to `artifact_path` and sets status to `impasse`. Stop posting. Tell
the human: "Impasse at {artifact_path}. Review the document and restart with
more context."

## Human turns

If `read_latest` returns a turn with `agent_id="human"`, treat it as
authoritative. Respond to it before making your next proposal. The human can
redirect, correct, or provide missing facts.

## Additional tools

**get_artifact**: Read the agreed artifact from the server, even if you're on a
different host.
```
get_artifact(negotiation_id=neg_id)
```

## Round budget

`turns_used` and `max_turns` are now returned in `post_position`, `read_latest`,
and `wait_for_turn`. Agents should mention their turn budget awareness in
discussion: "I'm at turn 6/20 — I'll keep my next proposal concise."
