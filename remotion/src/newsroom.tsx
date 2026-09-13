import React from 'react';
import {AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {FontFaces, theme} from './theme';
import type {BriefingSlide, BriefingVideoProps, CoverCopy} from './video';
import {copyText, factPages, headlineFontSize, pageAtFrame, uniqueStories} from './newsroom-layout';

export const ink = {paper: '#F4F0E8', black: '#182D30', red: '#D84B31', muted: '#66736F', line: '#C9CCC0', green: '#236558', white: '#FFFDFA'};
const mono = '"Consolas", "Courier New", monospace';
const full: React.CSSProperties = {position: 'absolute', inset: 0};

const Signal: React.FC<{size?: number; color?: string}> = ({size = 40, color = ink.red}) => (
  <svg width={size} height={size} viewBox="0 0 64 64" fill="none">
    <path d="M32 3v58M3 32h58M11.5 11.5l41 41M11.5 52.5l41-41" stroke={color} strokeWidth="7" />
  </svg>
);

const Masthead: React.FC<{date?: string; dark?: boolean}> = ({date, dark = false}) => (
  <div style={{position: 'absolute', left: 88, right: 88, top: 49, height: 76, display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', borderBottom: `1px solid ${dark ? '#72817B' : ink.line}`}}>
    <div style={{display: 'flex', alignItems: 'center', gap: 18}}><Signal /><span style={{fontSize: 35, fontWeight: 900, letterSpacing: -1}}>AI 这一天</span><span style={{fontSize: 16, fontFamily: mono, letterSpacing: 2, marginLeft: 15, opacity: .7}}>THE DAILY SIGNAL</span></div>
    <div style={{fontSize: 23, fontFamily: mono, paddingTop: 8, opacity: .8}}>{date || 'AI NEWS'}</div>
  </div>
);

const Appear: React.FC<{frame: number; delay?: number; children: React.ReactNode; style?: React.CSSProperties}> = ({frame, delay = 0, children, style}) => {
  const progress = interpolate(frame, [delay, delay + 16], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return <div style={{...style, opacity: progress, transform: `translateY(${(1 - progress) * 20}px)`}}>{children}</div>;
};

const EditionOpening: React.FC<{slide: BriefingSlide; frame: number; duration: number}> = ({slide, frame, duration}) => {
  const stories = (slide.timelineItems || []).filter(item => item.kind === 'news');
  const cards = slide.page.cards || [];
  const agenda = stories.length ? stories.map(item => ({title: item.entity || item.label, body: item.headline || item.label})) : cards;
  const pages = Array.from({length: Math.max(1, Math.ceil(agenda.length / 4))}, (_, index) => agenda.slice(index * 4, index * 4 + 4));
  const active = pageAtFrame(frame, duration, pages.length);
  return <>
    <div style={{position: 'absolute', left: 86, top: 186, width: 880}}>
      <Appear frame={frame}><div style={{fontSize: 24, color: ink.red, letterSpacing: 3, fontWeight: 700}}>人工智能 · 每日新闻</div></Appear>
      <Appear frame={frame} delay={3}><div style={{fontFamily: 'Arial, sans-serif', fontWeight: 900, fontSize: 282, lineHeight: .97, letterSpacing: -22, marginTop: 38}}>AI<span style={{color: ink.red}}>.</span></div></Appear>
      <Appear frame={frame} delay={7}><div style={{fontSize: 118, lineHeight: 1.1, fontWeight: 900, letterSpacing: -7, marginTop: 5}}>这一天</div></Appear>
      <Appear frame={frame} delay={10}><div style={{width: 700, borderTop: `3px solid ${ink.black}`, marginTop: 43, paddingTop: 23, fontSize: 30, lineHeight: 1.65, color: ink.muted}}>{slide.caption || '每天，把 AI 世界的新变化讲清楚。'}</div></Appear>
    </div>
    <div style={{position: 'absolute', top: 196, right: 88, bottom: 199, width: 732, borderLeft: `1px solid ${ink.line}`, paddingLeft: 57}}>
      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 26}}><span style={{fontSize: 21, letterSpacing: 3, color: ink.muted}}>本期看点</span><span style={{fontFamily: mono, color: ink.red, fontSize: 25}}>{String(agenda.length).padStart(2, '0')} / STORIES</span></div>
      {pages[active].map((item, index) => <Appear key={`${active}-${index}`} frame={frame} delay={12 + index * 3} style={{borderTop: `1px solid ${ink.line}`, padding: '26px 0', display: 'flex', gap: 28}}>
        <span style={{fontFamily: mono, color: ink.red, fontSize: 29, paddingTop: 4}}>{String(active * 4 + index + 1).padStart(2, '0')}</span>
        <div><div style={{fontSize: 36, lineHeight: 1.4, fontWeight: 800, overflowWrap: 'anywhere'}}>{copyText(item.title)}</div>{item.body !== item.title ? <div style={{fontSize: 25, lineHeight: 1.5, marginTop: 6, color: ink.muted}}>{copyText(item.body)}</div> : null}</div>
      </Appear>)}
    </div>
    <div style={{position: 'absolute', left: 90, bottom: 153, fontFamily: mono, fontSize: 17, color: ink.muted, letterSpacing: 3}}>MODELS / PRODUCTS / RESEARCH / PEOPLE</div>
  </>;
};

const StoryPage: React.FC<{slide: BriefingSlide; frame: number; duration: number}> = ({slide, frame, duration}) => {
  const {fps} = useVideoConfig();
  const pages = factPages(slide.page.cards || []);
  const usableFrames = Math.max(1, duration - Math.round((slide.closingDuration || 0) * fps));
  const active = pageAtFrame(frame, usableFrames, pages.length);
  const cards = pages[active];
  const localFrame = frame - active * usableFrames / pages.length;
  const isSolo = cards.length === 1;
  return <>
    <div style={{position: 'absolute', left: 88, top: 176, width: 222}}>
      <div style={{fontSize: 19, color: ink.red, letterSpacing: 3, fontWeight: 800}}>{slide.page.kind === 'ticker' ? '更多动态' : '新闻'}</div>
      <div style={{fontSize: 148, lineHeight: 1.2, fontFamily: mono, letterSpacing: -13, color: ink.red}}>{String(slide.storyPosition || slide.index || 1).padStart(2, '0')}</div>
      <div style={{width: 52, height: 3, background: ink.red, margin: '20px 0'}} />
      <div style={{fontSize: 25, fontWeight: 700, lineHeight: 1.5, overflowWrap: 'anywhere'}}>{copyText(slide.activeTab)}</div>
      <div style={{marginTop: 20, fontFamily: mono, fontSize: 18, color: ink.muted}}>{String(active + 1).padStart(2, '0')} / {String(pages.length).padStart(2, '0')}</div>
    </div>
    <div style={{position: 'absolute', left: 365, right: 88, top: 176}}>
      <Appear frame={frame}><div style={{fontSize: headlineFontSize(slide.title), lineHeight: 1.28, fontWeight: 900, letterSpacing: -2, overflowWrap: 'anywhere', maxHeight: 265}}>{slide.title}</div></Appear>
      <div style={{height: 5, background: ink.black, marginTop: 30, marginBottom: 20}} />
      <div key={active} style={{display: 'flex', flexDirection: 'column', gap: 0}}>
        {cards.map((card, index) => {
          const body = copyText(card.body || card.title);
          const title = card.body ? copyText(card.title) : '';
          const bodySize = isSolo ? (body.length > 230 ? 34 : body.length > 120 ? 44 : 57) : body.length > 125 ? 30 : cards.length === 3 ? 35 : 41;
          return <Appear key={index} frame={localFrame} delay={index * 4} style={{display: 'flex', alignItems: 'baseline', gap: 27, padding: isSolo ? '36px 0' : '24px 0', borderBottom: `1px solid ${ink.line}`}}>
            <span style={{fontFamily: mono, fontSize: 19, color: ink.red, flexShrink: 0}}>{String(index + 1).padStart(2, '0')}</span>
            <div style={{flex: 1, minWidth: 0}}>{title ? <div style={{fontSize: 22, color: ink.green, fontWeight: 800, marginBottom: 8}}>{title}</div> : null}<div style={{fontSize: bodySize, lineHeight: 1.55, fontWeight: isSolo ? 700 : 550, overflowWrap: 'anywhere'}}>{body}</div>{card.meta ? <div style={{fontSize: 21, color: ink.muted, marginTop: 9}}>{card.meta}</div> : null}</div>
          </Appear>;
        })}
        {cards.length === 0 && slide.page.lead ? <div style={{fontSize: 46, lineHeight: 1.6, paddingTop: 30}}>{slide.page.lead}</div> : null}
      </div>
    </div>
  </>;
};

const EvidencePage: React.FC<{slide: BriefingSlide; frame: number}> = ({slide, frame}) => {
  const visual = slide.page.evidenceVisual;
  const asset = copyText(visual?.asset);
  const source = copyText(visual?.source || slide.page.source || slide.activeTab);
  return <>
    <div style={{position: 'absolute', left: 88, top: 174, width: 465}}>
      <div style={{fontSize: 23, letterSpacing: 3, color: '#E79B7D'}}>原文现场</div>
      <div style={{fontSize: 51, fontWeight: 850, lineHeight: 1.42, marginTop: 29, overflowWrap: 'anywhere'}}>{slide.title}</div>
      <div style={{height: 1, background: '#72817B', margin: '32px 0 23px'}} />
      <div style={{fontSize: 23, color: '#C1CDC4', lineHeight: 1.6}}>{source}</div>
      <div style={{fontSize: 20, color: '#C1CDC4', lineHeight: 1.55, marginTop: 18, overflowWrap: 'anywhere'}}>{copyText(slide.page.source_url || visual?.url)}</div>
    </div>
    <Appear frame={frame} style={{position: 'absolute', left: 626, right: 88, top: 163, bottom: 182, background: ink.white, padding: 16, boxShadow: '0 16px 35px #00000022'}}>
      {asset ? <Img src={staticFile(asset)} style={{width: '100%', height: '100%', objectFit: 'contain'}} /> : <div style={{color: ink.black, fontSize: 35, lineHeight: 1.7, padding: 56}}>{slide.page.lead || slide.caption}</div>}
    </Appear>
  </>;
};

const ChapterTrack: React.FC<{slide: BriefingSlide; compositionFrame: number; fps: number; dark: boolean}> = ({slide, compositionFrame, fps, dark}) => {
  const items = slide.timelineItems || [];
  const total = slide.timelineTotal || Math.max(1, ...items.map(item => item.end), slide.end);
  const time = compositionFrame / fps;
  return <div style={{position: 'absolute', left: 88, right: 88, bottom: 32, height: 54, borderTop: `1px solid ${dark ? '#72817B' : ink.line}`, paddingTop: 18, display: 'flex', gap: 10, alignItems: 'center'}}>
    <span style={{fontSize: 17, fontFamily: mono, color: dark ? '#C1CDC4' : ink.muted, whiteSpace: 'nowrap', width: 82}}>AI DAILY</span>
    <div style={{flex: 1, display: 'flex', gap: 6}}>{(items.length ? items : [{start: 0, end: total, duration: total}]).map((item, index) => <div key={index} style={{flex: Math.max(.1, item.end - item.start), height: 4, background: dark ? '#596E68' : '#CFD2C7', overflow: 'hidden'}}><div style={{height: '100%', width: `${interpolate(time, [item.start, Math.max(item.start + .01, item.end)], [0, 100], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})}%`, background: ink.red}} /></div>)}</div>
    <span style={{fontFamily: mono, fontSize: 18, color: dark ? '#C1CDC4' : ink.muted, paddingLeft: 15}}>{String(Math.floor(time / 60)).padStart(2, '0')}:{String(Math.floor(time % 60)).padStart(2, '0')}</span>
  </div>;
};

export const NewsroomScene: React.FC<{slide: BriefingSlide; duration: number; compositionFrame: number}> = ({slide, duration, compositionFrame}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const evidence = slide.page.kind === 'evidence';
  const closing = Boolean(slide.closingDuration) && frame >= duration - (slide.closingDuration || 0) * fps;
  return <AbsoluteFill style={{background: evidence ? ink.black : ink.paper, overflow: 'hidden'}}>
    <FontFaces />
    <div style={{...full, width: 1920, height: 1080, left: '50%', top: '50%', transform: `translate(-50%,-50%) scale(${Math.min(width / 1920, height / 1080)})`, fontFamily: theme.sans, color: evidence ? ink.paper : ink.black}}>
      <div style={{...full, backgroundImage: `radial-gradient(${evidence ? '#B9C4BA12' : '#182D300B'} .7px, transparent .7px)`, backgroundSize: '6px 6px', pointerEvents: 'none'}} />
      <Masthead date={slide.runLabel} dark={evidence} />
      {slide.kind === 'intro' ? <EditionOpening slide={slide} frame={frame} duration={duration} /> : evidence ? <EvidencePage slide={slide} frame={frame} /> : <StoryPage slide={slide} frame={frame} duration={duration} />}
      {closing ? <div style={{position: 'absolute', left: 88, bottom: 187, background: ink.red, color: ink.white, padding: '14px 25px', fontSize: 25, fontWeight: 700}}>AI 这一天 · 明天见</div> : null}
      <ChapterTrack slide={slide} compositionFrame={compositionFrame} fps={fps} dark={evidence} />
    </div>
  </AbsoluteFill>;
};

export const NewsroomCover: React.FC<BriefingVideoProps & {fourByThree?: boolean}> = ({fourByThree = false, ...props}) => {
  const news = uniqueStories(props.slides || []);
  const copy: CoverCopy = {headline: news[0]?.title || 'AI 这一天', date: props.slides[0]?.runLabel, storyCount: news.length, ...props.cover};
  const title = copyText(copy.headline);
  const width = fourByThree ? 1440 : 1920;
  const titleSize = title.length > 42 ? 78 : title.length > 28 ? 95 : title.length > 18 ? 117 : 150;
  return <AbsoluteFill style={{background: ink.paper, color: ink.black, fontFamily: theme.sans, overflow: 'hidden'}}>
    <FontFaces />
    <div style={{position: 'absolute', width, height: 1080}}>
      <Masthead date={copy.date} />
      <div style={{position: 'absolute', left: 85, top: 191, color: ink.red, fontSize: 30, fontWeight: 800, letterSpacing: 4}}>今天，AI 又变了什么？</div>
      <div style={{position: 'absolute', left: 76, top: 265, width: width - 170, fontWeight: 900, fontSize: titleSize, lineHeight: 1.23, letterSpacing: -3, overflowWrap: 'anywhere'}}>{title}</div>
      <div style={{position: 'absolute', left: 88, right: 88, bottom: 168, borderTop: `4px solid ${ink.black}`, paddingTop: 25, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 32}}>
        <div style={{fontSize: 30, lineHeight: 1.5, flex: 1}}>{copy.subheadline || '模型 · 产品 · 研究 · 行业'}</div>
        <div style={{fontSize: 21, color: ink.red, fontFamily: mono, flexShrink: 0}}>{String(copy.storyCount || news.length).padStart(2, '0')} STORIES</div>
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 100, background: ink.red, color: ink.white, display: 'flex', alignItems: 'center', padding: '0 88px', justifyContent: 'space-between'}}><span style={{fontSize: 33, fontWeight: 900}}>AI 这一天</span><span style={{fontFamily: mono, fontSize: 21, letterSpacing: 4}}>THE DAILY SIGNAL</span><Signal color={ink.white} /></div>
    </div>
  </AbsoluteFill>;
};
