import React from 'react';
import {AbsoluteFill} from 'remotion';
import {RegistrationMark} from './chrome';
import {adaptiveHeadlineSize, deriveCoverCopy} from './layout';
import {
  COVER_43_WIDTH,
  DESIGN_HEIGHT,
  DESIGN_WIDTH,
  EDITORIAL_WORD_WRAP,
  FontFaces,
  SAFE_X,
  theme,
} from './theme';
import type {BriefingVideoProps} from './types';

const CoverMasthead: React.FC<{date: string; storyCount: number; badge: string; insetX: number}> = ({
  date,
  storyCount,
  badge,
  insetX,
}) => (
  <header
    style={{
      position: 'absolute',
      left: insetX,
      right: insetX,
      top: 40,
      height: 74,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      borderBottom: `3px solid ${theme.ruleStrong}`,
      paddingBottom: 14,
    }}
  >
    <div style={{display: 'flex', alignItems: 'center', gap: 16}}>
      <RegistrationMark color={theme.vermilion} size={26} />
      <span style={{fontFamily: theme.serif, fontSize: 34, fontWeight: 900, letterSpacing: -0.6, color: theme.ink}}>
        AI 日报
      </span>
      <span style={{color: theme.vermilion, fontWeight: 900, fontSize: 22}}>·</span>
      <span style={{fontFamily: theme.mono, fontSize: 16, fontWeight: 800, letterSpacing: 2.6, color: theme.inkSecondary}}>
        DAILY BRIEFING
      </span>
    </div>

    <div style={{display: 'flex', alignItems: 'center', gap: 18}}>
      <span
        style={{
          padding: '6px 14px',
          borderRadius: 4,
          background: theme.emeraldSoft,
          border: `1px solid ${theme.emeraldBorder}`,
          color: theme.emerald,
          fontSize: 16,
          fontWeight: 800,
        }}
      >
        {storyCount > 0 ? `${storyCount} 条经核验 AI 动态` : badge}
      </span>
      <span style={{fontFamily: theme.mono, fontSize: 18, fontWeight: 700, color: theme.inkSecondary, fontVariantNumeric: 'tabular-nums'}}>
        {date}
      </span>
    </div>
  </header>
);

const EntityPills: React.FC<{entities: string[]}> = ({entities}) => (
  <>
    {entities.map((entity, idx) => (
      <div
        key={`${entity}-${idx}`}
        style={{
          height: 44,
          padding: '0 16px',
          borderRadius: 4,
          background: theme.card,
          border: `1px solid ${theme.hairline}`,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 19,
          fontWeight: 750,
          color: theme.ink,
          whiteSpace: 'nowrap',
          flexShrink: 0,
        }}
      >
        <span style={{width: 7, height: 7, borderRadius: '50%', background: idx % 2 === 0 ? theme.vermilion : theme.emerald}} />
        <span>{entity}</span>
      </div>
    ))}
  </>
);

