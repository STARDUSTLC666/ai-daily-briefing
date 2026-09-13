import type {BriefingCard, BriefingSlide} from './video';

export const copyText = (value: unknown): string => String(value ?? '').replace(/\s+/g, ' ').trim();

// Paginate complete facts, never shorten a sentence or silently drop a card.
// Dense source material gets its own page and a smaller (still legible) type size.
export function factPages(cards: BriefingCard[]): BriefingCard[][] {
  const pages: BriefingCard[][] = [];
  let page: BriefingCard[] = [];
  let length = 0;
  for (const card of cards) {
    const size = copyText(card.body).length + copyText(card.title).length;
    if (page.length && (page.length === 3 || length + size > 220)) {
      pages.push(page);
      page = [];
      length = 0;
    }
    page.push(card);
    length += size;
  }
  if (page.length) pages.push(page);
  return pages.length ? pages : [[]];
}

export function pageAtFrame(frame: number, frames: number, count: number): number {
  return Math.min(Math.max(0, count - 1), Math.floor(Math.max(0, frame) / Math.max(1, frames) * count));
}

export function uniqueStories(slides: BriefingSlide[]): BriefingSlide[] {
  const seen = new Set<string>();
  return slides.filter(slide => {
    if (slide.kind !== 'news') return false;
    const key = copyText(slide.storyPosition ?? slide.title ?? slide.index);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export const headlineFontSize = (text: string, compact = false) => {
  const length = Array.from(copyText(text)).length;
  return compact ? (length > 38 ? 56 : length > 26 ? 66 : 80) : (length > 48 ? 62 : length > 32 ? 74 : 92);
};
