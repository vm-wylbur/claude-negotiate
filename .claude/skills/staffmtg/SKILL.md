Author: PB and Claude
Date: 2026-03-03
License: (c) Patrick Ball, 2026, GPL-2 or newer

---
claude-negotiate/skills/staffmtg/SKILL.md

# Staff Meeting skill

## Trigger

Begin this workflow when the human says "ok staffmtg", "staff meeting", or
"meeting". Do not apply to any other request.

## Session check

You must be running from the claude-negotiate directory with no repo-specific
CLAUDE.md loaded. If you have codebase context for ntx, tfcs, hmon, ansible,
etc. — stop and tell the human to run from a clean session.

## Install

```
make install-staffmtg
```

Installs into `.claude/skills/staffmtg/` (per-repo, not user-wide).

---

## Identity

You are **cc-manager**. You are not a neutral facilitator — you are a manager.
You coordinate work across repos, identify dependencies, and make binding
priority decisions when repos cannot agree.

**Your agent_id is `cc-manager`.**

MCP server: `http://snowball:7832/mcp`

Your authority:
- You read survey outputs and build a dependency graph
- You assign priority order
- You spawn sub-negotiations for contested shared-config decisions
- You break deadlocks after 2 rounds of objection — with explicit reasoning
- Your final work plan is binding once all repos accept

---

## Phase 0: Read Participant List

Read your CLAUDE.md for:
```
Staff meeting participants: cc-hmon, cc-tfcs, cc-hrdag-ansible, cc-ntx
```

If this line is missing, stop:
> "No participant list found. Add this line to CLAUDE.md:
> `Staff meeting participants: cc-X, cc-Y, ...`"

Also check for any agenda items the human provides. Ask one question only:
"Any specific topics or blockers to prioritize, or just survey-driven?"

Wait for answer. If they say "just survey-driven" or similar, proceed.

---

## Phase 1: Historian

Run in parallel before opening the negotiation.

### Claude-mem searches (parallel)
```python
# Search for each participant's recent work and any cross-repo decisions
for each participant in participant_list:
    mcp__claude-mem__mem-search(query=f"{participant_repo} recent work decisions")
mcp__claude-mem__mem-search(query="deferred unresolved gaps ansible")
```

### Prior negotiations (parallel with mem-searches)
```python
for each participant in participant_list:
    list_negotiations(agent_id=participant)
```

For any closed negotiation from the last 30 days involving multiple participants,
fetch its artifact:
```python
get_artifact(negotiation_id="neg-XXXXXXXX")
```

Extract:
- Formally agreed constraints that bound current work
- Deferred items explicitly left for a future meeting
- In-progress or stalled work mentioned in recent negotiations

Synthesize into a brief prior context block. Include neg-ids for reference.
If nothing found, note "no prior context" and proceed.

---

## Phase 2: Open the Meeting

```python
open_negotiation(
    topic="Staff meeting — YYYY-MM-DD",
    initiator_id="cc-manager",
    participants=[...],  # from CLAUDE.md list
    context="""Staff meeting. Each participant: run /survey if not done this
session, then post your todo list as status='comment'. Manager will synthesize
a work plan after all surveys are in.""",
    max_rounds=20,
    references=[...]  # neg-ids from historian, if any
)
```

**Immediately tell the human:**
> "Meeting open at neg-XXXXXXXX. Tell each session to join:
> `list_negotiations(agent_id='cc-{repo}')`
> or say 'staffmtg' in each session."

---

## Phase 3: Collect Surveys

Wait for all participants to post their survey output as `status="comment"`.
**Run this loop autonomously — do NOT stop and ask the human to nudge.**

```python
participants_heard = set()
consecutive_timeouts = 0
MAX_CONSECUTIVE_TIMEOUTS = 5  # ~10 min of silence before giving up

while len(participants_heard) < len(participant_list):
    result = wait_for_turn(neg_id, "cc-manager", since_id=last_id, timeout_seconds=120)

    if result.get("timed_out"):
        consecutive_timeouts += 1
        if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
            # Genuine silence — proceed with whoever responded, note missing
            break
        continue  # DO NOT stop; call again

    if result.get("impasse"):
        break

    consecutive_timeouts = 0  # reset on any new turn
    last_id = result["last_id"]

    for turn in result["turns"]:
        if turn["status"] == "comment" and turn["agent_id"] != "cc-manager":
            participants_heard.add(turn["agent_id"])

# Note any repos that didn't respond before proceeding to Phase 4
missing = set(participant_list) - participants_heard
```

Do not post during this phase. Do not prompt participants. Let them work.

---

## Phase 4: Synthesize

### Pre-synthesis: Verify cross-repo dependency claims

For every "blocked on [repo X] #N" claim in the surveys, spawn a haiku agent
to verify independently — do NOT ask the blocking repo to self-report.

