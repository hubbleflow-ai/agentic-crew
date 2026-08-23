---
name: python-service-conventions
description: House style for Python services here - typing, error handling, and what the reviewer will send back. Read before writing code.
allowed_tools: [read_file, write_file, edit_file, sandbox_exec]
---

# Python service conventions

## Non-negotiable

- Type annotations on every function, including the return type.
- `ruff check` and `mypy` clean before you say you are finished.
- No bare `except:`. Catch what you can actually handle.
- `raise ... from err` inside an `except` block, so the original cause survives.

## Errors

Return the status code that describes what happened, and a body that says what
to do about it.

```python
if not project.exists():
    raise HTTPException(404, f"no project {project_id}")
```

Never swallow an exception to make a test pass. A test that passes because the
error vanished is worse than a failing one.

## Tests

Test behaviour, not implementation. A test that breaks when you rename a
private method is a maintenance cost with no benefit.

Name them after what they prove: `test_refuses_a_fourth_project`, not
`test_project_4`.

## Running things

`sandbox_exec` runs in a throwaway container with your workspace mounted. It
has no network. If something needs the internet, it will not work there, and
that is deliberate.

```
sandbox_exec("python -m pytest tests/ -q")
sandbox_exec("python -m ruff check src/")
```

## Before you hand off

Run the tests. Say what you ran and what it printed. "Should work" is not a
result, and the reviewer will ask.
