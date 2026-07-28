export type TimelineRange = {
  start: number;
  end: number;
};

const bounded = (value: number, min: number, max: number) =>
  Math.max(min, Math.min(max, Number.isFinite(value) ? value : min));

export const timelineProgressPercent = (time: number, total: number) => {
  const safeTotal = Number.isFinite(total) && total > 0 ? total : 1;
  return (bounded(time, 0, safeTotal) / safeTotal) * 100;
};

export const timelineGeometry = (item: TimelineRange, total: number) => {
  const safeTotal = Number.isFinite(total) && total > 0 ? total : 1;
  const start = bounded(item.start, 0, safeTotal);
  const end = bounded(Math.max(start, item.end), start, safeTotal);
  return {
    leftPercent: (start / safeTotal) * 100,
    widthPercent: ((end - start) / safeTotal) * 100,
    rightPercent: (end / safeTotal) * 100,
  };
};

export const timelineTrackGeometry = (viewportWidth: number, inset = 0) => {
  const safeWidth = Number.isFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 1;
  const safeInset = bounded(inset, 0, safeWidth / 2);
  const widthPx = Math.max(0, safeWidth - safeInset * 2);
  return {
    leftPx: safeInset,
    rightPx: safeInset + widthPx,
    widthPx,
  };
};

export const timelineXAtTime = (time: number, total: number, viewportWidth: number, inset = 0) => {
  const track = timelineTrackGeometry(viewportWidth, inset);
  return track.leftPx + (timelineProgressPercent(time, total) / 100) * track.widthPx;
};

export const timelineItemPixelGeometry = (
  item: TimelineRange,
  total: number,
  viewportWidth: number,
  inset = 0,
) => {
  const track = timelineTrackGeometry(viewportWidth, inset);
  const itemGeometry = timelineGeometry(item, total);
  const leftPx = track.leftPx + (itemGeometry.leftPercent / 100) * track.widthPx;
  const widthPx = (itemGeometry.widthPercent / 100) * track.widthPx;
  return {
    leftPx,
    rightPx: leftPx + widthPx,
    widthPx,
  };
};

export const timelineItemIndexAtTime = (items: TimelineRange[], time: number) => {
  if (!items.length) return -1;
  const lastIndex = items.length - 1;
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    const atOrAfterStart = time >= item.start;
    const beforeEnd = time < item.end;
    const atFinalEnd = index === lastIndex && time <= item.end;
    if (atOrAfterStart && (beforeEnd || atFinalEnd)) return index;
  }
  return time < items[0].start ? 0 : lastIndex;
};
