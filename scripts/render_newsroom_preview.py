"""Render an explicitly non-publishable design sample through the real pipeline.

No synthetic facts, dummy audio, source audits or release attestations are made.
The input is a small, separately reviewed editorial example, not today's queue.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from briefing.audio_quality import analyze_audio
from briefing.render import (
    _browser_slides, _concat_audio, _duration, _edge_tts,
    _write_news_manuscript, _write_script_json, _write_srt,
)
from briefing.remotion_renderer import render_remotion_video


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding='utf-8-sig'))
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit('Use an empty sample output directory; existing output is preserved.')
    out.mkdir(parents=True, exist_ok=True)
    render = out / 'render'
    render.mkdir()
    manifest = {'qa_fixture': True, 'publish_allowed': False, 'kind': 'newsroom_design_preview',
                'edition_date': data['edition_date'], 'source_notes': data['source_notes']}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    segments = data['segments']
    _write_news_manuscript(out, segments)
    (out / 'cover-copy.json').write_text(json.dumps(data['cover'], ensure_ascii=False, indent=2), encoding='utf-8')
    print('Generating real Edge TTS narration and subtitles...', flush=True)
    audios, subtitle_paths, voice = _edge_tts(render, segments)
    durations = [_duration(audio) for audio in audios]
    narration = _concat_audio(render, audios)
    srt = _write_srt(out, segments, durations, subtitle_paths=subtitle_paths)
    script = _write_script_json(out, segments, durations)
    slides = _browser_slides(out, segments, durations, '1080p')
    for slide in slides:
        slide['runLabel'] = data['edition_date']
        if slide['kind'] == 'news':
            slide['title'] = slide['page'].get('title') or slide['title']
    print(f'Rendering {len(slides)} scenes, {sum(durations):.1f} seconds...', flush=True)
    result = render_remotion_video(out, render, slides, durations, narration, srt, script, '1080p', f'edge-tts:{voice}')
    (out / 'render-info.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    audio = analyze_audio(out / 'final.mp4', out / 'audio-quality.json')
    print(json.dumps({'render': result, 'audio_ok': audio.get('ok'), 'publish_allowed': False}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
