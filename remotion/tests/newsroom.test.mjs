import assert from 'node:assert/strict';
import test from 'node:test';
import {factPages, pageAtFrame, uniqueStories} from '../src/newsroom-layout.ts';

test('pagination preserves every fact in order, including long facts', () => {
  const cards = Array.from({length: 13}, (_, i) => ({title: `事实 ${i}`, body: '具体事实。'.repeat(i * 5 + 1)}));
  const pages = factPages(cards);
  assert.deepEqual(pages.flat(), cards);
  assert.ok(pages.every(page => page.length <= 3));
  assert.ok(pages.length > 1);
});

test('page timing is bounded at transitions and the final frame', () => {
  assert.equal(pageAtFrame(-1, 120, 3), 0);
  assert.equal(pageAtFrame(39, 120, 3), 0);
  assert.equal(pageAtFrame(40, 120, 3), 1);
  assert.equal(pageAtFrame(119, 120, 3), 2);
  assert.equal(pageAtFrame(134, 120, 3), 2);
});

test('evidence pages do not inflate the cover story count', () => {
  const slides = [
    {kind: 'intro', index: 0},
    {kind: 'news', storyPosition: 1, title: '同一条新闻'},
    {kind: 'news', storyPosition: 1, title: '同一条新闻', page: {kind: 'evidence'}},
    {kind: 'news', storyPosition: 2, title: '另一条新闻'},
  ];
  assert.equal(uniqueStories(slides).length, 2);
});
