---
name: working-in-the-workspace
description: How the shared /workspace volume is organised, and where to put files so other agents find them. Read before writing your first file on a project.
allowed_tools: [ls, read_file, write_file, edit_file, glob]
---

# Working in the workspace

Everyone on a project shares one directory: `/workspace`. It is a real volume,
not a scratch space — what you write is still there after your container has
gone, and every teammate can read it.

This is how work moves between agents. A backend engineer does not send the
reviewer a diff; the reviewer reads the files.

## Layout

```
/workspace
  ticket.md            the scope. read it first, do not edit it unless you are the EM
  src/                 implementation
  tests/               tests, mirroring src/
  notes/<role>.md      your working notes
  evidence/            screenshots and citations from research
```

## Rules that avoid collisions

**Write only under your own area, plus `src/` and `tests/`.** Two agents
editing one file will clobber each other — there is no locking.

**Put your reasoning in `notes/<your-role>.md`.** Not in chat. Chat is
ephemeral; a teammate spawned an hour from now will read your notes and will
never see your messages.

**Read before you write.** `ls` and `glob` cost almost nothing. Re-implementing
something a teammate finished twenty minutes ago costs a great deal.

## What does not belong here

Secrets, API keys, tokens. The volume is shared with every agent on the
project and is not encrypted.
