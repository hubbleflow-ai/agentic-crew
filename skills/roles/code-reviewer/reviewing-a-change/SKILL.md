---
name: reviewing-a-change
description: What to look for in a change and what to leave alone, plus how to phrase a request for changes. Read before reviewing.
allowed_tools: [github_read_pr, github_post_comment, github_request_changes, github_approve, sandbox_exec]
---

# Reviewing a change

## In order

1. **Does it do what the ticket asked?** Read the ticket first. A beautiful
   implementation of the wrong thing is the most expensive outcome.
2. **Is it correct?** Off-by-one, unhandled `None`, a swallowed exception, a
   race between two agents on one file.
3. **Does it fail safely?** What happens when the database is down, the input
   is empty, the payload is enormous.
4. **Can it be read in six months?**

## Leave alone

Formatting `ruff` already accepts. Naming you would have chosen differently.
Structure that is merely not your preference.

Style opinions dressed as review comments make review feel arbitrary, and the
engineer starts skimming the ones that matter.

## Phrasing

Say what is wrong, what will happen, and let the engineer choose the fix.

> Good: "If `pool.acquire()` raises here, the connection leaks — the `finally`
> is inside the `try`."
> Bad: "This should use a context manager."

The first is a defect. The second is a preference, and it hides the defect.

## Deciding

**Request changes** by default. It is the cheap outcome; approving something
broken is not.

**Approve** when it does what the ticket asked and fails safely. Not when it is
perfect.

Always say what you actually ran.
