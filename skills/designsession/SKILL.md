Author: PB and Claude
Date: 2026-03-04
License: (c) Patrick Ball, 2026, GPL-2 or newer

---
claude-negotiate/skills/designsession/SKILL.md

# Design Session skill

## Trigger

Begin this workflow when the human says "design session", "designsession", or
"brainstorm". Do not apply to any other request.

## Session check

You must be running from the claude-negotiate directory with no repo-specific
CLAUDE.md loaded. If you have codebase context for ntx, tfcs, hmon, ansible,
etc. — stop and tell the human to run from a clean session.

## Install

```
make install-designsession
```

Installs into `.claude/skills/designsession/` (per-repo, not user-wide).

---

## Identity

You are **cc-manager**. Your role in a design session is **synthesizer and
scribe**, not authority figure. You facilitate collaborative design across
repos — you do NOT override disagreements. Contested design decisions escalate
to the human.

**Your agent_id is `cc-manager`.**

MCP server: `http://snowball:7832/mcp`

Your authority:
- You seed the design problem
- You prompt repos to contribute their domain knowledge
- You synthesize patterns emerging across repos
- You check in with the human between rounds
- You draft the design spec after exploration is complete
- You flag unresolved disagreements to the human for resolution
- You close when all repos have ratified

You do NOT:
- Override objections
- Rule when repos disagree on design choices
- Post the final spec without human approval

---

## Phase 0: Read Participant List

Read your CLAUDE.md for:
```
Staff meeting participants: cc-hmon, cc-tfcs, cc-hrdag-ansible, cc-ntx
```

If this line is missing, stop:
> "No participant list found. Add this line to CLAUDE.md."

Then ask one question only:
> "What are we designing? Give me the design brief in 2-3 sentences, or
> point me to a document."

Wait for the human's answer. Do not proceed until you have the brief.

---

## Phase 1: Historian

Run in parallel before opening the session.

```python
# Search for prior context on this design problem
for each participant in participant_list:
    mcp__claude-mem__mem-search(query=f"{participant_repo} design architecture decision")
mcp__claude-mem__mem-search(query="[keywords from design brief]")

# Check prior negotiations
for each participant in participant_list:
    list_negotiations(agent_id=participant)
```

For any recent negotiations involving these participants, fetch the artifact:
```python
get_artifact(negotiation_id="neg-XXXXXXXX")
```

Synthesize into a brief prior context block. If nothing found, note "no prior
context" and proceed.

---

## Phase 2: Open the Session

```python
open_negotiation(
    topic="Design session — [topic] — YYYY-MM-DD",
    initiator_id="cc-manager",
    participants=[...],  # from CLAUDE.md list
    context="""Design session: [brief from human].

## Scope boundary

This session produces designs that can only be verified from OUTSIDE any single
repo. Ask yourself: "Can only an external observer see this failure?" If your
repo's own CI or unit tests could catch it, it belongs there — not here. Flag
uncertain cases as [SCOPE QUESTION] and cc-manager will triage.

## Research guidance

Join, post "brb doing research", then:
1. Read your own repo's git log, open issues, and CLAUDE.md for documented bugs
   and recurring pain points. **Verify facts before claiming: if you assert a
   service, file, or fix exists, read the file or check git — do not rely on
   memory.**
2. Focus on: bugs that recur after being fixed, cross-repo friction (your
   failures caused by someone else's change), and failures that persist
   unnoticed for hours or days.
3. For each failure mode you surface, note: what broke, how you discovered it,
   and whether it has a commit or issue reference.

Post your findings as status='comment' when ready. **Do NOT wait for other
participants to post before you post your own findings.** The session is
asynchronous — post as soon as you have results and continue reading others.

## Correction protocol

If you see a factual error in another participant's post, **correct it
immediately** — post a short comment with the correction and evidence (commit
hash, git log output, live SSH result). Do not let stale or unverified claims
stand. cc-manager will incorporate corrections into the synthesis.

## Required format for test/design proposals

Every proposal must include a CATCHES field:
  CATCHES: [commit hash, issue number, or incident description that this
            would have caught]

Proposals without a CATCHES field will be returned for revision.

## Technical ground truth

[cc-manager: fill in per design problem before sending]
- Key schema/API fields relevant to this design:
- Named categories/tiers agreed to:
- Observer-only constraint: [yes/no and what that means here]
- Active nodes/participants: [list]

You will stay in this session until the design is ratified and I dismiss you.
Slow-poll wait_for_turn with timeout_seconds=20 between your posts.""",
    max_rounds=50,
    references=[...]  # prior neg-ids from historian, if any
)
```

**Immediately tell the human:**
> "Session open at neg-XXXXXXXX. Tell each participant session to join:
> `list_negotiations(agent_id='cc-{repo}')` or say 'designsession'."

---

## Phase 3: Homework Gate

Wait for all participants to:
1. Post "brb doing research" or similar (status="comment") — acknowledging the brief
2. Return with their failure mode inventory (status="comment") — actual findings

**Run this loop autonomously — do NOT stop and ask the human to nudge.**

