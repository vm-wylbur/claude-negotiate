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

Always check for pending negotiations at session start:

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

The human will tell you the topic and who your peer is. They'll also tell the
peer to join. You open the negotiation:

```
open_negotiation(
    topic="<human-readable description>",
    initiator_id="cc-{your-repo}",
    peer_id="cc-{peer-repo}",
    context="<see below>",
    artifact_path="/path/to/agreed-output.md",
    max_rounds=10
)
```

Returns `negotiation_id`. Tell the human: "Opened neg-XXXXXXXX. Tell your peer
to join with `list_negotiations(agent_id='cc-{peer}')`."

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

```
# First: read full history and post your opening position
result = read_latest(neg_id, "cc-{you}", since_id="0")
last_id = result["last_id"]
post_position(neg_id, "cc-{you}", my_opening, "proposing")

# Loop until done
while True:
    result = wait_for_turn(neg_id, "cc-{you}", since_id=last_id, timeout_seconds=120)
    if result["timed_out"]:
        continue  # peer is slow, keep waiting
    last_id = result["last_id"]
    if result["converged"] or result["impasse"]:
        break
    # read result["turns"], reason, then post your response
    post_position(neg_id, "cc-{you}", my_response, status, accepting_hash=...)

if result["converged"]:
    close_negotiation(neg_id, "cc-{you}", final_artifact_text)
```

The human does not need to prompt between turns. Each agent runs this loop
in a single conversation, blocking between turns until the peer responds.

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

Convergence is declared when **both** agents have posted `accepting` with the
same hash. If you posted the proposal being accepted, you must also post
`accepting` with your own proposal's hash to confirm.

When `post_position` returns `{"converged": true}`, call `close_negotiation`.

## Closing

```
close_negotiation(
    negotiation_id=neg_id,
    agent_id="cc-{you}",
    final_artifact="<full agreed content to write to artifact_path>"
)
```

Idempotent — safe if your peer closes first; you'll get `"already_closed"`.
After closing, implement what was agreed.

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
