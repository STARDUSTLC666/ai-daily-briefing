import React from 'react';
import {CHAPTER_BOTTOM, CHAPTER_HEIGHT, DESIGN_WIDTH, SAFE_X, theme} from './theme';
import {cleanText, timelineGeometry, uniqueStories} from './layout';
import type {BriefingSlide, TimelineItem} from './types';

export const RegistrationMark: React.FC<{color?: string; size?: number}> = ({
  color = theme.vermilion,
  size = 24,
}) => (
  <svg width={size} height={size} viewBox="0 0 28 28" fill="none" style={{flexShrink: 0}}>
    <rect x="2" y="2" width="24" height="24" stroke={color} strokeWidth="2.5" />
    <rect x="8.5" y="8.5" width="11" height="11" fill={color} />
  </svg>
);

const formatCategoryLabel = (slide: BriefingSlide): string => {
  if (slide.kind === 'intro') return '头版导读';
  if (slide.kind === 'ticker' || slide.page?.kind === 'ticker') return '快讯速览';
  if (slide.page?.kind === 'evidence') return '原文核验';
  const raw = cleanText(slide.activeTab);
  if (!raw || raw.toLowerCase() === 'intro') return '今日要闻';
  if (raw.toLowerCase() === 'outro') return '播报收尾';
  return raw;
};

const formatClock = (seconds: number): string => {
  const whole = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  return `${String(Math.floor(whole / 60)).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`;
};

export const Masthead: React.FC<{
  slide: BriefingSlide;
  allSlides?: BriefingSlide[];
  dark?: boolean;
}> = ({slide, allSlides = [], dark = false}) => {
  const stories = uniqueStories(allSlides);
  const totalStories = slide.storyTotal || stories.length || 0;
  const position = slide.storyPosition || 0;
  const signalKind = cleanText(slide.signalKind || slide.signal_kind);
  const hasDisclaimer = Boolean(cleanText(slide.disclaimer) || signalKind);
  const isEvidence = slide.page?.kind === 'evidence';

  const inkColor = dark ? theme.slateInk : theme.ink;
  const secondaryColor = dark ? theme.slateSecondary : theme.inkSecondary;
  const mutedColor = dark ? theme.slateMuted : theme.muted;
  const ruleColor = dark ? theme.slateHairline : theme.ruleStrong;

  let statusLabel = '一手信源核验';
  let statusColor: string = dark ? '#7CE0C6' : theme.emerald;
  let statusBg: string = dark ? 'rgba(27, 107, 90, 0.24)' : theme.emeraldSoft;
  let statusBorder: string = dark ? 'rgba(92, 206, 179, 0.34)' : theme.emeraldBorder;

  if (hasDisclaimer) {
    statusLabel = signalKind === 'community_rumour' ? '社区风向 · 待核实' : '一线线索 · 待官方确认';
    statusColor = dark ? '#F2B872' : theme.amber;
    statusBg = dark ? 'rgba(180, 105, 30, 0.24)' : theme.amberSoft;
    statusBorder = theme.amberBorder;
  } else if (isEvidence) {
    statusLabel = '原始证据核验';
  } else if (cleanText(slide.risk)) {
    statusLabel = cleanText(slide.risk);
  }

  const counterText =
    slide.kind === 'news' && position > 0 && totalStories > 0
      ? `${String(position).padStart(2, '0')} / ${String(totalStories).padStart(2, '0')}`
      : totalStories > 0
        ? `${String(totalStories).padStart(2, '0')} STORIES`
        : 'OVERVIEW';

  return (
    <header
      style={{
        position: 'absolute',
        left: SAFE_X,
        right: SAFE_X,
        top: 40,
        height: 76,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        borderBottom: `${dark ? 1 : 2}px solid ${ruleColor}`,
        paddingBottom: 14,
      }}
    >
      <div style={{display: 'flex', alignItems: 'center', gap: 16, minWidth: 0}}>
        <RegistrationMark color={theme.vermilion} size={24} />
        <div style={{display: 'flex', alignItems: 'baseline', gap: 12}}>
          <span style={{fontFamily: theme.serif, fontSize: 31, fontWeight: 900, letterSpacing: -0.5, color: inkColor}}>
            AI 日报
          </span>
          <span style={{color: theme.vermilion, fontWeight: 900, fontSize: 20}}>·</span>
          <span style={{fontFamily: theme.mono, fontSize: 15, fontWeight: 700, letterSpacing: 2.4, color: secondaryColor}}>
            DAILY BRIEFING
          </span>
        </div>
        <span
          style={{
            marginLeft: 8,
            padding: '5px 14px',
            borderRadius: 4,
            background: dark ? theme.slateSurface : theme.tint,
            border: `1px solid ${dark ? theme.slateHairline : theme.hairline}`,
            fontSize: 16,
            fontWeight: 700,
            color: inkColor,
          }}
        >
          {formatCategoryLabel(slide)}
        </span>
      </div>

      <div style={{display: 'flex', alignItems: 'center', gap: 18, flexShrink: 0}}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '5px 13px',
            borderRadius: 4,
            background: statusBg,
            border: `1px solid ${statusBorder}`,
            color: statusColor,
            fontSize: 15,
            fontWeight: 700,
          }}
        >
          <span style={{width: 7, height: 7, borderRadius: '50%', background: statusColor, flexShrink: 0}} />
          <span>{statusLabel}</span>
        </div>
        <div
          style={{
            fontFamily: theme.mono,
            fontSize: 18,
            fontWeight: 800,
            color: slide.kind === 'news' ? theme.vermilion : secondaryColor,
            fontVariantNumeric: 'tabular-nums',
            letterSpacing: 0.8,
          }}
        >
          {counterText}
        </div>
        <div style={{height: 18, width: 1, background: dark ? theme.slateHairline : theme.hairline}} />
        <div style={{fontFamily: theme.mono, fontSize: 16, fontWeight: 600, color: mutedColor, fontVariantNumeric: 'tabular-nums'}}>
          {cleanText(slide.runLabel) || 'DAILY EDITION'}
        </div>
      </div>
    </header>
  );
};