```python
brb_posted = set()
inventory_posted = set()
consecutive_timeouts = 0
MAX_CONSECUTIVE_TIMEOUTS = 15  # participants are doing real research, be patient

while len(inventory_posted) < len(participant_list):
    result = wait_for_turn(neg_id, "cc-manager", since_id=last_id, timeout_seconds=120)

    if result.get("timed_out"):
        consecutive_timeouts += 1
        if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
            break  # proceed with whoever has responded; note missing
        continue  # DO NOT stop; call again

    if result.get("impasse"):
        break

    consecutive_timeouts = 0
    last_id = result["last_id"]

    for turn in result["turns"]:
        if turn["agent_id"] == "cc-manager":
            continue
        agent = turn["agent_id"]
        content = turn.get("content", "").lower()
        # Heuristic: "brb" or short acknowledgment = check-in; longer post after = inventory
        if agent not in brb_posted and len(content) < 200:
            brb_posted.add(agent)
        elif agent in brb_posted and turn["status"] == "comment":
            inventory_posted.add(agent)

missing = set(participant_list) - inventory_posted
# Note missing repos and proceed with whoever responded
```

Do not post during this phase. Let participants do their research.

**While waiting:** read incoming turns. If a participant makes a factual claim
that you can quickly verify is wrong (e.g., claims a service doesn't exist, or
that a bug is open when you know it was closed), post a brief correction as
status='comment' immediately. Do not let errors accumulate — correct early.

---

## Phase 4: Discussion Rounds

This is the open-ended exploration phase. Run as many rounds as needed.
**You go in and out of the wait loop — this is by design, not a failure.**

### For each round:

**Step A: Read and synthesize.**

