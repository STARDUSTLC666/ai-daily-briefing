import type {
  BriefingCard,
  BriefingSlide,
  BriefingVideoProps,
  CoverCopy,
  TimelineRange,
} from './types';

export const DEFAULT_CLOSING_TEXT =
  '今天的新闻播完了。置顶评论有个问题等你聊，觉得有用就点个关注，我们明天早上见。';

const clamp = (value: number, min: number, max: number): number =>
  Number.isFinite(value) ? Math.max(min, Math.min(max, value)) : min;

export const copyText = (value: unknown): string =>
  String(value ?? '').replace(/\s+/g, ' ').trim();

export const cleanText = (value: unknown): string =>
  String(value ?? '')
    .replace(/…+/g, '')
    .replace(/\.{3,}/g, '')
    .replace(/\s+/g, ' ')
    .trim();

export const charCount = (value: unknown): number => Array.from(copyText(value)).length;

export const visualTextUnits = (value: unknown): number => {
  let units = 0;
  for (const ch of cleanText(value)) {
    const code = ch.codePointAt(0) ?? 0;
    units += ch === ' ' ? 0.32 : code <= 0x7f ? (/[A-Z]/.test(ch) ? 0.68 : 0.56) : 1;
  }
  return units;
};

/**
 * Paginates complete facts in order without dropping or truncating any card.
 * Keeps character budget compact (<= 190 chars, <= 3 cards) so 3 cards never overflow SUBTITLE_SAFE_BOTTOM.
 */
export function factPages(
  cards: BriefingCard[],
  maxCardsPerPage = 3,
  maxCharsPerPage = 190,
): BriefingCard[][] {
  const safeMaxCards = Math.max(1, Math.floor(maxCardsPerPage));
  const safeMaxChars = Math.max(40, maxCharsPerPage);
  const pages: BriefingCard[][] = [];
  let current: BriefingCard[] = [];
  let charsOnPage = 0;

  for (const card of cards || []) {
    const weight = charCount(card.title) + charCount(card.body) + Math.min(18, charCount(card.meta));
    if (current.length > 0 && (current.length >= safeMaxCards || charsOnPage + weight > safeMaxChars)) {
      pages.push(current);
      current = [];
      charsOnPage = 0;
    }
    current.push(card);
    charsOnPage += weight;
  }

  if (current.length > 0) pages.push(current);
  return pages.length > 0 ? pages : [[]];
}

export function pageAtFrame(frame: number, usableFrames: number, pageCount: number): number {
  const safeCount = Math.max(1, Math.floor(pageCount));
  if (safeCount <= 1) return 0;
  const rawIndex = Math.floor((Math.max(0, frame) / Math.max(1, usableFrames)) * safeCount);
  return clamp(rawIndex, 0, safeCount - 1);
}

