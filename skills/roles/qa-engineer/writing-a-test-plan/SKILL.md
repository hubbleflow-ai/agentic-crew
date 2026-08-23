---
name: writing-a-test-plan
description: How to turn acceptance criteria into a test plan, including the failure cases engineers skip. Read before testing a change.
allowed_tools: [read_ticket, sandbox_exec, add_ticket_comment]
---

# Writing a test plan

Start from the ticket's acceptance criteria. Each one becomes at least one
test. If a criterion cannot be turned into an assertion, say so in a ticket
comment — that is a scoping bug, and finding it is your job.

## The four the engineer skipped

For any endpoint or function, ask:

1. **Empty** — no items, empty string, zero, `None`.
2. **Too big** — a payload past the limit, a list of ten thousand.
3. **Wrong shape** — a string where a number was expected.
4. **Unavailable** — the database is down, the upstream times out.

The fourth catches the most real bugs and is skipped the most often, because it
is the one that is inconvenient to reproduce.

## Reporting

A failure report needs three things, and only three:

```
What I ran:      pytest tests/test_healthz.py::test_reports_db_down -q
What I expected: 503 with {"database": "unreachable"}
What happened:   200 with {"status": "ok"}
```

No diagnosis. No suggested fix. The engineer who wrote it will find the cause
faster than you will, and a wrong guess sends them down it anyway.

## Passing

Say what you ran and how many passed. "Looks good" is not a test result.