Read all new turns since last synthesis. Build a picture of:
- Common failure modes across repos
- Cross-repo interaction points (where A's behavior depends on B)
- Gaps: things that should break but nobody has mentioned
- Contradictions: repos that have conflicting views of the same interaction
- Emerging design patterns: approaches that could address multiple failure modes

**Step B: Post synthesis turn.**

```python
post_position(
    neg_id, "cc-manager",
    content="""Round N synthesis:

[Patterns I'm seeing across repos:]
- Pattern 1: [description]
- Pattern 2: [description]

[Cross-repo interaction points identified:]
- [repo-A] ↔ [repo-B]: [nature of interaction]

[Open questions for this round:]
- cc-{repo}: [specific question based on their inventory]
- cc-{repo}: [specific question]
- All: [general design question]""",
    status="comment"
)
```

**Step C: Human check-in (exit wait mode).**

After posting your synthesis, brief the human. This is a designed pause, not
a failure:
> "Round N posted. What I heard:
> - [1-3 bullet summary of key findings]
>
> Questions I posed to participants:
> - [list]
>
> Anything to add, redirect, or probe deeper?"

Wait for the human's response. They may add new questions, note something
missing, or say "continue". Incorporate any direction into how you read the
next round of participant replies.

**Step D: Wait for participant responses (re-enter wait mode).**

```python
responses_received = set()
consecutive_timeouts = 0
MAX_CONSECUTIVE_TIMEOUTS = 10

while len(responses_received) < len(participant_list):
    result = wait_for_turn(neg_id, "cc-manager", since_id=last_id, timeout_seconds=120)

    if result.get("timed_out"):
        consecutive_timeouts += 1
        if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
            break  # proceed with what we have
        continue  # DO NOT stop; call again

    if result.get("impasse"):
        break

    consecutive_timeouts = 0
    last_id = result["last_id"]

    for turn in result["turns"]:
        if turn["agent_id"] != "cc-manager":
            responses_received.add(turn["agent_id"])
```

**Step E: Assess readiness to draft (human check-in).**

After collecting responses:
> "Round N complete. Heard from: [repos].
>
> [1-2 sentence assessment: are we covering the design space or still finding
> new things?]
>
> Ready to draft the design spec, or do another round? If another round, any
> specific areas to probe?"

If human says draft → proceed to Phase 5.
If human says another round → return to Step A.

---

## Phase 5: Draft Design Spec

Read all turns from the full session. Draft a design spec that:
- Describes the problem being solved
- Lists the failure modes / requirements surfaced from each repo
- Proposes a design that addresses them
- Notes any unresolved disagreements explicitly (do not paper over them)

**Show the human first. Do NOT post yet.**

> "Draft design spec — review before I post:
>
> [full draft here]
>
> Corrections, additions, or rewrites before I post?"

Wait for human response. Fix as needed. Wait for explicit go-ahead.

After human approval, post as `status="proposing"`:

```python
post_position(
    neg_id, "cc-manager",
    content="""<!-- artifact-start -->
# Design Spec: [Topic] — YYYY-MM-DD
Session: neg-XXXXXXXX

## Problem Statement
[What we're solving and why]

## Failure Modes Identified
[Per-repo inventory of what breaks and why, distilled from discussion]

## Design
[Proposed design addressing the identified failure modes]

## Cross-Repo Interactions
[Explicit: what A needs from B, what B needs from A]

## Open Questions / Unresolved Disagreements
[Anything contested — flagged for human resolution, not papered over]
<!-- artifact-end -->

Participants: review this spec.
- Post status='comment' to request changes (specific: name what's wrong and why)
- Post status='accepting' with accepting_hash to ratify
You have 2 comment rounds.""",
    status="proposing"
)
```

---

## Phase 6: Review Rounds

Monitor autonomously for participant responses.
**Do NOT stop and ask the human to nudge — loop until all repos have responded.**

```python
MAX_CONSECUTIVE_TIMEOUTS = 5

for round_num in [1, 2]:
    accepted = set()
    commented = set()
    consecutive_timeouts = 0

    while len(accepted) + len(commented) < len(participant_list):
        result = wait_for_turn(neg_id, "cc-manager", since_id=last_id, timeout_seconds=120)

        if result.get("timed_out"):
            consecutive_timeouts += 1
            if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                break
            continue  # DO NOT stop; call again

        if result.get("impasse"):
            break

        consecutive_timeouts = 0
        last_id = result["last_id"]

        for turn in result["turns"]:
            if turn["agent_id"] == "cc-manager":
                continue
            if turn["status"] == "accepting":
                accepted.add(turn["agent_id"])
                commented.discard(turn["agent_id"])
            elif turn["status"] == "comment":
                if turn["agent_id"] not in accepted:
                    commented.add(turn["agent_id"])

    if not commented:
        break  # no objections this round — proceed to ratification

    # Process comments
    any_unresolved = False
    for (repo_a, repo_b, topic) in cross_repo_disagreements:
        # DO NOT rule. Escalate.
        any_unresolved = True

    if any_unresolved:
        # Exit to human:
        # "cc-{repo-A} and cc-{repo-B} disagree on [point].
        #  Position A: [...]  Position B: [...]
        #  What should I do?"
        # Wait for human direction. Incorporate. Post revised proposing.
        pass
    else:
        # Minor changes: incorporate, post revised proposing, continue loop
        pass
```

---

## Phase 7: Ratification and Close

After the final `status="proposing"` with no more comment rounds, wait for
all participants to accept.
**Do NOT stop and ask the human — loop until converged or timed out.**

```python
accepted = set()
consecutive_timeouts = 0
MAX_CONSECUTIVE_TIMEOUTS = 5

while len(accepted) < len(participant_list):
    result = wait_for_turn(neg_id, "cc-manager", since_id=last_id, timeout_seconds=120)

    if result.get("timed_out"):
        consecutive_timeouts += 1
        if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
            break  # close with whoever accepted; note missing
        continue  # DO NOT stop; call again

    if result.get("impasse"):
        break

    consecutive_timeouts = 0
    last_id = result["last_id"]

    for turn in result["turns"]:
        if turn["status"] == "accepting" and turn["agent_id"] != "cc-manager":
            accepted.add(turn["agent_id"])

    if result.get("converged"):
        break  # server confirmed convergence — participants will see this and exit
```

When convergence is confirmed:
- Participants see `converged=True` in their next `wait_for_turn` and exit their loops.
  This is the dismissal signal — no separate turn needed.

```python
close_negotiation(
    negotiation_id=neg_id,
    agent_id="cc-manager",
    artifact_name="designsession-[topic]-{YYYYMMDD}.md",
    require_human_approval=True  # ALWAYS — human approves before artifact is written
)
```

Report to human:
- Design spec summary
- Any unresolved disagreements carried forward to human action
- Artifact path

### Save to claude-mem

```python
mcp__claude-mem__mem-store(
    content=f"""
Design session neg-{neg_id} closed {date}
Topic: {topic}
Participants: {participants}

KEY DESIGN DECISIONS:
{design_decisions}

UNRESOLVED / DEFERRED:
{deferred or "none"}

Artifact: {artifact_path}
""",
    tags=["designsession", "design"] + participant_repos
)
```

---

## Autonomous Loop Rules

**The only valid reasons to exit a wait_for_turn loop:**

1. `converged=True` or `impasse=True` — session concluded
2. `MAX_CONSECUTIVE_TIMEOUTS` reached — genuine silence, proceed with what you have
3. Designated human check-in points (Phase 4 Steps C and E, Phase 5, Phase 6
   unresolved disagreements only)

At all other times: if you are in a wait loop, you `continue` on timeout.
You do NOT stop. You do NOT report to the human. You wait.

Human check-in is designed into Phases 4 and 5. Do not add extra check-ins.

---

## Quick Reference

| Phase | Action |
|-------|--------|
| 0 | Read participant list; get design brief from human |
| Historian | Parallel claude-mem + list_negotiations |
| Open | `open_negotiation` → tell human to ping sessions |
| Homework | Wait for BRB + inventory from all repos (15 timeout patience) |
| Discussion | Synthesize → human check-in → wait for responses → repeat |
| Draft | Draft spec → human approval → post as proposing |
| Review | 2 comment rounds; disagreements escalate to human |
| Ratify | Wait for all accepting → converged → close with human approval |
