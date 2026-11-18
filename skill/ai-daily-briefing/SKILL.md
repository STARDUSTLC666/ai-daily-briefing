---
name: ai-daily-briefing
description: Run and maintain the layered, evidence-first AI Daily Briefing video pipeline. Use when asked to generate an AI briefing, prepare or serve the review workbench, verify a run package, render Remotion newsroom videos/covers, or check Bilibili upload readiness.
---

# AI Daily Briefing (`briefing` v0.2)

## Repository & Unified CLI

Work from the repository root containing `pyproject.toml`, `briefing/`, and `remotion/`. Always invoke Python via `py -3` on Windows.

```powershell
# Check runtime prerequisites or unattended run stage
py -3 -m briefing doctor
py -3 -m briefing doctor --status .\runs\YYYY-MM-DD

# End-to-end daily run
py -3 -m briefing run --date today
py -3 -m briefing run --date today --config .\sources.public-web.json --quality 4k

# Review / Agent workflow
py -3 -m briefing prepare --mode review --date today
py -3 -m briefing review --run-dir .\runs\YYYY-MM-DD
py -3 -m briefing review --finalize --run-dir .\runs\YYYY-MM-DD
py -3 -m briefing render --run-dir .\runs\YYYY-MM-DD --quality 1080p

# Verification, repurposing & Bilibili publishing
py -3 -m briefing verify --run-dir .\runs\YYYY-MM-DD
py -3 -m briefing repurpose --run-dir .\runs\YYYY-MM-DD
py -3 -m briefing publish --run-dir .\runs\YYYY-MM-DD --tid 231
```

## Core Quality Rules

- **Evidence first**: RSS, aggregators, and social feeds only supply leads. Claims must bind to verified primary/article excerpts, never unverified RSS blurbs.
- **Risk tiers**: `green` = fresh Tier-A official source in window; `yellow` = credible media/arXiv/personnel signal with conservative attribution; `red` = stale, undated, off-topic, or uncorroborated (excluded from video).
- **Copy hygiene**: Zero clickbait, zero ellipses (`…` / `...`), zero raw English prose, balanced brackets/quotes, and mandatory disclaimers on personnel/community signals.
- **Cover & subtitle geometry**: `cover.png` (4:3) and `cover-16x9.png` default to the lead story (`titles[0]`), and all Remotion Editorial Newsroom scenes keep body copy above `SUBTITLE_SAFE_BOTTOM` so burned-in subtitles never overlap text.
- **Safe publishing**: Always run `briefing publish --run-dir ...` (dry-run preflight) first; pass `--execute` only when explicitly authorized.

## Verification Commands

```powershell
py -3 -m unittest discover -s tests -v
npm --prefix .\remotion test
npm --prefix .\remotion run typecheck
```