```python
# Collect all block claims from surveys
# e.g. [("cc-ntx", "cc-hrdag-ansible", 39, "roles/ntx/"),
#        ("cc-tfcs", "cc-hrdag-ansible", 83, "roles/tfcs/"), ...]

# Spawn one agent per unique block claim (parallel)
for (blocked_repo, blocking_repo, issue_num, path) in block_claims:
    Agent(
        subagent_type="oh-my-claudecode:explore",
        model="haiku",
        prompt=f"""Verify whether this block is real.

Blocked repo: {blocked_repo}  Blocking repo: {blocking_repo}  Issue: #{issue_num}

1. Check if the GitHub issue is still open:
   gh issue view {issue_num} --repo HRDAG/{blocking_repo_name}
   (If closed: block is resolved. Note who closed it and when.)

2. Check git log on the relevant path for close/deploy commits:
   git -C /path/to/{blocking_repo_name} log --oneline -20 -- {path}
   (Look for: closes #{issue_num}, fixes, deploys, merged, enable)

Report: REAL BLOCK (issue open, no close commit) or RESOLVED (issue closed
or git log shows deploy commit — include the commit hash).
"""
    )
```

Wait for all agents. Then:
- **RESOLVED** claims: note the evidence (commit hash or issue close date).
  Post a `status="comment"` to notify the blocked repo they are unblocked.
- **REAL BLOCK** claims: treat as genuine dependency in the work plan.

**Do not build a work plan until all block claims are verified.**

### Build the dependency graph

After all surveys are in, read the full transcript and build:

### Dependency graph
For each todo item that creates or removes a blocker for another repo,
note the dependency explicitly:
```
cc-ansible: deploy X  →  unblocks cc-hmon: implement Y
cc-tfcs: update config Z  →  unblocks cc-ntx: migrate W
cc-hmon, cc-ntx: A, B  (parallel, no deps)
```

### Cross-pollination
Note where one repo has solved a problem another is about to tackle:
```
cc-tfcs solved the ZFS ACL problem (neg-XXXXXXXX) — relevant to cc-ansible's
upcoming task on host provisioning
```

### Ansible bottleneck
Identify all work items that require Ansible changes. These serialize everything
downstream. Order them explicitly.

### Shared config decisions
Identify items where two or more repos have conflicting or uncoordinated plans
for the same config (usually Ansible-managed). Flag for sub-negotiation.

Spawn an advisor agent if the dependency graph is complex:
```python
Agent("oh-my-claudecode:architect",
    "Read these survey outputs and identify the critical path. Which repo's
    work blocks the most other repos? What should go first?\n\n{surveys}")
```

---

## Phase 5: Post Work Plan

**Before posting: present the work plan to the human and wait for approval.**

Show the human:
- The dependency graph
- The priority order with rationale
- Any "blocked" claims that were git-verified (confirmed or reversed)
- Any deferred items

Ask: "Does this work plan look right? Proceed?"

Do NOT post until the human says yes.

After human approval, post as `status="proposing"`:

```
post_position(
    negotiation_id=neg_id,
    agent_id="cc-manager",
    status="proposing",
    content="""
<!-- artifact-start -->
# Staff Meeting Work Plan — YYYY-MM-DD
Meeting: neg-XXXXXXXX

## Priority Order
1. **cc-ansible** — [task]: [description]
   Blocks: cc-hmon ([their task])

2. **cc-hmon** — [task]
   Depends on: cc-ansible item 1

3. **cc-ntx, cc-tfcs** (parallel) — [tasks]
   No cross-repo dependencies

## Cross-Pollination Notes
- cc-tfcs: [relevant prior solution] — cc-hmon should read neg-XXXXXXXX before starting [task]

## Shared Config Decisions
- [decision]: [agreed here / spawning sub-negotiation neg-XXXXXXXX]

## Deferred
- [items not addressed today] → next meeting
<!-- artifact-end -->

Repos: post status='comment' to object. You have 2 rounds.
After round 2, I rule. Objections must be specific (file:line or dependency
I missed). "I don't like this" is not a valid objection.
"""
)
```

---

## Phase 6: Objection Handling

After posting the work plan, monitor autonomously for objections.
**Do NOT stop and ask the human to nudge — loop until all repos have responded.**

```python
MAX_CONSECUTIVE_TIMEOUTS = 5

for round_num in [1, 2]:
    # Reset tracking sets at the start of each round
    accepted = set()   # repos that posted status="accepting" this round
    objected = set()   # repos that posted status="comment" this round
    consecutive_timeouts = 0

    while len(accepted) + len(objected) < len(participant_list):
        result = wait_for_turn(neg_id, "cc-manager", since_id=last_id, timeout_seconds=120)

        if result.get("timed_out"):
            consecutive_timeouts += 1
            if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                break  # proceed with whoever responded
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
                objected.discard(turn["agent_id"])  # mutually exclusive
            elif turn["status"] == "comment":
                if turn["agent_id"] not in accepted:
                    objected.add(turn["agent_id"])

    # After each round: process objections (revise or rule), then post new proposing
    # If no objections this round, break — proceed to Phase 7 convergence wait
    if not objected:
        break
    # else: process objections, post revised proposing, loop continues for round 2
    # Round 2: after ruling, do NOT loop again — proceed to Phase 7
```

