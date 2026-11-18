import React from 'react';
import {Img, interpolate, staticFile, useVideoConfig} from 'remotion';
import {EDITORIAL_WORD_WRAP, SAFE_TOP, SAFE_X, SUBTITLE_SAFE_BOTTOM, theme} from './theme';
import {
  DEFAULT_CLOSING_TEXT,
  adaptiveHeadlineSize,
  charCount,
  cleanText,
  copyText,
  factPages,
  pageAtFrame,
} from './layout';
import type {BriefingCard, BriefingSlide} from './types';

const SETTLE_FRAMES = 18;

const SAFE_BOUNDS: React.CSSProperties = {
  position: 'absolute',
  left: SAFE_X,
  right: SAFE_X,
  top: SAFE_TOP,
  bottom: SUBTITLE_SAFE_BOTTOM,
  overflow: 'hidden',
};

export const EditorialReveal: React.FC<{
  frame: number;
  delay?: number;
  offsetY?: number;
  style?: React.CSSProperties;
  children: React.ReactNode;
}> = ({frame, delay = 0, offsetY = 16, style, children}) => {
  const local = Math.max(0, frame - delay);
  if (local >= SETTLE_FRAMES) return <div style={style}>{children}</div>;
  const raw = interpolate(local, [0, SETTLE_FRAMES], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const eased = 1 - Math.pow(1 - raw, 3);
  return (
    <div style={{...style, opacity: eased, transform: `translateY(${(1 - eased) * offsetY}px)`}}>
      {children}
    </div>
  );
};

const ClosingSignOffCard: React.FC<{frame: number; text: string}> = ({frame, text}) => (
  <EditorialReveal
    frame={frame}
    style={{flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', justifyContent: 'center'}}
  >
    <div
      style={{
        background: theme.card,
        border: `1.5px solid ${theme.ruleStrong}`,
        borderLeft: `8px solid ${theme.vermilion}`,
        borderRadius: '0 8px 8px 0',
        padding: '34px 40px',
        boxShadow: '0 12px 32px rgba(22, 24, 26, 0.06)',
      }}
    >
      <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16}}>
        <span style={{fontFamily: theme.mono, fontSize: 15, fontWeight: 800, letterSpacing: 2.4, color: theme.vermilion}}>
          EDITION COMPLETE · 今日播报完毕
        </span>
        <span style={{fontFamily: theme.mono, fontSize: 14, fontWeight: 700, color: theme.muted}}>
          AI 日报 · DAILY BRIEFING
        </span>
      </div>
      <div style={{fontSize: 32, lineHeight: 1.52, fontWeight: 800, color: theme.ink, ...EDITORIAL_WORD_WRAP}}>
        {text}
      </div>
    </div>
  </EditorialReveal>
);

const extractDomain = (url?: string): string => {
  const raw = copyText(url);
  if (!raw) return '';
  try {
    return new URL(raw).hostname.replace(/^www\./, '');
  } catch {
    return raw.replace(/^https?:\/\/(www\.)?/, '').split('/')[0] || '';
  }
};

const extractPublishedAt = (slide: BriefingSlide): string => {
  const direct = cleanText(
    slide.publishedAt ||
      slide.published_at ||
      slide.page?.publishedAt ||
      slide.page?.published_at ||
      slide.page?.evidenceVisual?.publishedAt ||
      slide.page?.evidenceVisual?.published_at,
  );
  if (direct) return direct;
  const provenance = (slide.page?.cards || []).find((c) => copyText(c.title) === '原文与时间');
  if (provenance?.body) {
    const parts = provenance.body.split(/[｜|·]/).map(cleanText).filter(Boolean);
    return parts.find((p) => /\d+月\d+日|\d{1,2}:\d{2}|发布|发现/.test(p)) || '';
  }
  return '';
};

const formatTierBadge = (slide: BriefingSlide): {label: string; color: string; bg: string} => {
  const tier = cleanText(slide.editorialTier || slide.editorial_tier).toLowerCase();
  if (tier === 'lead' || tier === 'top' || slide.storyPosition === 1) {
    return {label: '头版头条 · LEAD', color: theme.vermilion, bg: theme.vermilionSoft};
  }
  if (tier === 'brief' || slide.page?.kind === 'brief') {
    return {label: '简讯核验 · BRIEF', color: theme.inkSecondary, bg: theme.tint};
  }
  if (tier === 'deep' || tier === 'analysis') {
    return {label: '深度追踪 · DEEP', color: theme.emerald, bg: theme.emeraldSoft};
  }
  return {label: '重点报道 · STORY', color: theme.ink, bg: theme.tint};
};

const cardAccentColor = (card: BriefingCard, index: number): string => {
  const combined = `${copyText(card.title)} ${copyText(card.body)}`;
  if (/来源说明|待确认|传闻|风向|风险|边界/.test(combined)) return theme.amber;
  if (/原文与时间|信源|发布/.test(combined)) return theme.emerald;
  return index === 0 ? theme.vermilion : index === 1 ? theme.emerald : theme.ink;
};

export type SceneProps = {slide: BriefingSlide; frame: number; duration: number};

export const OpeningScene: React.FC<SceneProps> = ({slide, frame, duration}) => {
  const timelineStories = (slide.timelineItems || []).filter((item) => item.kind === 'news');
  const rawCards = slide.page?.cards || [];
  const agenda: BriefingCard[] = timelineStories.length
    ? timelineStories.map((i) => ({title: cleanText(i.entity || i.label), body: cleanText(i.headline || i.label)}))
    : rawCards.length
      ? rawCards
      : [{title: '今日看点', body: '模型、产品、研究与产业一手动态正在整理。'}];

  const itemsPerPage = 4;
  const pageCount = Math.max(1, Math.ceil(agenda.length / itemsPerPage));
  const agendaPages = Array.from({length: pageCount}, (_, idx) =>
    agenda.slice(idx * itemsPerPage, (idx + 1) * itemsPerPage),
  );
  const activePage = pageAtFrame(frame, duration, agendaPages.length);
  const pageLocalFrame = Math.max(0, frame - activePage * Math.max(1, duration / agendaPages.length));

  const rawCaption = cleanText(slide.caption);
  const rawHook = cleanText(slide.hook);
  const hookCopy =
    rawCaption && rawCaption !== 'AI 日报'
      ? rawCaption
      : rawHook && rawHook !== 'AI 日报'
        ? rawHook
        : '每天几分钟，用一手证据讲清 AI 世界的新变化。';

  const storyCount = timelineStories.length || rawCards.length;

  return (
    <section style={{...SAFE_BOUNDS, display: 'grid', gridTemplateColumns: '710px minmax(0, 1fr)', gap: 56}}>
      <div style={{display: 'flex', flexDirection: 'column', justifyContent: 'space-between', height: '100%', overflow: 'hidden', paddingRight: 8}}>
        <div>
          <EditorialReveal frame={frame} delay={0}>
            <div style={{fontFamily: theme.mono, fontSize: 14, fontWeight: 700, letterSpacing: 2.4, color: theme.vermilion}}>
              MODELS · PRODUCTS · RESEARCH · INFRA
            </div>
          </EditorialReveal>

          <EditorialReveal frame={frame} delay={3} style={{marginTop: 22}}>
            <div style={{display: 'flex', alignItems: 'baseline', gap: 10}}>
              <span style={{fontFamily: theme.serif, fontSize: 136, lineHeight: 0.96, fontWeight: 900, letterSpacing: -5, color: theme.ink}}>
                AI 日报
              </span>
              <span style={{width: 18, height: 18, background: theme.vermilion, display: 'inline-block', flexShrink: 0}} />
            </div>
            <div style={{marginTop: 14, fontFamily: theme.mono, fontSize: 18, fontWeight: 700, letterSpacing: 4, color: theme.inkSecondary}}>
              THE VERIFIED AI DAILY BRIEFING
            </div>
          </EditorialReveal>

          <div style={{height: 3, width: '100%', background: theme.ruleStrong, margin: '32px 0 26px'}} />

          <EditorialReveal frame={frame} delay={6}>
            <p style={{margin: 0, fontSize: 29, lineHeight: 1.58, fontWeight: 600, color: theme.ink, ...EDITORIAL_WORD_WRAP}}>
              {hookCopy}
            </p>
          </EditorialReveal>
        </div>

        <EditorialReveal frame={frame} delay={9}>
          <div style={{padding: '18px 22px', background: theme.tint, borderLeft: `4px solid ${theme.vermilion}`, borderRadius: '0 6px 6px 0'}}>
            <div style={{fontFamily: theme.mono, fontSize: 13, fontWeight: 800, letterSpacing: 2, color: theme.vermilion, marginBottom: 6}}>
              EDITION FOCUS · 本期关注
            </div>
            <div style={{fontSize: 21, fontWeight: 700, color: theme.inkSecondary, lineHeight: 1.4, ...EDITORIAL_WORD_WRAP}}>
              {cleanText(slide.page?.lead) || '官方一手发布 · 关键基准与价格变化 · 原始页面核验'}
            </div>
          </div>
        </EditorialReveal>
      </div>

      <div style={{borderLeft: `1px solid ${theme.hairline}`, paddingLeft: 48, height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column'}}>
        <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: 16, borderBottom: `2px solid ${theme.ruleStrong}`}}>
          <span style={{fontSize: 18, fontWeight: 800, letterSpacing: 2, color: theme.ink}}>今日目录 · LEAD AGENDA</span>
          <div style={{display: 'flex', alignItems: 'center', gap: 14, fontFamily: theme.mono, fontSize: 16, fontWeight: 700, color: theme.vermilion}}>
            <span>{String(storyCount).padStart(2, '0')} STORIES</span>
            {agendaPages.length > 1 ? (
              <span style={{color: theme.muted}}>
                PAGE {String(activePage + 1).padStart(2, '0')}/{String(agendaPages.length).padStart(2, '0')}
              </span>
            ) : null}
          </div>
        </div>

        <div key={`agenda-${activePage}`} style={{flex: 1, display: 'flex', flexDirection: 'column', marginTop: 8, gap: 12, overflow: 'hidden'}}>
          {agendaPages[activePage].map((item, idx) => {
            const globalNum = activePage * itemsPerPage + idx + 1;
            const itemTitle = cleanText(item.title);
            const itemBody = cleanText(item.body);
            const showBody = Boolean(itemBody && itemBody !== itemTitle);
            return (
              <EditorialReveal
                key={`${activePage}-${idx}`}
                frame={pageLocalFrame}
                delay={idx * 3}
                style={{
                  background: theme.card,
                  border: `1px solid ${theme.hairline}`,
                  borderLeft: `4px solid ${idx === 0 && activePage === 0 ? theme.vermilion : theme.ink}`,
                  borderRadius: '0 6px 6px 0',
                  padding: '18px 22px',
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 20,
                }}
              >
                <span style={{fontFamily: theme.mono, fontSize: 22, fontWeight: 800, color: theme.vermilion, lineHeight: 1.3, fontVariantNumeric: 'tabular-nums', flexShrink: 0}}>
                  {String(globalNum).padStart(2, '0')}
                </span>
                <div style={{minWidth: 0, flex: 1}}>
                  <div style={{fontSize: showBody ? 23 : 26, fontWeight: 800, lineHeight: 1.32, color: theme.ink, ...EDITORIAL_WORD_WRAP}}>
                    {itemTitle || itemBody}
                  </div>
                  {showBody ? (
                    <div style={{marginTop: 6, fontSize: 21, lineHeight: 1.42, fontWeight: 500, color: theme.inkSecondary, ...EDITORIAL_WORD_WRAP}}>
                      {itemBody}
                    </div>
                  ) : null}
                </div>
              </EditorialReveal>
            );
          })}
        </div>
      </div>
    </section>
  );
};

export const StoryScene: React.FC<SceneProps> = ({slide, frame, duration}) => {
  const {fps} = useVideoConfig();
  const safeFps = fps || 30;
  const closingFrames = slide.closingDuration ? Math.max(1, Math.round(slide.closingDuration * safeFps)) : 0;
  const usableFrames = Math.max(1, duration - closingFrames);
  const isClosing = closingFrames > 0 && frame >= usableFrames;

  const rawCards = slide.page?.cards?.length
    ? slide.page.cards
    : [{title: '核心变化', body: cleanText(slide.page?.lead || slide.caption || slide.title)}];

  const pages = factPages(rawCards);
  const activePage = pageAtFrame(frame, usableFrames, pages.length);
  const cards = pages[activePage];
  const pageLocalFrame = Math.max(0, frame - activePage * Math.max(1, usableFrames / pages.length));

  const storyNumber = String(Math.max(1, slide.storyPosition || 1)).padStart(2, '0');
  const tier = formatTierBadge(slide);
  const sourceName = cleanText(slide.page?.source || slide.page?.evidenceVisual?.source || slide.activeTab || '官方信源');
  const sourceDomain = extractDomain(slide.page?.source_url || slide.page?.evidenceVisual?.url);
  const publishedAt = extractPublishedAt(slide);

  const signalKind = cleanText(slide.signalKind || slide.signal_kind);
  const disclaimerText =
    cleanText(slide.disclaimer) ||
    (signalKind === 'community_rumour'
      ? '传闻/风向，未获官方公告确认。'
      : signalKind === 'official_personnel'
        ? '一线消息，未获官方公告确认。'
        : '');

  const titleText = cleanText(slide.title);
  const titleSize = adaptiveHeadlineSize(titleText, {story: true});
  const leadText = cleanText(slide.page?.lead);
  const pageCharSum = cards.reduce((s, c) => s + charCount(c.title) + charCount(c.body), 0);
  const showLead =
    !isClosing &&
    Boolean(leadText) &&
    leadText !== titleText &&
    leadText !== cleanText(cards[0]?.body) &&
    (cards.length <= 2 || (cards.length === 3 && pageCharSum <= 135 && charCount(titleText) <= 32));

  return (
    <section style={{...SAFE_BOUNDS, display: 'grid', gridTemplateColumns: '256px minmax(0, 1fr)', gap: 44}}>
      <aside style={{height: '100%', overflow: 'hidden', borderRight: `1px solid ${theme.hairline}`, paddingRight: 32, display: 'flex', flexDirection: 'column', justifyContent: 'space-between'}}>
        <div>
          <div style={{display: 'inline-block', padding: '5px 11px', borderRadius: 4, background: tier.bg, color: tier.color, fontSize: 13, fontWeight: 800, letterSpacing: 1.2}}>
            {tier.label}
          </div>
          <div style={{marginTop: 14, fontFamily: theme.serif, fontSize: 108, lineHeight: 0.94, fontWeight: 900, letterSpacing: -4, color: theme.vermilion, fontVariantNumeric: 'tabular-nums'}}>
            {storyNumber}
          </div>
          <div style={{width: 48, height: 3, background: theme.ruleStrong, margin: '20px 0'}} />

          <div style={{background: theme.card, border: `1px solid ${theme.hairline}`, borderRadius: 6, padding: '14px 16px'}}>
            <div style={{fontFamily: theme.mono, fontSize: 12, fontWeight: 800, letterSpacing: 1.8, color: theme.emerald, marginBottom: 6}}>
              SOURCE · 信源出处
            </div>
            <div style={{fontSize: 19, fontWeight: 800, color: theme.ink, lineHeight: 1.35, ...EDITORIAL_WORD_WRAP}}>
              {sourceName}
            </div>
            {publishedAt ? (
              <div style={{marginTop: 6, fontSize: 15, fontWeight: 600, color: theme.inkSecondary, lineHeight: 1.35}}>
                {publishedAt}
              </div>
            ) : null}
            {sourceDomain ? (
              <div style={{marginTop: 4, fontFamily: theme.mono, fontSize: 13, color: theme.muted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'}}>
                {sourceDomain}
              </div>
            ) : null}
          </div>

          {disclaimerText ? (
            <div
              style={{
                marginTop: 14,
                background: theme.amberSoft,
                border: `1.5px solid ${theme.amberBorder}`,
                borderLeft: `4px solid ${signalKind === 'community_rumour' ? theme.vermilion : theme.amber}`,
                borderRadius: '0 6px 6px 0',
                padding: '12px 14px',
              }}
            >
              <div style={{fontSize: 13, fontWeight: 900, letterSpacing: 1.2, color: signalKind === 'community_rumour' ? theme.vermilion : theme.amber, marginBottom: 4}}>
                {signalKind === 'community_rumour' ? '传闻/风向提示' : '信源核实提示'}
              </div>
              <div style={{fontSize: 15, fontWeight: 700, lineHeight: 1.4, color: theme.ink, ...EDITORIAL_WORD_WRAP}}>
                {disclaimerText}
              </div>
            </div>
          ) : null}
        </div>

        <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingTop: 12, borderTop: `1px solid ${theme.hairline}`, fontFamily: theme.mono, fontSize: 14, fontWeight: 700, color: theme.muted}}>
          <span>PAGE</span>
          <span style={{color: theme.ink}}>
            {String(activePage + 1).padStart(2, '0')} / {String(pages.length).padStart(2, '0')}
          </span>
        </div>
      </aside>

      <div style={{minWidth: 0, height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column'}}>
        <EditorialReveal frame={frame} delay={0}>
          <div style={{marginBottom: 10, fontFamily: theme.mono, fontSize: 14, fontWeight: 800, letterSpacing: 2, color: theme.vermilion}}>
            {slide.page?.title && cleanText(slide.page.title) !== titleText ? cleanText(slide.page.title) : '核心事实 · VERIFIED DISPATCH'}
          </div>
          <h1
            style={{
              margin: 0,
              fontSize: titleSize,
              lineHeight: 1.22,
              fontWeight: 900,
              letterSpacing: -1.2,
              color: theme.ink,
              maxHeight: Math.round(titleSize * 1.22 * 3 + 6),
              overflow: 'hidden',
              ...EDITORIAL_WORD_WRAP,
            }}
          >
            {titleText}
          </h1>
        </EditorialReveal>

        <div style={{height: 3, background: theme.ruleStrong, margin: '16px 0 14px', flexShrink: 0}} />

        {showLead ? (
          <div
            style={{
              padding: '11px 18px',
              marginBottom: 14,
              background: theme.tint,
              borderLeft: `4px solid ${theme.emerald}`,
              borderRadius: '0 6px 6px 0',
              fontSize: 22,
              lineHeight: 1.42,
              fontWeight: 650,
              color: theme.inkSecondary,
              maxHeight: 78,
              overflow: 'hidden',
              flexShrink: 0,
              ...EDITORIAL_WORD_WRAP,
            }}
          >
            {leadText}
          </div>
        ) : null}

        {isClosing ? (
          <ClosingSignOffCard
            frame={Math.max(0, frame - usableFrames)}
            text={cleanText(slide.closingText) || DEFAULT_CLOSING_TEXT}
          />
        ) : (
          <div
            key={`cards-${activePage}`}
            style={{flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column', gap: cards.length === 3 ? 12 : 16}}
          >
            {cards.map((card, idx) => {
              const cardTitle = cleanText(card.title);
              const cardBody = cleanText(card.body || card.title);
              const showCardTitle = Boolean(card.body && cardTitle);
              const accentColor = cardAccentColor(card, idx);
              const bodyLen = charCount(cardBody);
              const bodySize =
                cards.length === 1
                  ? bodyLen > 130 ? 28 : bodyLen > 75 ? 32 : 36
                  : cards.length === 2
                    ? bodyLen > 95 ? 25 : 28
                    : bodyLen > 75 ? 23 : 25;
              const padY = cards.length === 1 ? 26 : cards.length === 2 ? 18 : 14;

              return (
                <EditorialReveal
                  key={`${activePage}-${idx}`}
                  frame={pageLocalFrame}
                  delay={idx * 3}
                  style={{
                    background: theme.card,
                    border: `1px solid ${theme.hairline}`,
                    borderLeft: `4px solid ${accentColor}`,
                    borderRadius: '0 6px 6px 0',
                    padding: `${padY}px 24px`,
                    overflow: 'hidden',
                  }}
                >
                  <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: showCardTitle ? 6 : 4, gap: 12}}>
                    <div style={{display: 'flex', alignItems: 'center', gap: 10}}>
                      <span style={{fontFamily: theme.mono, fontSize: 15, fontWeight: 800, color: accentColor, fontVariantNumeric: 'tabular-nums'}}>
                        {String(activePage * 3 + idx + 1).padStart(2, '0')}
                      </span>
                      {showCardTitle ? (
                        <span style={{fontSize: 18, fontWeight: 800, color: accentColor, letterSpacing: 0.4}}>{cardTitle}</span>
                      ) : null}
                    </div>
                    {card.meta ? <span style={{fontSize: 15, fontWeight: 600, color: theme.muted}}>{cleanText(card.meta)}</span> : null}
                  </div>
                  <div style={{fontSize: bodySize, lineHeight: cards.length === 3 ? 1.4 : 1.46, fontWeight: cards.length === 1 ? 700 : 600, color: theme.ink, ...EDITORIAL_WORD_WRAP}}>
                    {cardBody}
                  </div>
                </EditorialReveal>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
};

export const EvidenceScene: React.FC<SceneProps> = ({slide, frame}) => {
  const visual = slide.page?.evidenceVisual;
  const asset = copyText(visual?.asset);
  const sourceName = cleanText(visual?.source || slide.page?.source || slide.activeTab || '一手官方页面');
  const rawUrl = copyText(visual?.url || slide.page?.source_url);
  const domain = extractDomain(rawUrl);
  const publishedAt = extractPublishedAt(slide);
  const titleText = cleanText(slide.title);
  const leadText = cleanText(visual?.excerpt || slide.page?.lead || slide.caption);
  const excerptCards = (slide.page?.cards || slide.page?.background_cards || []).slice(0, 3);
  const sideSize = charCount(titleText) > 34 ? 34 : charCount(titleText) > 22 ? 40 : 46;

  return (
    <section style={{...SAFE_BOUNDS, display: 'grid', gridTemplateColumns: '430px minmax(0, 1fr)', gap: 36}}>
      <aside style={{background: theme.slateSurface, border: `1px solid ${theme.slateHairline}`, borderRadius: 8, padding: '26px 28px', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', overflow: 'hidden'}}>
        <div style={{overflow: 'hidden'}}>
          <div style={{display: 'inline-block', padding: '5px 12px', borderRadius: 4, background: 'rgba(27, 107, 90, 0.28)', border: '1px solid rgba(124, 224, 198, 0.36)', color: '#7CE0C6', fontSize: 14, fontWeight: 800, letterSpacing: 1.4}}>
            原始证据核验 · SOURCE INSPECTION
          </div>
          <h2 style={{margin: '20px 0 0', fontSize: sideSize, lineHeight: 1.28, fontWeight: 850, color: theme.slateInk, maxHeight: Math.round(sideSize * 1.28 * 4 + 6), overflow: 'hidden', ...EDITORIAL_WORD_WRAP}}>
            {titleText}
          </h2>
          {leadText && leadText !== titleText ? (
            <p style={{margin: '18px 0 0', fontSize: 20, lineHeight: 1.52, color: theme.slateSecondary, ...EDITORIAL_WORD_WRAP}}>
              {leadText}
            </p>
          ) : null}
        </div>

        <div style={{borderTop: `1px solid ${theme.slateHairline}`, paddingTop: 16}}>
          <div style={{fontFamily: theme.mono, fontSize: 12, fontWeight: 700, letterSpacing: 1.8, color: theme.slateMuted, marginBottom: 6}}>
            PROVENANCE · 来源信息
          </div>
          <div style={{fontSize: 19, fontWeight: 800, color: theme.slateInk}}>{sourceName}</div>
          {publishedAt ? <div style={{marginTop: 4, fontSize: 15, color: theme.slateSecondary}}>{publishedAt}</div> : null}
          {domain || rawUrl ? (
            <div style={{marginTop: 6, fontFamily: theme.mono, fontSize: 13, color: theme.slateMuted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'}}>
              {domain || rawUrl}
            </div>
          ) : null}
        </div>
      </aside>

      <EditorialReveal
        frame={frame}
        delay={2}
        style={{
          height: '100%',
          minWidth: 0,
          background: theme.slateSurface,
          border: `1px solid ${theme.slateHairline}`,
          borderRadius: 8,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 20px 44px rgba(12, 16, 23, 0.28)',
        }}
      >
        <div style={{height: 48, background: theme.slateElevated, borderBottom: `1px solid ${theme.slateHairline}`, padding: '0 18px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexShrink: 0}}>
          <div style={{display: 'flex', alignItems: 'center', gap: 10, minWidth: 0}}>
            <span style={{width: 9, height: 9, borderRadius: '50%', background: '#E06C5A'}} />
            <span style={{width: 9, height: 9, borderRadius: '50%', background: '#E2B354'}} />
            <span style={{width: 9, height: 9, borderRadius: '50%', background: '#57B894'}} />
            <span style={{marginLeft: 8, fontSize: 15, fontWeight: 700, color: theme.slateInk, whiteSpace: 'nowrap'}}>
              {sourceName}
            </span>
            {domain || rawUrl ? (
              <span style={{fontFamily: theme.mono, fontSize: 13, color: theme.slateSecondary, padding: '2px 10px', borderRadius: 4, background: 'rgba(15, 19, 26, 0.35)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 460}}>
                {domain || rawUrl}
              </span>
            ) : null}
          </div>

          <div style={{display: 'flex', alignItems: 'center', gap: 14, flexShrink: 0}}>
            {publishedAt ? <span style={{fontFamily: theme.mono, fontSize: 13, color: theme.slateSecondary}}>{publishedAt}</span> : null}
            <span style={{padding: '3px 10px', borderRadius: 4, background: 'rgba(27, 107, 90, 0.32)', border: '1px solid rgba(124, 224, 198, 0.4)', color: '#7CE0C6', fontSize: 13, fontWeight: 800}}>
              原始证据核验
            </span>
          </div>
        </div>

        <div style={{flex: 1, minHeight: 0, padding: 16, background: asset ? '#F2F4F7' : theme.paper, overflow: 'hidden', display: 'flex', flexDirection: 'column', justifyContent: 'center'}}>
          {asset ? (
            <Img
              src={staticFile(asset)}
              style={{width: '100%', height: '100%', objectFit: 'contain', objectPosition: 'center', background: '#FFFFFF', borderRadius: 4}}
            />
          ) : (
            <div style={{background: theme.card, border: `1px solid ${theme.hairline}`, borderTop: `4px solid ${theme.emerald}`, borderRadius: 6, padding: '28px 34px', height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column', justifyContent: 'space-between'}}>
              <div>
                <div style={{fontFamily: theme.mono, fontSize: 13, fontWeight: 800, letterSpacing: 2, color: theme.emerald, marginBottom: 10}}>
                  VERIFIED SOURCE EXCERPT · 信源原文摘录
                </div>
                <div style={{fontSize: 28, fontWeight: 850, lineHeight: 1.38, color: theme.ink, marginBottom: 18, ...EDITORIAL_WORD_WRAP}}>
                  {cleanText(visual?.title || slide.page?.lead || slide.title)}
                </div>
                <div style={{display: 'flex', flexDirection: 'column', gap: 12}}>
                  {excerptCards.length > 0 ? (
                    excerptCards.map((card, idx) => (
                      <div key={idx} style={{padding: '12px 16px', background: theme.paper, borderLeft: `3px solid ${idx === 0 ? theme.vermilion : theme.emerald}`, fontSize: 21, lineHeight: 1.45, color: theme.ink, ...EDITORIAL_WORD_WRAP}}>
                        {card.title ? <strong style={{marginRight: 10}}>{cleanText(card.title)}：</strong> : null}
                        <span>{cleanText(card.body)}</span>
                      </div>
                    ))
                  ) : (
                    <div style={{fontSize: 24, lineHeight: 1.55, color: theme.inkSecondary, ...EDITORIAL_WORD_WRAP}}>{leadText}</div>
                  )}
                </div>
              </div>
              <div style={{paddingTop: 12, borderTop: `1px solid ${theme.hairline}`, display: 'flex', justifyContent: 'space-between', fontFamily: theme.mono, fontSize: 14, color: theme.muted}}>
                <span>{sourceName}</span>
                <span>{rawUrl || 'PRIMARY RECORD'}</span>
              </div>
            </div>
          )}
        </div>
      </EditorialReveal>
    </section>
  );
};

export const TickerScene: React.FC<SceneProps> = ({slide, frame, duration}) => {
  const {fps} = useVideoConfig();
  const safeFps = fps || 30;
  const closingFrames = slide.closingDuration ? Math.max(1, Math.round(slide.closingDuration * safeFps)) : 0;
  const usableFrames = Math.max(1, duration - closingFrames);
  const isClosing = closingFrames > 0 && frame >= usableFrames;

  const allCards = slide.page?.cards || [];
  const perPage = 6;
  const pageCount = Math.max(1, Math.ceil(allCards.length / perPage));
  const pages = Array.from({length: pageCount}, (_, idx) => allCards.slice(idx * perPage, (idx + 1) * perPage));
  const activePage = pageAtFrame(frame, usableFrames, pages.length);
  const pageLocalFrame = Math.max(0, frame - activePage * Math.max(1, usableFrames / pages.length));

  return (
    <section style={{...SAFE_BOUNDS, display: 'flex', flexDirection: 'column'}}>
      <div style={{display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', paddingBottom: 14, borderBottom: `3px solid ${theme.ruleStrong}`, flexShrink: 0}}>
        <div style={{display: 'flex', alignItems: 'baseline', gap: 18}}>
          <h1 style={{margin: 0, fontSize: 52, lineHeight: 1.1, fontWeight: 900, letterSpacing: -1, color: theme.ink}}>
            快讯速览
          </h1>
          <span style={{fontFamily: theme.mono, fontSize: 18, fontWeight: 800, letterSpacing: 2.8, color: theme.vermilion}}>
            MORE SIGNALS
          </span>
        </div>
        <div style={{display: 'flex', alignItems: 'center', gap: 16, fontFamily: theme.mono, fontSize: 16, fontWeight: 700, color: theme.inkSecondary}}>
          <span>{cleanText(slide.page?.lead) || '更多经核验官方更新'}</span>
          <span style={{color: theme.vermilion}}>{String(allCards.length).padStart(2, '0')} ITEMS</span>
        </div>
      </div>

      {isClosing ? (
        <ClosingSignOffCard
          frame={Math.max(0, frame - usableFrames)}
          text={cleanText(slide.closingText) || DEFAULT_CLOSING_TEXT}
        />
      ) : (
        <div
          key={`ticker-${activePage}`}
          style={{flex: 1, minHeight: 0, marginTop: 22, display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gridAutoRows: 'minmax(0, 1fr)', gap: '18px 32px', overflow: 'hidden'}}
        >
          {pages[activePage].map((card, idx) => {
            const entity = cleanText(card.title);
            const summary = cleanText(card.body);
            const accent = idx % 2 === 0 ? theme.vermilion : theme.emerald;
            return (
              <EditorialReveal
                key={`${activePage}-${idx}`}
                frame={pageLocalFrame}
                delay={idx * 2}
                style={{
                  background: theme.card,
                  border: `1px solid ${theme.hairline}`,
                  borderLeft: `4px solid ${accent}`,
                  borderRadius: '0 6px 6px 0',
                  padding: '18px 22px',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'center',
                  overflow: 'hidden',
                }}
              >
                <div style={{display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8}}>
                  <span style={{fontFamily: theme.mono, fontSize: 15, fontWeight: 800, color: accent, fontVariantNumeric: 'tabular-nums'}}>
                    {String(activePage * perPage + idx + 1).padStart(2, '0')}
                  </span>
                  {entity ? (
                    <span style={{padding: '2px 10px', borderRadius: 4, background: theme.tint, fontSize: 17, fontWeight: 800, color: theme.ink}}>
                      {entity}
                    </span>
                  ) : null}
                </div>
                <div style={{fontSize: 23, lineHeight: 1.42, fontWeight: 650, color: theme.ink, ...EDITORIAL_WORD_WRAP}}>
                  {summary || entity}
                </div>
              </EditorialReveal>
            );
          })}
        </div>
      )}
    </section>
  );
};