export function uniqueStories(slides: BriefingSlide[]): BriefingSlide[] {
  const seen = new Set<string>();
  return (slides || []).filter((slide) => {
    if (slide?.kind !== 'news') return false;
    const key = copyText(slide.storyPosition || slide.segmentIndex || slide.title || slide.index);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function mergeOutroIntoFinalStory(slides: BriefingSlide[]): BriefingSlide[] {
  const merged: BriefingSlide[] = [];
  for (const slide of slides || []) {
    if (slide.kind === 'outro' && merged.length > 0) {
      const prev = merged[merged.length - 1];
      const added = Math.max(0, Number(slide.duration) || 0);
      merged[merged.length - 1] = {
        ...prev,
        duration: (Number(prev.duration) || 0) + added,
        end: Math.max(Number(prev.end) || 0, Number(slide.end) || 0),
        closingDuration: (Number(prev.closingDuration) || 0) + added,
        closingText: cleanText(slide.closingText) || cleanText(prev.closingText) || DEFAULT_CLOSING_TEXT,
      };
      continue;
    }
    merged.push({...slide});
  }
  return merged;
}

export function slideDurationFrames(durationSeconds: number, fps = 30): number {
  const safeFps = Number.isFinite(fps) && fps > 0 ? fps : 30;
  const safeDuration = Number.isFinite(durationSeconds) && durationSeconds > 0 ? durationSeconds : 1;
  return Math.max(24, Math.round(safeDuration * safeFps));
}

export function totalFrames(props: Pick<BriefingVideoProps, 'slides' | 'fps'>): number {
  const fps = props?.fps || 30;
  const sum = mergeOutroIntoFinalStory(props?.slides || []).reduce(
    (acc, slide) => acc + slideDurationFrames(slide.duration, fps),
    0,
  );
  return Math.max(24, sum);
}

export type TimelineSegmentGeometry = {
  index: number;
  start: number;
  end: number;
  duration: number;
  leftPercent: number;
  widthPercent: number;
  rightPercent: number;
  leftPx: number;
  widthPx: number;
  rightPx: number;
  fillPercent: number;
  active: boolean;
};

export type TimelineTrackLayout = {
  totalDuration: number;
  trackWidth: number;
  currentTime: number;
  progressPercent: number;
  progressPx: number;
  activeIndex: number;
  segments: TimelineSegmentGeometry[];
};

export function activeIndexAtTime(items: TimelineRange[], globalTime: number): number {
  if (!items || items.length === 0) return -1;
  const safeTime = Number.isFinite(globalTime) ? globalTime : 0;
  const last = items.length - 1;
  for (let i = 0; i < items.length; i += 1) {
    const item = items[i];
    if (safeTime >= item.start && (safeTime < item.end || (i === last && safeTime <= item.end))) {
      return i;
    }
  }
  return safeTime < items[0].start ? 0 : last;
}

export function activeSlideAtTime<T extends TimelineRange>(slides: T[], globalTime: number): T | null {
  const idx = activeIndexAtTime(slides, globalTime);
  return idx >= 0 ? slides[idx] : null;
}

export function timelineGeometry(
  items: TimelineRange[],
  totalDuration: number,
  currentTime = 0,
  trackWidth = 1648,
): TimelineTrackLayout {
  const maxEnd = (items || []).reduce((m, i) => (Number.isFinite(i?.end) ? Math.max(m, i.end) : m), 0);
  const safeTotal = Number.isFinite(totalDuration) && totalDuration > 0 ? totalDuration : maxEnd > 0 ? maxEnd : 1;
  const safeWidth = Number.isFinite(trackWidth) && trackWidth > 0 ? trackWidth : 1;
  const clampedTime = clamp(currentTime, 0, safeTotal);
  const progressRatio = clampedTime / safeTotal;

  const ranges = (items || []).map((item) => {
    const start = clamp(item.start, 0, safeTotal);
    const end = clamp(Math.max(start, item.end), start, safeTotal);
    return {start, end};
  });
  const activeIndex = activeIndexAtTime(ranges, clampedTime);

  const segments: TimelineSegmentGeometry[] = ranges.map((r, index) => {
    const duration = r.end - r.start;
    const leftPercent = (r.start / safeTotal) * 100;
    const widthPercent = (duration / safeTotal) * 100;
    const leftPx = (leftPercent / 100) * safeWidth;
    const widthPx = (widthPercent / 100) * safeWidth;
    const fillPercent =
      duration <= 0 ? (clampedTime >= r.end ? 100 : 0) : clamp((clampedTime - r.start) / duration, 0, 1) * 100;

    return {
      index,
      start: r.start,
      end: r.end,
      duration,
      leftPercent,
      widthPercent,
      rightPercent: (r.end / safeTotal) * 100,
      leftPx,
      widthPx,
      rightPx: leftPx + widthPx,
      fillPercent,
      active: index === activeIndex,
    };
  });

  return {
    totalDuration: safeTotal,
    trackWidth: safeWidth,
    currentTime: clampedTime,
    progressPercent: progressRatio * 100,
    progressPx: progressRatio * safeWidth,
    activeIndex,
    segments,
  };
}

export function adaptiveHeadlineSize(
  title: string,
  options: {fourByThree?: boolean; story?: boolean} = {},
): number {
  const {fourByThree = false, story = false} = options;
  const units = visualTextUnits(title);
  if (story) {
    return units <= 16 ? 70 : units <= 26 ? 60 : units <= 38 ? 52 : units <= 52 ? 46 : 40;
  }
  if (fourByThree) {
    return units <= 14 ? 76 : units <= 22 ? 66 : units <= 34 ? 56 : units <= 48 ? 48 : 42;
  }
  return units <= 14 ? 90 : units <= 22 ? 78 : units <= 34 ? 66 : units <= 48 ? 56 : 48;
}

/**
 * Selects stories[0] (the lead story, never stories.at(-1)) as default cover headline
 * and strips any ellipses ("…" or "...").
 */
export function deriveCoverCopy(
  props: Pick<BriefingVideoProps, 'slides' | 'cover'>,
): Required<CoverCopy> {
  const slides = props?.slides || [];
  const stories = uniqueStories(slides);
  const leadStory = stories[0];
  const secondStory = stories[1];
  const rawCover = props?.cover || {};

  const storyCount =
    typeof rawCover.storyCount === 'number' && Number.isFinite(rawCover.storyCount)
      ? Math.max(0, Math.round(rawCover.storyCount))
      : stories.length;

  const timelineEntities = (slides[0]?.timelineItems || [])
    .filter((item) => item.kind === 'news')
    .map((item) => cleanText(item.entity || item.label).replace(/^\d{1,2}\s*/, ''))
    .filter(Boolean);

  const introCardTitles = (slides[0]?.page?.cards || []).map((c) => cleanText(c.title)).filter(Boolean);
  const storyTabs = stories.map((s) => cleanText(s.activeTab)).filter(Boolean);
  const rawEntities = rawCover.entities?.length
    ? rawCover.entities
    : timelineEntities.length
      ? timelineEntities
      : storyTabs.length
        ? storyTabs
        : introCardTitles;

  const entities = Array.from(new Set(rawEntities.map(cleanText).filter(Boolean))).slice(0, 6);

  const introCardBodies = (slides[0]?.page?.cards || [])
    .map((c) => cleanText(c.body || c.title))
    .filter(Boolean);
  const defaultHighlights = stories.length
    ? stories.slice(0, 3).map((s) => cleanText(s.title))
    : introCardBodies.slice(0, 3);

  const highlights = (rawCover.highlights?.length ? rawCover.highlights : defaultHighlights)
    .map(cleanText)
    .filter(Boolean)
    .slice(0, 3);

  return {
    eyebrow: cleanText(rawCover.eyebrow) || 'AI 日报 · DAILY BRIEFING',
    headline: cleanText(rawCover.headline) || cleanText(leadStory?.title) || '今日 AI 核心动态',
    subheadline:
      cleanText(rawCover.subheadline) ||
      cleanText(secondStory?.title || leadStory?.page?.lead) ||
      '模型 · 产品 · 研究 · 产业一手核验',
    badge: cleanText(rawCover.badge) || (storyCount > 0 ? `${storyCount} 条经核验 AI 动态` : '经核验 AI 动态'),
    date: cleanText(rawCover.date) || cleanText(slides[0]?.runLabel) || 'DAILY EDITION',
    storyCount,
    highlights,
    entities,
  };
}
