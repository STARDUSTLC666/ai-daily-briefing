import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";

import {
  timelineGeometry,
  timelineItemIndexAtTime,
  timelineItemPixelGeometry,
  timelineProgressPercent,
  timelineTrackGeometry,
  timelineXAtTime,
} from "../src/timeline.ts";

const closeTo = (actual, expected, tolerance = 1e-9) => {
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);
};

test("timeline segments use the same continuous coordinate system as playback time", () => {
  const total = 159.936;
  const items = [
    {start: 0, end: 8.4},
    {start: 8.4, end: 42.96},
    {start: 42.96, end: 53.52},
    {start: 53.52, end: 77.4},
    {start: 77.4, end: 98.808},
    {start: 98.808, end: 125.28},
    {start: 125.28, end: 156.672},
    {start: 156.672, end: 159.936},
  ];
  const geometry = items.map((item) => timelineGeometry(item, total));

  closeTo(geometry[0].leftPercent, 0);
  for (let index = 0; index < geometry.length - 1; index += 1) {
    closeTo(geometry[index].rightPercent, geometry[index + 1].leftPercent);
  }
  closeTo(geometry.at(-1).rightPercent, 100);
  closeTo(geometry[1].widthPercent / geometry[2].widthPercent, 34.56 / 10.56);
  closeTo(timelineProgressPercent(80, total), (80 / total) * 100);
});

test("timeline geometry clamps invalid ranges without leaving the track", () => {
  assert.deepEqual(timelineGeometry({start: -4, end: 140}, 100), {
    leftPercent: 0,
    widthPercent: 100,
    rightPercent: 100,
  });
  closeTo(timelineProgressPercent(120, 100), 100);
});

test("seeking the player selects the news item at that global time", () => {
  const items = [
    {start: 0, end: 8.4},
    {start: 8.4, end: 42.96},
    {start: 42.96, end: 53.52},
    {start: 53.52, end: 77.4},
    {start: 77.4, end: 98.808},
    {start: 98.808, end: 125.28},
    {start: 125.28, end: 156.672},
    {start: 156.672, end: 159.936},
  ];

  assert.equal(timelineItemIndexAtTime(items, 0), 0);
  assert.equal(timelineItemIndexAtTime(items, 8.4), 1);
  assert.equal(timelineItemIndexAtTime(items, 50), 2);
  assert.equal(timelineItemIndexAtTime(items, 80), 4);
  assert.equal(timelineItemIndexAtTime(items, 120), 5);
  assert.equal(timelineItemIndexAtTime(items, 156.672), 7);
  assert.equal(timelineItemIndexAtTime(items, 159.936), 7);
});

test("timeline cards and progress fill share the player's full-width coordinate system", () => {
  const total = 159.936;
  const viewportWidth = 1920;
  const inset = 8;
  const time = 59.8;
  const track = timelineTrackGeometry(viewportWidth, inset);
  const progressX = timelineXAtTime(time, total, viewportWidth, inset);
  const google = timelineItemPixelGeometry({start: 53.52, end: 77.4}, total, viewportWidth, inset);

  assert.deepEqual(track, {leftPx: 8, rightPx: 1912, widthPx: 1904});
  closeTo(progressX, inset + (time / total) * track.widthPx);
  assert.ok(progressX >= google.leftPx && progressX < google.rightPx);

  const oldSqueezedTrackX = 272 + (time / total) * (1908 - 272);
  assert.ok(Math.abs(oldSqueezedTrackX - progressX) > 150);
});

test("timeline uses horizontal progress without a vertical playhead overlay", () => {
  const source = readFileSync(new URL("../src/video.tsx", import.meta.url), "utf8");
  const timeline = source.slice(source.indexOf("const Timeline"), source.indexOf("const Kicker"));

  assert.match(timeline, /const progressX = timelineXAtTime\(globalTime,\s*total,\s*DESIGN_WIDTH,\s*TIMELINE_TRACK_INSET\)/);
  assert.match(timeline, /width:\s*Math\.max\(0,\s*progressX - track\.leftPx\)/);
  assert.doesNotMatch(timeline, /left:\s*progressX/);
  assert.doesNotMatch(timeline, /transform:\s*"translateX\(-1px\)"/);
});

test("the rendered navigation uses the global player frame without text-jitter effects", () => {
  const source = readFileSync(new URL("../src/video.tsx", import.meta.url), "utf8");

  assert.match(source, /compositionFrame\s*\/\s*fps/);
  assert.match(source, /timelineXAtTime\(globalTime,\s*total,\s*DESIGN_WIDTH,\s*TIMELINE_TRACK_INSET\)/);
  assert.match(source, /timelineItemPixelGeometry\(item,\s*total,\s*DESIGN_WIDTH,\s*TIMELINE_TRACK_INSET\)/);
  assert.doesNotMatch(source, /width:\s*168,\s*\n\s*padding:\s*"17px 18px 12px 24px"/);
  assert.doesNotMatch(source, /slide\.start\s*\+\s*frame\s*\/\s*fps/);
  assert.doesNotMatch(source, /const\s+scan\s*=/);
  assert.doesNotMatch(source, /const\s+sheen\s*=/);
  assert.doesNotMatch(source, /FocusZoom/);
  assert.doesNotMatch(source, /item\.kind === "outro"[\s\S]*?hasClosing/);
});

test("opening copy and what-changed copy do not stay on animated transform layers", () => {
  const source = readFileSync(new URL("../src/video.tsx", import.meta.url), "utf8");
  const opening = source.slice(source.indexOf("const Opening"), source.indexOf("const Overview"));
  const newsBoardStart = source.indexOf("const NewsBoard");
  const newsBoard = source.slice(newsBoardStart, source.indexOf("const Evidence =", newsBoardStart));

  assert.match(source, /const MOTION_SETTLE_FRAMES = 30/);
  assert.match(source, /const settled = !animated \|\| localFrame >= MOTION_SETTLE_FRAMES/);
  assert.match(source, /const stableSurface = slide\.kind === "intro"/);
  assert.match(opening, /label="MORNING EDITION"[^>]*animated=\{false\}/);
  assert.equal((opening.match(/<MotionBlock[^>]*animated=\{false\}/g) || []).length, 4);
  assert.match(newsBoard, /<MotionBlock[^>]*animated=\{false\}[\s\S]*?WHAT CHANGED/);
  assert.match(newsBoard, /background: "rgba\(250,252,251,\.98\)"/);
});
