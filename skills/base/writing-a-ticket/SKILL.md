---
name: writing-a-ticket
description: The house format for a task ticket, and what makes acceptance criteria testable. Use when authoring or updating ticket.md.
allowed-tools: [read_file, write_file, edit_file]
---

# Writing a ticket

One ticket per project. It is the contract every other agent works from, so it
has to be readable by someone who never saw the founder's message.

## Format

```markdown
# <what is being built, in one line>

## Context
Why this is wanted. One paragraph. The founder's own words where possible.

## Scope
- What is included
- What is explicitly NOT included   <- this section prevents most rework

## API contract
Endpoints, payloads, status codes. Concrete enough to write a test against.

## Acceptance criteria
- [ ] Each one independently checkable

## Assignments
- backend_engineer: ...
- qa_engineer: ...
```

## Acceptance criteria that work

An acceptance criterion is testable or it is decoration.

> Bad: "the endpoint should be fast"
> Good: "GET /healthz returns within 200ms with the database reachable"

> Bad: "handle errors properly"
> Good: "returns 503 with `{"database": "unreachable"}` when the connection pool is exhausted"

If you cannot imagine the assertion, the criterion is not finished.

## The section people skip

**Out of scope.** Write it even when it feels obvious. Most rework in a crew
comes from an engineer helpfully building something nobody asked for, and the
only defence is having written down that it was not wanted.