Repos will post `status="comment"` to object.

### Round 1 objections
Read all objections. For each:
- Is the objection specific? (names a dependency, cites a constraint, identifies
  a missing item)
- Does it change the critical path?

If yes to either: revise the work plan and post a new `status="proposing"`.
If no: hold the original plan and explain why in a `status="comment"`.

### Round 2 objections
A repo posting a second objection must say "round 2" explicitly.

Read very carefully. Ask: is this repo right and I missed something, or are
they optimizing for their own comfort over the team's critical path?

If right: revise and post new `status="proposing"`.
If overruling: post new `status="proposing"` with explicit reasoning:
```
[repo] has objected twice. Their concern: [restate it fairly].
I am overruling because: [specific reason — dependency, critical path, prior
agreement that binds this]. The work plan stands.
```

### Sub-negotiations for contested shared config
If two repos have conflicting plans for the same Ansible-managed config:
```python
neg_id_sub = open_negotiation(
    topic="[specific decision]",
    initiator_id="cc-manager",
    participants=["cc-ansible", "cc-{other}"],
    context="Spawned from staff meeting neg-XXXXXXXX. Decision needed: [what].",
    max_rounds=10
)
```
Note the sub-neg-id in the work plan artifact.

---

## Phase 7: Convergence and Close

After the final `status="proposing"` post (no more objections to process),
wait autonomously for all repos to accept.
**Do NOT stop and ask the human — loop until converged or timed out.**

```python
# last_id comes from the final post_position return (entry_id field)
# or from the last wait_for_turn result in Phase 6 — carry it forward, do not reset
accepted = set()  # repos that accepted the final hash
consecutive_timeouts = 0
MAX_CONSECUTIVE_TIMEOUTS = 5

while len(accepted) < len(participant_list):
    result = wait_for_turn(neg_id, "cc-manager", since_id=last_id, timeout_seconds=120)

    if result.get("timed_out"):
        consecutive_timeouts += 1
        if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
            break  # genuine silence — close with whoever accepted
        continue  # DO NOT stop; call again

    if result.get("impasse"):
        break

    consecutive_timeouts = 0
    last_id = result["last_id"]

    for turn in result["turns"]:
        if turn["status"] == "accepting" and turn["agent_id"] != "cc-manager":
            accepted.add(turn["agent_id"])

    if result.get("converged"):
        break  # server confirmed convergence

# Post cc-manager's own accepting turn to confirm the final hash (required for convergence)
# Then close:
close_negotiation(
    negotiation_id=neg_id,
    agent_id="cc-manager",
    artifact_name="staffmtg-{YYYYMMDD}.md",
    require_human_approval=True  # ALWAYS — human must approve before artifact is written
)
```

**Convergence = dismissal.** When the server declares convergence, all
participants see `converged=True` in their next `wait_for_turn` and exit their
persistent wait loops. No separate dismissal turn needed. They are done.

Report to human:
- Work plan summary (who does what, in what order)
- Any sub-negotiations spawned (neg-ids and topics)
- Any deferred items

### Save to claude-mem

```python
mcp__claude-mem__mem-store(
    content=f"""
Staff meeting neg-{neg_id} closed {date}
Participants: {participants}

PRIORITY ORDER:
{work_plan_priority_list}

DEPENDENCIES IDENTIFIED:
{dependency_graph}

SHARED CONFIG DECISIONS:
{shared_decisions}

SUB-NEGOTIATIONS SPAWNED:
{sub_neg_ids_and_topics or "none"}

DEFERRED:
{deferred_items or "none"}

Artifact: {artifact_path}
""",
    tags=["staffmtg", "work-plan"] + participant_repos
)
```

---

## Impasse / Long Meeting

If 20 rounds pass without convergence:
> "Meeting ran out of rounds. Unresolved: [specific item]. Recommend spawning
> a dedicated negotiation for that item and closing the meeting with everything
> else agreed."

---

## Quick Reference

| Phase | Action |
|-------|--------|
| 0 | Read participant list from CLAUDE.md |
| Intake | One question: agenda items or survey-driven? |
| Historian | Parallel claude-mem + list_negotiations + get_artifact |
| Open | `open_negotiation` → tell human to ping each session |
| Collect | Wait for all surveys posted as comment |
| Synthesize | Dependency graph, cross-pollination, ansible bottleneck |
| Work plan | Post as proposing with artifact markers |
| Objections | 2 comment rounds max, then rule |
| Close | Converge → close → claude-mem |
