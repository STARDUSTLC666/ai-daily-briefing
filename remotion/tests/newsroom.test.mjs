import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {
  adaptiveHeadlineSize,
  deriveCoverCopy,
  factPages,
  mergeOutroIntoFinalStory,
  pageAtFrame,
  totalFrames,
  uniqueStories,
} from '../src/layout.ts';
import {
  DESIGN_HEIGHT,
  DESIGN_WIDTH,
  SAFE_TOP,
  SUBTITLE_SAFE_BOTTOM,
  theme,
} from '../src/theme.ts';

test('pagination preserves every fact in order without truncation or ellipses', () => {
  const cards = Array.from({length: 13}, (_, i) => ({
    title: `事实 ${i}`,
    body: '具体核验事实内容。'.repeat(i * 3 + 1),
  }));
  const pages = factPages(cards);

  assert.deepEqual(pages.flat(), cards);
  assert.ok(pages.every((page) => page.length >= 1 && page.length <= 3));
  assert.ok(pages.length > 1);

  for (const page of pages) {
    if (page.length > 1) {
      const totalChars = page.reduce(
        (sum, card) => sum + Array.from(card.title).length + Array.from(card.body).length,
        0,
      );
      assert.ok(totalChars <= 190, `multi-card page exceeded compact budget: ${totalChars}`);
    }
  }
});

test('pageAtFrame stays bounded across negative pre-roll, transitions, and final frame', () => {
  assert.equal(pageAtFrame(-5, 120, 3), 0);
  assert.equal(pageAtFrame(0, 120, 3), 0);
  assert.equal(pageAtFrame(39, 120, 3), 0);
  assert.equal(pageAtFrame(40, 120, 3), 1);
  assert.equal(pageAtFrame(79, 120, 3), 1);
  assert.equal(pageAtFrame(80, 120, 3), 2);
  assert.equal(pageAtFrame(119, 120, 3), 2);
  assert.equal(pageAtFrame(134, 120, 3), 2);
  assert.equal(pageAtFrame(50, 120, 1), 0);
});

test('evidence and context pages do not inflate uniqueStories cover story count', () => {
  const slides = [
    {index: 0, kind: 'intro', start: 0, end: 5, duration: 5, activeTab: 'Intro', accent: '', title: 'AI 日报', caption: '', bottomTabs: [], bottomActive: '', page: {}},
    {index: 1, kind: 'news', storyPosition: 1, start: 5, end: 20, duration: 15, activeTab: 'OpenAI', accent: '', title: '头版第一条新闻', caption: '', bottomTabs: [], bottomActive: '', page: {kind: 'cards'}},
    {index: 2, kind: 'news', storyPosition: 1, start: 20, end: 30, duration: 10, activeTab: 'OpenAI', accent: '', title: '头版第一条新闻', caption: '', bottomTabs: [], bottomActive: '', page: {kind: 'evidence'}},
    {index: 3, kind: 'news', storyPosition: 2, start: 30, end: 48, duration: 18, activeTab: 'Anthropic', accent: '', title: '第二条核心新闻', caption: '', bottomTabs: [], bottomActive: '', page: {kind: 'cards'}},
    {index: 4, kind: 'ticker', storyPosition: 0, start: 48, end: 58, duration: 10, activeTab: '速览', accent: '', title: '快讯速览', caption: '', bottomTabs: [], bottomActive: '', page: {kind: 'ticker'}},
  ];

  const stories = uniqueStories(slides);
  assert.equal(stories.length, 2);
  assert.equal(stories[0].title, '头版第一条新闻');
  assert.equal(stories[1].title, '第二条核心新闻');
});

test('deriveCoverCopy selects the lead story (stories[0]), never the last story, and never emits ellipses', () => {
  const slides = [
    {
      index: 0,
      kind: 'intro',
      runLabel: '2026-11-18',
      start: 0,
      end: 6,
      duration: 6,
      activeTab: 'Intro',
      accent: '',
      title: 'AI 日报',
      caption: 'AI 日报',
      bottomTabs: [],
      bottomActive: '',
      timelineItems: [
        {label: 'OpenAI', entity: 'OpenAI', start: 6, end: 24, kind: 'news'},
        {label: 'DeepSeek', entity: 'DeepSeek', start: 24, end: 42, kind: 'news'},
        {label: 'Google', entity: 'Google', start: 42, end: 60, kind: 'news'},
      ],
      page: {},
    },
    {
      index: 1,
      kind: 'news',
      storyPosition: 1,
      start: 6,
      end: 24,
      duration: 18,
      activeTab: 'OpenAI',
      accent: '',
      title: 'OpenAI 发布全新推理架构并大幅下调 API 价格…',
      caption: '',
      bottomTabs: [],
      bottomActive: '',
      page: {lead: '推理吞吐提升三倍...'},
    },
    {
      index: 2,
      kind: 'news',
      storyPosition: 2,
      start: 24,
      end: 42,
      duration: 18,
      activeTab: 'DeepSeek',
      accent: '',
      title: 'DeepSeek 开源新一代代码评测集...',
      caption: '',
      bottomTabs: [],
      bottomActive: '',
      page: {},
    },
    {
      index: 3,
      kind: 'news',
      storyPosition: 3,
      start: 42,
      end: 60,
      duration: 18,
      activeTab: 'Google',
      accent: '',
      title: '末尾新闻不应成为封面主标题',
      caption: '',
      bottomTabs: [],
      bottomActive: '',
      page: {},
    },
  ];

  const copy = deriveCoverCopy({slides});
  assert.equal(copy.headline, 'OpenAI 发布全新推理架构并大幅下调 API 价格');
  assert.notEqual(copy.headline, '末尾新闻不应成为封面主标题');
  assert.equal(copy.subheadline, 'DeepSeek 开源新一代代码评测集');
  assert.equal(copy.storyCount, 3);
  assert.equal(copy.date, '2026-11-18');
  assert.deepEqual(copy.entities, ['OpenAI', 'DeepSeek', 'Google']);

  for (const text of [copy.headline, copy.subheadline, copy.badge, ...copy.highlights, ...copy.entities]) {
    assert.doesNotMatch(text, /…|\.{3}/);
  }
});

