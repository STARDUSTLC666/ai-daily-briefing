import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {activeIndexAtTime, activeSlideAtTime, timelineGeometry} from '../src/layout.ts';

const closeTo = (actual, expected, tolerance = 1e-9) => {
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);
};

const SAMPLE_ITEMS = [
  {label: '开场', start: 0, end: 8.4, kind: 'intro'},
  {label: 'OpenAI', start: 8.4, end: 42.96, kind: 'news'},
  {label: 'Anthropic', start: 42.96, end: 53.52, kind: 'news'},
  {label: 'Google', start: 53.52, end: 77.4, kind: 'news'},
  {label: 'DeepSeek', start: 77.4, end: 98.808, kind: 'news'},
  {label: 'Meta', start: 98.808, end: 125.28, kind: 'news'},
  {label: 'Qwen', start: 125.28, end: 156.672, kind: 'news'},
  {label: '收尾', start: 156.672, end: 159.936, kind: 'outro'},
];

test('timelineGeometry uses continuous global coordinates matching playback time', () => {
  const total = 159.936;
  const trackWidth = 1648;
  const layout = timelineGeometry(SAMPLE_ITEMS, total, 59.8, trackWidth);

  closeTo(layout.segments[0].leftPercent, 0);
  closeTo(layout.segments[0].leftPx, 0);

  for (let i = 0; i < layout.segments.length - 1; i += 1) {
    closeTo(layout.segments[i].rightPercent, layout.segments[i + 1].leftPercent);
    closeTo(layout.segments[i].rightPx, layout.segments[i + 1].leftPx);
  }

  closeTo(layout.segments.at(-1).rightPercent, 100);
  closeTo(layout.segments.at(-1).rightPx, trackWidth);
  closeTo(
    layout.segments[1].widthPercent / layout.segments[2].widthPercent,
    34.56 / 10.56,
  );

  closeTo(layout.progressPercent, (59.8 / total) * 100);
  closeTo(layout.progressPx, (59.8 / total) * trackWidth);

  const googleSeg = layout.segments[3];
  assert.equal(layout.activeIndex, 3);
  assert.equal(googleSeg.active, true);
  assert.ok(layout.progressPx >= googleSeg.leftPx && layout.progressPx < googleSeg.rightPx);
});

test('timelineGeometry clamps out-of-bounds ranges and playback times cleanly', () => {
  const layout = timelineGeometry([{start: -10, end: 150}], 100, 140, 1200);
  assert.equal(layout.currentTime, 100);
  closeTo(layout.progressPercent, 100);
  closeTo(layout.progressPx, 1200);
  assert.equal(layout.segments[0].leftPercent, 0);
  assert.equal(layout.segments[0].widthPercent, 100);
  assert.equal(layout.segments[0].rightPercent, 100);
  assert.equal(layout.segments[0].fillPercent, 100);
});

test('activeSlideAtTime and activeIndexAtTime resolve exact chapter boundaries', () => {
  assert.equal(activeIndexAtTime(SAMPLE_ITEMS, -2), 0);
  assert.equal(activeIndexAtTime(SAMPLE_ITEMS, 0), 0);
  assert.equal(activeIndexAtTime(SAMPLE_ITEMS, 8.4), 1);
  assert.equal(activeIndexAtTime(SAMPLE_ITEMS, 50), 2);
  assert.equal(activeIndexAtTime(SAMPLE_ITEMS, 80), 4);
  assert.equal(activeIndexAtTime(SAMPLE_ITEMS, 120), 5);
  assert.equal(activeIndexAtTime(SAMPLE_ITEMS, 156.672), 7);
  assert.equal(activeIndexAtTime(SAMPLE_ITEMS, 159.936), 7);
  assert.equal(activeIndexAtTime(SAMPLE_ITEMS, 190), 7);

  assert.equal(activeSlideAtTime(SAMPLE_ITEMS, 59.8)?.label, 'Google');
  assert.equal(activeSlideAtTime([], 10), null);
});

test('ChapterTrack uses global playback frame and horizontal fill without vertical playhead or text-jitter', () => {
  const chromeSource = readFileSync(new URL('../src/chrome.tsx', import.meta.url), 'utf8');
  const videoSource = readFileSync(new URL('../src/video.tsx', import.meta.url), 'utf8');

  assert.match(videoSource, /const compositionFrame = useCurrentFrame\(\)/);
  assert.match(chromeSource, /compositionFrame\s*\/\s*safeFps/);
  assert.match(chromeSource, /timelineGeometry\(rawItems,\s*totalDuration,\s*globalTime,\s*CHAPTER_TRACK_WIDTH\)/);
  assert.match(chromeSource, /width:\s*Math\.max\(0,\s*geometry\.progressPx\)/);
  assert.doesNotMatch(chromeSource, /translateX\(-1px\)/);
  assert.doesNotMatch(chromeSource, /slide\.start\s*\+\s*frame/);
});
