---
name: ai-daily-briefing
description: Run and maintain a local, evidence-first AI news video pipeline. Use when asked to generate an AI briefing, audit sources, verify a run package, prepare Bilibili metadata, test rendering, or check upload readiness.
---

# Daily Bilibili Briefing

## Repo and default command

Work from the repository root that contains `pyproject.toml` and the `briefing/` package.

Main run:

```powershell
py -3 -m briefing run --date today --target bilibili
```

Useful variants:

```powershell
py -3 -m briefing run --date today --target bilibili --skip-render
py -3 -m briefing run --date today --target bilibili --quality 4k
py -3 -m briefing verify-run --run-dir .\runs\YYYY-MM-DD
py -3 -m briefing bilibili-publish --run-dir .\runs\YYYY-MM-DD --tid 231
```

Use `py -3`, not bare `python`.

## Quality rules

- Treat RSS as input only; publishability depends on source tier, freshness, first-seen time, evidence links, and old-news/future-date checks.
- Green: official/strong evidence in the freshness window. Yellow: media or research item requiring conservative wording. Red: community-only, no-date stale, off-topic, or insufficient evidence; do not put red items in the main video.
- Keep domestic and foreign sources: official model/release feeds, Hugging Face organization model updates, GitHub changelogs/releases, arXiv, Chinese tech media, and community clue sources.
- Do not let arXiv, Hacker News, Reddit, or a single media source dominate the selected video. Prefer source/entity diversity and official product/model updates.
- After rendering, verify that Bilibili time navigation is based on `script.json` actual timestamps, not estimates.
- The unattended voice default is Edge `zh-CN-XiaoxiaoNeural` at `+8%` and original pitch. IndexTTS2 profiles are explicit opt-in only.

## Expected outputs

For each run directory `runs\YYYY-MM-DD\`, confirm these exist and are non-empty:

- `final.mp4`, `cover.png`, `subtitles.srt`, `script.json`
- Remotion 成片还应输出 `cover-16x9.png`；`cover.png` 是 B站首页推荐使用的 4:3 主封面
- `bilibili.md`, `bilibili.json`, `pinned-comment.md`
- `sources.md`, `fact-check.md`, `manifest.json`

Always run:

```powershell
py -3 -m unittest discover -s tests -v
py -3 -m briefing verify-run --run-dir .\runs\YYYY-MM-DD
```

For Bilibili upload readiness, dry-run first. Real upload requires environment variables:

```powershell
$env:BILI_CLIENT_ID = "..."
$env:BILI_CLIENT_SECRET = "..."
$env:BILI_ACCESS_TOKEN = "..."
$env:BILI_TID = "231"
```

Then use `--execute` only after the user confirms.

## Daily automation

The fallback Windows workflow installs a 07:00 generation task and an 08:00 publish recheck. Tasks remain disabled unless `-Enable` is supplied:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_scheduled_task.ps1
```

If debugging automation, inspect `logs\daily-YYYY-MM-DD.log`, the latest `runs\YYYY-MM-DD`, and Windows Task Scheduler state.
