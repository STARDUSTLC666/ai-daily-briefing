"""Read-only diagnostics and restart planning for an agent-operated newsroom.

The planner never writes an audit, declares facts checked, or submits a video.
Every transition after an audit revalidates the existing artifact contract.
"""
from __future__ import annotations

from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from .config import load_config, load_sources, project_root


def _probe(command: list[str]) -> bool:
    try:
        result = subprocess.run(command, capture_output=True, timeout=15, check=False)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def runtime_doctor(config: Path | None = None) -> dict[str, Any]:
    from .browser_runtime import crawl_channel, installed_crawl_channel
    from .remotion_renderer import _browser_exe, _remotion_bin

    root = project_root()
    config = config or root / 'sources.public-web.json'
    cfg = load_config(config)
    checks = []

    def record(name: str, ok: bool, fix: str, *, required: bool = True) -> None:
        checks.append({'name': name, 'ok': bool(ok), 'required': required, 'fix': '' if ok else fix})

    record('python', sys.version_info >= (3, 11), 'Run scripts/bootstrap.ps1 with Python 3.11+.')
    for module in ('requests', 'PIL', 'edge_tts', 'crawl4ai', 'playwright'):
        record(module, importlib.util.find_spec(module) is not None, 'Run scripts/bootstrap.ps1.')
    node = shutil.which('node')
    record('node_22', bool(node) and _probe([node, '-e', 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)']), 'Install Node.js 22 or later.')
    for name in ('ffmpeg', 'ffprobe'):
        path = shutil.which(name)
        record(name, bool(path) and _probe([path, '-version']), f'Install {name} and add it to PATH.')
    record('remotion', _remotion_bin(root / 'remotion').exists(), 'Run npm --prefix remotion ci.')
    record('render_browser', bool(_browser_exe()), 'Set BRIEFING_CHROME to an installed Chrome or Edge executable.')
    downloaded_browser = False
    if importlib.util.find_spec('playwright'):
        # Isolate Playwright's event loop from the caller, including a running
        # async agent/crawler. This probe never opens a window or a profile.
        downloaded_browser = _probe([sys.executable, '-c',
            'from pathlib import Path; from playwright.sync_api import sync_playwright; '
            'p=sync_playwright().start(); ok=Path(p.chromium.executable_path).is_file(); p.stop(); raise SystemExit(0 if ok else 1)'])
    record('evidence_browser', bool(_browser_exe()), 'Set BRIEFING_CHROME to an installed Chrome or Edge executable.')
    channel = crawl_channel()
    crawl_ready = downloaded_browser if channel == 'chromium' else installed_crawl_channel() == channel
    record('crawl_browser', crawl_ready, 'Install Chrome/Edge, or run .venv/Scripts/python.exe -m playwright install chromium.')
    record('sources', bool(load_sources(config)), 'Enable at least one source in the edition configuration.')
    credentials = all(os.environ.get(key, '').strip() for key in ('BILI_CLIENT_ID', 'BILI_CLIENT_SECRET', 'BILI_ACCESS_TOKEN'))
    record('bilibili_credentials', credentials or (root / '.local/bilibili-credentials.clixml').exists(), 'Configure Bilibili Open Platform credentials with scripts/configure_bilibili.ps1.', required=False)
    return {
        'ok': all(row['ok'] for row in checks if row['required']),
        'profile': cfg.get('profile', 'social-audited'),
        'crawl_channel': channel,
        'checks': checks,
        'publishing_enabled': os.environ.get('BRIEFING_AUTO_PUBLISH') == '1',
        'note': 'Local checks only. Source connectivity, actual TTS and account authorization are verified during production.',
    }


def _object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError(f'{path.name} must contain an object')
    return value


def automation_status(run_dir: Path, *, config: Path | None = None) -> dict[str, Any]:
    from .agent_workflow import validate_agent_edit, verify_agent_edit_contract, verify_visual_agent_audit
    from .output_verify import verify_run_dir

    run_dir = Path(run_dir).resolve()
    config = (config or project_root() / 'sources.public-web.json').resolve()

    def result(stage: str, action: str, *arguments: str, errors: list[str] | None = None) -> dict[str, Any]:
        return {
            'ok': stage not in {'blocked', 'submission_unknown'},
            'stage': stage,
            'next_action': action,
            'arguments': list(arguments),
            'run_dir': str(run_dir),
            'errors': errors or [],
        }

    try:
        # A timed task never repackages an old date as today's news.
        if run_dir.name != datetime.now().strftime('%Y-%m-%d'):
            return result('blocked', 'inspect_date', errors=['Use the current local date for a daily production run.'])
        manifest = _object(run_dir / 'manifest.json')
        upload = _object(run_dir / 'bilibili-upload-result.json')
        if upload.get('status') == 'submitted' and upload.get('dry_run') is False:
            return result('submitted', 'none')
        if upload.get('status') == 'submitting':
            return result('submission_unknown', 'check_bilibili', errors=['Submission outcome is unknown; check the creator dashboard before any retry.'])
        if not manifest:
            if run_dir.exists() and any(run_dir.iterdir()):
                return result('blocked', 'inspect_partial_run', errors=['An incomplete run already exists; preserve its files before preparing again.'])
            return result('new', 'prepare-agent', '--date', run_dir.name, '--config', str(config), '--runs-dir', str(run_dir.parent))
        if manifest.get('qa_fixture') is True:
            return result('blocked', 'none', errors=['Design samples and QA fixtures must never enter daily publication.'])
        policy = manifest.get('agent_policy') or {}
        if policy.get('required') is not True:
            return result('blocked', 'inspect_existing_run', errors=['This run has no agent contract; do not overwrite it automatically.'])
        package = manifest.get('package_quality') or {}
        if package.get('ok') is False:
            return result('no_edition', 'none', errors=[str(package.get('reason') or 'No sufficiently verified news; skip this edition.')])
        if not (run_dir / 'review/agent-audit.json').exists():
            return result('source_review', 'review_sources_and_copy')
        if not (run_dir / 'review/agent-contract.json').exists():
            errors = validate_agent_edit(run_dir)
            return result('blocked', 'repair_source_audit', errors=errors) if errors else result('finalize', 'finalize-agent', '--run-dir', str(run_dir))
        errors = verify_agent_edit_contract(run_dir)
        if errors:
            return result('blocked', 'repair_source_audit', errors=errors)
        render = _object(run_dir / 'render-info.json')
        if not (run_dir / 'final.mp4').exists() or render.get('status') != 'ok':
            return result('render', 'render-agent', '--run-dir', str(run_dir), '--quality', '1080p')
        if not (run_dir / 'review/visual-qa/visual-qa-input.json').exists():
            return result('visual_package', 'prepare_visual_qa')
        if not (run_dir / 'review/visual-qa/visual-agent-audit.json').exists():
            return result('visual_review', 'inspect_video_and_frames')
        errors = verify_visual_agent_audit(run_dir)
        if errors:
            return result('blocked', 'repair_visual_audit', errors=errors)
        if (manifest.get('automatic_quality_gate') or {}).get('ok') is not True:
            return result('complete', 'complete-agent-run', '--run-dir', str(run_dir))
        verification = verify_run_dir(run_dir)
        if not verification.get('ok'):
            return result('blocked', 'inspect_quality_failure', errors=list(verification.get('errors') or []))
        return result('ready', 'bilibili-preflight', '--run-dir', str(run_dir))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return result('blocked', 'inspect_artifacts', errors=[str(exc)])