const CLOCK_WIDTH = 96;
export const CHAPTER_TRACK_WIDTH = DESIGN_WIDTH - SAFE_X * 2 - CLOCK_WIDTH;

const chapterLabel = (item: TimelineItem): string => {
  if (item.kind === 'intro') return '导读';
  if (item.kind === 'outro') return '收尾';
  if (item.kind === 'ticker') return '速览';
  return cleanText(item.entity || item.label).replace(/^\d{1,2}\s*/, '') || '要闻';
};

export const ChapterTrack: React.FC<{
  slide: BriefingSlide;
  compositionFrame: number;
  fps: number;
  dark?: boolean;
}> = ({slide, compositionFrame, fps, dark = false}) => {
  const safeFps = fps || 30;
  const globalTime = Math.max(0, compositionFrame / safeFps);
  const rawItems: TimelineItem[] =
    slide.timelineItems && slide.timelineItems.length > 0
      ? slide.timelineItems
      : [{label: slide.title || 'AI 日报', start: 0, end: Math.max(1, slide.timelineTotal || slide.end || slide.duration || 1), kind: slide.kind}];

  const totalDuration = slide.timelineTotal || Math.max(1, ...rawItems.map((i) => i.end || 0), slide.end || 1);
  const geometry = timelineGeometry(rawItems, totalDuration, globalTime, CHAPTER_TRACK_WIDTH);
  const cellBorder = dark ? theme.slateHairline : theme.hairline;
  const activeText = dark ? theme.slateInk : theme.ink;
  const inactiveText = dark ? theme.slateMuted : theme.muted;
  const fontSize = rawItems.length >= 9 ? 13 : 14;

  return (
    <footer
      style={{
        position: 'absolute',
        left: SAFE_X,
        right: SAFE_X,
        bottom: CHAPTER_BOTTOM,
        height: CHAPTER_HEIGHT,
        display: 'flex',
        alignItems: 'stretch',
      }}
    >
      <div style={{position: 'relative', width: CHAPTER_TRACK_WIDTH, height: '100%'}}>
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            width: CHAPTER_TRACK_WIDTH,
            height: 4,
            background: dark ? 'rgba(238, 242, 247, 0.14)' : 'rgba(22, 24, 26, 0.12)',
            borderRadius: 2,
            overflow: 'hidden',
          }}
        >
          <div style={{width: Math.max(0, geometry.progressPx), height: '100%', background: theme.vermilion}} />
        </div>

        {geometry.segments.map((seg, index) => {
          const showText = seg.widthPx >= 52;
          const activeBg = dark ? theme.slateElevated : theme.vermilionSoft;
          const idleBg = dark ? 'rgba(39, 45, 56, 0.55)' : 'rgba(237, 233, 223, 0.58)';
          return (
            <div
              key={`${seg.start}-${index}`}
              style={{
                position: 'absolute',
                left: seg.leftPx,
                width: Math.max(0, seg.widthPx),
                top: 10,
                bottom: 0,
                paddingRight: index < geometry.segments.length - 1 ? 4 : 0,
              }}
            >
              <div
                style={{
                  width: '100%',
                  height: '100%',
                  borderRadius: 4,
                  background: seg.active ? activeBg : idleBg,
                  border: `1px solid ${seg.active ? theme.vermilionBorder : cellBorder}`,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: showText ? '0 10px' : '0 4px',
                  overflow: 'hidden',
                }}
              >
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    background: seg.active ? theme.vermilion : inactiveText,
                    flexShrink: 0,
                  }}
                />
                {showText ? (
                  <span
                    style={{
                      fontSize,
                      fontWeight: seg.active ? 800 : 600,
                      color: seg.active ? activeText : inactiveText,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {chapterLabel(rawItems[index])}
                  </span>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>

      <div
        style={{
          width: CLOCK_WIDTH,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'flex-end',
          fontFamily: theme.mono,
          fontSize: 15,
          fontWeight: 700,
          color: dark ? theme.slateSecondary : theme.inkSecondary,
          fontVariantNumeric: 'tabular-nums',
          paddingTop: 6,
        }}
      >
        {formatClock(geometry.currentTime)}
      </div>
    </footer>
  );
};