test('mergeOutroIntoFinalStory preserves custom closingText and matches exact totalFrames', () => {
  const slides = [
    {index: 0, kind: 'intro', start: 0, end: 5, duration: 5, activeTab: 'Intro', accent: '', title: 'AI 日报', caption: '', bottomTabs: [], bottomActive: '', page: {}},
    {index: 1, kind: 'news', storyPosition: 1, start: 5, end: 22.4, duration: 17.4, activeTab: 'OpenAI', accent: '', title: '主新闻', caption: '', bottomTabs: [], bottomActive: '', page: {}},
    {index: 2, kind: 'outro', start: 22.4, end: 26.2, duration: 3.8, activeTab: 'Outro', accent: '', title: '播送完毕', caption: '明天见', closingText: '本期核验完毕，欢迎在评论区留言交流，明天早上见。', bottomTabs: [], bottomActive: '', page: {}},
  ];

  const merged = mergeOutroIntoFinalStory(slides);
  assert.equal(merged.length, 2);
  assert.equal(merged[1].closingDuration, 3.8);
  assert.equal(merged[1].closingText, '本期核验完毕，欢迎在评论区留言交流，明天早上见。');
  assert.ok(Math.abs(merged[1].duration - 21.2) < 1e-9);

  const expectedFrames = Math.round(5 * 30) + Math.round(21.2 * 30);
  assert.equal(totalFrames({slides, fps: 30}), expectedFrames);
});

test('adaptiveHeadlineSize scales down smoothly for long headlines and narrower 4:3 covers', () => {
  const shortTitle = 'OpenAI 发布新模型';
  const mediumTitle = 'OpenAI 发布新一代推理模型并同步开放开发者 API 接口';
  const longTitle = 'OpenAI 正式发布新一代多模态推理模型并宣布企业级批处理接口价格下调百分之五十';

  const short16x9 = adaptiveHeadlineSize(shortTitle, {fourByThree: false});
  const medium16x9 = adaptiveHeadlineSize(mediumTitle, {fourByThree: false});
  const long16x9 = adaptiveHeadlineSize(longTitle, {fourByThree: false});

  assert.ok(short16x9 > medium16x9, `${short16x9} should be > ${medium16x9}`);
  assert.ok(medium16x9 > long16x9, `${medium16x9} should be > ${long16x9}`);

  for (const sample of [shortTitle, mediumTitle, longTitle]) {
    const size16x9 = adaptiveHeadlineSize(sample, {fourByThree: false});
    const size4x3 = adaptiveHeadlineSize(sample, {fourByThree: true});
    assert.ok(size4x3 < size16x9, `4:3 size (${size4x3}) should be smaller than 16:9 (${size16x9})`);
  }
});

test('editorial safe-zone geometry and word-wrapping invariants hold across components', () => {
  assert.equal(DESIGN_WIDTH, 1920);
  assert.equal(DESIGN_HEIGHT, 1080);
  assert.equal(SAFE_TOP, 148);
  assert.equal(SUBTITLE_SAFE_BOTTOM, 208);
  assert.equal(DESIGN_HEIGHT - SUBTITLE_SAFE_BOTTOM, 872);
  assert.equal(theme.slateBg, '#1F242D');
  assert.notEqual(theme.slateBg.toLowerCase(), '#000000');

  for (const relPath of [
    '../src/theme.ts',
    '../src/chrome.tsx',
    '../src/scenes.tsx',
    '../src/cover.tsx',
    '../src/video.tsx',
  ]) {
    const source = readFileSync(new URL(relPath, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /overflowWrap:\s*['"]anywhere['"]/);
    assert.doesNotMatch(source, /AI\s*这一天/);
  }
});