export const BriefingCover: React.FC<BriefingVideoProps> = (props) => {
  const copy = deriveCoverCopy(props);
  const headlineSize = adaptiveHeadlineSize(copy.headline, {fourByThree: false});
  const highlights = copy.highlights.slice(0, 3);
  const entities = copy.entities.slice(0, 6);

  return (
    <AbsoluteFill style={{width: DESIGN_WIDTH, height: DESIGN_HEIGHT, background: theme.paper, color: theme.ink, fontFamily: theme.sans, overflow: 'hidden'}}>
      <FontFaces />
      <CoverMasthead date={copy.date} storyCount={copy.storyCount} badge={copy.badge} insetX={SAFE_X} />

      <div style={{position: 'absolute', left: SAFE_X, right: SAFE_X, top: 146, bottom: 132, overflow: 'hidden', display: 'grid', gridTemplateColumns: '1080px minmax(0, 1fr)', gap: 48}}>
        <div style={{height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column', justifyContent: 'space-between'}}>
          <div style={{overflow: 'hidden', maxHeight: 620}}>
            <div style={{display: 'inline-flex', alignItems: 'center', gap: 10, fontFamily: theme.mono, fontSize: 16, fontWeight: 800, letterSpacing: 2.8, color: theme.vermilion, marginBottom: 18}}>
              <span style={{width: 28, height: 3, background: theme.vermilion, display: 'inline-block'}} />
              <span>LEAD DISPATCH · 今日头版</span>
            </div>

            <h1
              style={{
                margin: 0,
                fontSize: headlineSize,
                lineHeight: 1.18,
                fontWeight: 900,
                letterSpacing: -1.8,
                color: theme.ink,
                maxHeight: Math.round(headlineSize * 1.18 * 3 + 8),
                overflow: 'hidden',
                ...EDITORIAL_WORD_WRAP,
              }}
            >
              {copy.headline}
            </h1>

            <div style={{width: 88, height: 6, background: theme.vermilion, margin: '26px 0 22px'}} />

            <p style={{margin: 0, fontSize: 33, lineHeight: 1.42, fontWeight: 650, color: theme.inkSecondary, maxHeight: 100, overflow: 'hidden', ...EDITORIAL_WORD_WRAP}}>
              {copy.subheadline}
            </p>
          </div>

          <div style={{display: 'flex', alignItems: 'center', gap: 22, paddingTop: 18, borderTop: `1px solid ${theme.hairline}`}}>
            <span style={{fontFamily: theme.serif, fontSize: 72, lineHeight: 0.95, fontWeight: 900, color: theme.vermilion, fontVariantNumeric: 'tabular-nums'}}>
              {copy.storyCount > 0 ? String(copy.storyCount).padStart(2, '0') : '24H'}
            </span>
            <div>
              <div style={{fontSize: 22, fontWeight: 850, color: theme.ink}}>
                {copy.storyCount > 0 ? `${copy.storyCount} 条经核验 AI 动态` : copy.badge}
              </div>
              <div style={{marginTop: 4, fontFamily: theme.mono, fontSize: 14, fontWeight: 700, letterSpacing: 1.6, color: theme.muted}}>
                VERIFIED PRIMARY SOURCES · 24H EDITION
              </div>
            </div>
          </div>
        </div>

        <div style={{borderLeft: `1px solid ${theme.hairline}`, paddingLeft: 44, height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column'}}>
          <div style={{fontFamily: theme.mono, fontSize: 15, fontWeight: 800, letterSpacing: 2.4, color: theme.inkSecondary, paddingBottom: 14, borderBottom: `2px solid ${theme.ruleStrong}`}}>
            TODAY&apos;S SIGNALS · 本期要闻
          </div>
          <div style={{flex: 1, minHeight: 0, marginTop: 18, display: 'flex', flexDirection: 'column', gap: 18, overflow: 'hidden'}}>
            {highlights.map((item, idx) => {
              const accent = idx === 0 ? theme.vermilion : idx === 1 ? theme.emerald : theme.ink;
              return (
                <div key={`${item}-${idx}`} style={{background: theme.card, border: `1px solid ${theme.hairline}`, borderLeft: `5px solid ${accent}`, borderRadius: '0 6px 6px 0', padding: '22px 24px', overflow: 'hidden'}}>
                  <div style={{fontFamily: theme.mono, fontSize: 15, fontWeight: 800, color: accent, marginBottom: 8}}>
                    SIGNAL {String(idx + 1).padStart(2, '0')}
                  </div>
                  <div style={{fontSize: 26, lineHeight: 1.38, fontWeight: 780, color: theme.ink, maxHeight: 110, overflow: 'hidden', ...EDITORIAL_WORD_WRAP}}>
                    {item}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <footer style={{position: 'absolute', left: SAFE_X, right: SAFE_X, bottom: 34, height: 72, borderTop: `2px solid ${theme.ruleStrong}`, paddingTop: 14, display: 'flex', alignItems: 'center', gap: 12, overflow: 'hidden'}}>
        <span style={{fontFamily: theme.mono, fontSize: 14, fontWeight: 800, letterSpacing: 2, color: theme.muted, marginRight: 6, flexShrink: 0}}>
          ENTITIES
        </span>
        <EntityPills entities={entities} />
      </footer>
    </AbsoluteFill>
  );
};

export const BriefingCover43: React.FC<BriefingVideoProps> = (props) => {
  const copy = deriveCoverCopy(props);
  const insetX = 68;
  const headlineSize = adaptiveHeadlineSize(copy.headline, {fourByThree: true});
  const highlights = copy.highlights.slice(0, 3);
  const entities = copy.entities.slice(0, 6);

  return (
    <AbsoluteFill style={{width: COVER_43_WIDTH, height: DESIGN_HEIGHT, background: theme.paper, color: theme.ink, fontFamily: theme.sans, overflow: 'hidden'}}>
      <FontFaces />
      <CoverMasthead date={copy.date} storyCount={copy.storyCount} badge={copy.badge} insetX={insetX} />

      <div style={{position: 'absolute', left: insetX, right: insetX, top: 138, height: 410, overflow: 'hidden', display: 'flex', flexDirection: 'column'}}>
        <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16}}>
          <span style={{fontFamily: theme.mono, fontSize: 16, fontWeight: 800, letterSpacing: 2.6, color: theme.vermilion}}>
            LEAD DISPATCH · 今日头版
          </span>
          <span style={{fontFamily: theme.mono, fontSize: 18, fontWeight: 900, color: theme.vermilion}}>
            {copy.storyCount > 0 ? `${String(copy.storyCount).padStart(2, '0')} VERIFIED STORIES` : '24H EDITION'}
          </span>
        </div>

        <h1
          style={{
            margin: 0,
            fontSize: headlineSize,
            lineHeight: 1.18,
            fontWeight: 900,
            letterSpacing: -1.4,
            color: theme.ink,
            maxHeight: Math.round(headlineSize * 1.18 * 3 + 6),
            overflow: 'hidden',
            ...EDITORIAL_WORD_WRAP,
          }}
        >
          {copy.headline}
        </h1>

        <p style={{margin: '18px 0 0', fontSize: 29, lineHeight: 1.38, fontWeight: 650, color: theme.inkSecondary, maxHeight: 82, overflow: 'hidden', ...EDITORIAL_WORD_WRAP}}>
          {copy.subheadline}
        </p>
      </div>

      <div style={{position: 'absolute', left: 0, right: 0, top: 566, height: 330, background: theme.tint, borderTop: `3px solid ${theme.ruleStrong}`, borderBottom: `1px solid ${theme.hairline}`, padding: `22px ${insetX}px`, overflow: 'hidden'}}>
        <div style={{fontFamily: theme.mono, fontSize: 15, fontWeight: 800, letterSpacing: 2.4, color: theme.inkSecondary, marginBottom: 16}}>
          TODAY&apos;S SIGNALS · 本期看点
        </div>
        <div style={{display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 22, height: 244}}>
          {highlights.map((item, idx) => {
            const accent = idx === 0 ? theme.vermilion : idx === 1 ? theme.emerald : theme.ink;
            return (
              <div key={`${item}-${idx}`} style={{background: theme.card, border: `1px solid ${theme.hairline}`, borderTop: `4px solid ${accent}`, borderRadius: '0 0 6px 6px', padding: '18px 20px', overflow: 'hidden'}}>
                <div style={{fontFamily: theme.mono, fontSize: 15, fontWeight: 800, color: accent, marginBottom: 10}}>
                  {String(idx + 1).padStart(2, '0')}
                </div>
                <div style={{fontSize: 24, lineHeight: 1.36, fontWeight: 780, color: theme.ink, maxHeight: 166, overflow: 'hidden', ...EDITORIAL_WORD_WRAP}}>
                  {item}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <footer style={{position: 'absolute', left: insetX, right: insetX, top: 918, bottom: 36, overflow: 'hidden', display: 'flex', alignItems: 'center', gap: 12}}>
        <span style={{fontFamily: theme.mono, fontSize: 14, fontWeight: 800, letterSpacing: 2, color: theme.muted, marginRight: 4, flexShrink: 0}}>
          ENTITIES
        </span>
        <div style={{display: 'flex', alignItems: 'center', gap: 10, overflow: 'hidden'}}>
          <EntityPills entities={entities} />
        </div>
      </footer>
    </AbsoluteFill>
  );
};
