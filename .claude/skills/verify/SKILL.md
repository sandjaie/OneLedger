---
name: verify
description: Run OneLedger's single exact-head final gate after review and any confirmation are clean, before the first push or a completion claim.
---

# Verify

Run from a clean, committed task branch:

```bash
scripts/verify.sh
```

Do not reconstruct the gate with separate commands or pass a mode. During implementation, run
affected tests directly. This command is the one comprehensive final run.

The runner selects scope from the diff against `develop`: strictly documentation-only changes run
documentation, static, Markdown and diff-size checks; every other change runs the complete API,
database and web gate as well. It reuses the task's `db-test` server and tears it down once.

Green means every applicable stage passed, the worktree remained clean, `HEAD` did not change, and
`.git/oneledger-verified-head` contains that exact commit. Pre-push checks the receipt; any later
commit invalidates it.

Never repeat the gate on an unchanged head. Never weaken, remove or skip a test or invariant to
make it pass. Report any failure and the stage that produced it.
