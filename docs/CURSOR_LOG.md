# How Cursor was used

The rules ask every team to build in Cursor (with Space XAI). This file records, honestly and as it
happens, every use of Cursor during the build window, so a judge can check it against the git history.

**Context.** The sponsor credit for Cursor was never distributed during the build window (the "Get
sponsor credits" task on HackOS had no link or code; see the Help Cursor channel, 21-22 October). We
built on Cursor's free plan, where the agent allows only the Auto model and stops after a few sessions.
Claude Code was used alongside for most of the code, as the AI Usage Policy allows; every change is
reviewed, tested and explained by the author before it is committed.

## The working rules Cursor reads on every request

`.cursor/rules/greentrace.mdc` (always applied) and `.cursorignore` (keeps `.env` and logs out of
Cursor's context). They set the product rules: verdicts decided by code in `app/rules.py`, every
sponsor call in the trace, sample mode with the real logic, no number written in advance, tests that
turn red when the bug comes back.

## Log

| Date (UTC) | Who | What was asked of Cursor | What came of it |
|---|---|---|---|
| 2026-10-22 00:05 | Amir | Installed the working rules, opened the repository in Cursor only, Auto-run set to "Ask every time" | Commit 65b46e9 (rules and .cursorignore) |
| 2026-10-22 00:10 | Amir | First agent prompt: read the rules and the README, list what the repository holds and what sample mode still needs | The free plan answered with its usage limit; the work went on with Claude Code, opened beside Cursor |

<!-- Add one row per Cursor session: date, who, the request, the result (commit or file). -->
