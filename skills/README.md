# Skills

Instructions the crew loads **on demand**.

A system prompt is paid for on every single model call. A skill is paid for
only when it is opened. That difference is what makes it possible to give an
agent a lot of specific knowledge without making every one of its turns
expensive.

`SkillsMiddleware` reads only the YAML frontmatter of each `SKILL.md` at
`before_agent` and injects a one-line index into the system prompt. The body —
which can be long — is fetched with `read_file` if and only if the model
decides it is relevant.

```
skills/
  base/     every role gets these
  roles/    layered on top, per role
```

Sources apply in order, so a role skill of the same name overrides a base one.

**Writing one.** The `description` is the only thing the model sees before it
chooses, so it should say *when to use this*, not what it contains. "How to
scope a vague founder request into a ticket" is useful. "Ticket documentation"
is not.
