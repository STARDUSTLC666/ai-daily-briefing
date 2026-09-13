import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import {TransitionSeries, linearTiming} from "@remotion/transitions";
import {wipe} from "@remotion/transitions/wipe";
import {FontFaces, theme as T} from "./theme";
import {NewsroomScene} from "./newsroom";
import {
  timelineItemIndexAtTime,
  timelineItemPixelGeometry,
  timelineTrackGeometry,
  timelineXAtTime,
} from "./timeline";

export type BriefingCard = {
  icon?: string;
  title?: string;
  body?: string;
  meta?: string;
  accent?: string;
};

export type EvidenceVisual = {
  source?: string;
  title?: string;
  url?: string;
  image?: string;
  asset?: string;
  status?: string;
  required?: boolean;
};

export type BriefingPage = {
  kind?: string;
  title?: string;
  lead?: string;
  source?: string;
  source_url?: string;
  evidenceVisual?: EvidenceVisual | null;
  cards?: BriefingCard[];
  background_cards?: BriefingCard[];
};

export type TimelineItem = {
  headline?: string;
  label: string;
  start: number;
  end: number;
  duration: number;
  kind: string;
  activeTab: string;
  icon?: string;
  entity?: string;
};

export type AssetSlot = {
  id: string;
  layer: string;
  purpose: string;
  optional: boolean;
  expected: string;
};

export type BriefingSlide = {
  index: number;
  storyPosition?: number;
  storyTotal?: number;
  runLabel?: string;
  start: number;
  end: number;
  duration: number;
  kind: string;
  activeTab: string;
  accent: string;
  title: string;
  caption: string;
  kicker?: string;
  bottomTabs: string[];
  bottomActive: string;
  timelineItems?: TimelineItem[];
  timelineTotal?: number;
  assetSlots?: AssetSlot[];
  page: BriefingPage;
  closingDuration?: number;
  closingText?: string;
};

export type BriefingVideoProps = {
  visualStyle?: 'newsroom' | 'classic';
  fps: number;
  width: number;
  height: number;
  slides: BriefingSlide[];
  cover?: CoverCopy;
};

export type CoverCopy = {
  eyebrow?: string;
  headline?: string;
  subheadline?: string;
  badge?: string;
  date?: string;
  storyCount?: number;
  highlights?: string[];
  entities?: string[];
};

const TRANSITION_FRAMES = 14;
const TIMELINE_HEIGHT = 82;
const TIMELINE_TRACK_INSET = 8;
const RAIL_WIDTH = 96;
const DESIGN_WIDTH = 1920;
const DESIGN_HEIGHT = 1080;

// Composition metadata must sum the SAME slide list the video renders
// (mergeOutroIntoFinalStory folds the outro into the final story before rounding);
// summing the unmerged list can differ by a frame and leave a contentless last frame.
export const totalFrames = (props: BriefingVideoProps) =>
  Math.max(
    24,
    mergeOutroIntoFinalStory(props.slides || []).reduce(
      (total, slide) =>
        total + Math.max(24, Math.round((slide.duration || 1) * (props.fps || 30))),
      0,
    ),
  );

const clean = (value: unknown) =>
  String(value || "")
    .replace(/…+/g, "")
    .replace(/\.{3,}/g, "")
    .replace(/\s+/g, " ")
    .trim();

const cut = (value: unknown, limit: number) => {
  const text = clean(value);
  if (text.length <= limit) return text;
  const window = text.slice(0, limit + 1);
  const boundaries = ["。", "！", "？", "；", "，", "、", " "]
    .map((mark) => window.lastIndexOf(mark))
    .filter((position) => position >= Math.max(8, Math.floor(limit * 0.62)));
  const boundary = boundaries.length ? Math.max(...boundaries) : limit;
  const includePunctuation = /[。！？；，、]/.test(text[boundary] || "");
  return text.slice(0, boundary + (includePunctuation ? 1 : 0)).trim();
};

const clamp = (value: number, min = 0, max = 1) =>
  Math.max(min, Math.min(max, value));

const alpha = (color: string, suffix: string) =>
  /^#[0-9a-f]{6}$/i.test(color) ? `${color}${suffix}` : color;

const accent = (slide: BriefingSlide) => {
  const raw = clean(slide.accent).replace(/^#/, "");
  return /^[0-9a-f]{6}$/i.test(raw) ? `#${raw}` : T.cyan;
};

const normalizedTimeline = (raw: TimelineItem[]) => raw.map((item) => ({...item}));

const MOTION_SETTLE_FRAMES = 30;

const storyCount = (slide: BriefingSlide) =>
  normalizedTimeline(slide.timelineItems || []).filter((item) => item.kind === "news").length;

const reveal = (frame: number, delay = 0, stiffness = 118) => {
  const localFrame = Math.max(0, frame - delay);
  if (localFrame >= MOTION_SETTLE_FRAMES) return 1;
  return clamp(
    spring({
      frame: localFrame,
      fps: 30,
      config: {damping: 22, stiffness, mass: 0.82},
    }),
  );
};

const MotionBlock: React.FC<{
  frame: number;
  delay?: number;
  fromX?: number;
  fromY?: number;
  fromScale?: number;
  animated?: boolean;
  style?: React.CSSProperties;
  children: React.ReactNode;
}> = ({
  frame,
  delay = 0,
  fromX = 0,
  fromY = 24,
  fromScale = 1,
  animated = true,
  style,
  children,
}) => {
  const localFrame = Math.max(0, frame - delay);
  const settled = !animated || localFrame >= MOTION_SETTLE_FRAMES;
  const progress = settled ? 1 : reveal(frame, delay);
  const motionStyle: React.CSSProperties = settled
    ? {}
    : {
        opacity: progress,
        transform: `translate3d(${(1 - progress) * fromX}px, ${(1 - progress) * fromY}px, 0) scale(${fromScale + (1 - fromScale) * progress})`,
        transformOrigin: "center center",
        willChange: "transform, opacity",
      };
  return (
    <div
      style={{
        ...style,
        ...motionStyle,
      }}
    >
      {children}
    </div>
  );
};

const EMPHASIS_SPLIT =
  /((?:GPT|Grok|GLM|Qwen|Llama|Gemini|Claude|Seedance|DGX|RTX)[ -]?[A-Za-z0-9.]+|OpenAI|Anthropic|NVIDIA|DeepSeek|Kimi|Codex|xAI|\d+(?:\.\d+)?(?:%|B|GB|TB|K|ms|秒|分钟|小时|美元|万|亿)?)/gi;
const EMPHASIS_TEST =
  /^(?:(?:GPT|Grok|GLM|Qwen|Llama|Gemini|Claude|Seedance|DGX|RTX)[ -]?[A-Za-z0-9.]+|OpenAI|Anthropic|NVIDIA|DeepSeek|Kimi|Codex|xAI|\d+(?:\.\d+)?(?:%|B|GB|TB|K|ms|秒|分钟|小时|美元|万|亿)?)$/i;

const RichText: React.FC<{
  text: string;
  color?: string;
  emphasisColor?: string;
}> = ({text, color = T.text, emphasisColor = T.cyan}) => (
  <span style={{color}}>
    {clean(text)
      .split(EMPHASIS_SPLIT)
      .filter(Boolean)
      .map((part, index) =>
        EMPHASIS_TEST.test(part) ? (
          <strong key={`${part}-${index}`} style={{color: emphasisColor, fontWeight: 900}}>
            {part}
          </strong>
        ) : (
          <React.Fragment key={`${part}-${index}`}>{part}</React.Fragment>
        ),
      )}
  </span>
);

const AccentRule: React.FC<{
  frame: number;
  delay?: number;
  color?: string;
  width?: number | string;
  height?: number;
  style?: React.CSSProperties;
}> = ({frame, delay = 0, color = T.cyan, width = 120, height = 4, style}) => {
  const progress = reveal(frame, delay);
  return (
    <div
      style={{
        width,
        height,
        background: color,
        borderRadius: 999,
        opacity: progress,
        transform: `scaleX(${progress})`,
        transformOrigin: "left center",
        boxShadow: `0 0 24px ${alpha(color, "44")}`,
        willChange: "transform, opacity",
        ...style,
      }}
    />
  );
};

const SignalDots: React.FC<{frame: number; color: string}> = ({frame, color}) => {
  const points = [
    [8, 18],
    [18, 73],
    [33, 42],
    [49, 84],
    [64, 22],
    [78, 61],
    [91, 34],
  ];
  return (
    <>
      {points.map(([left, top], index) => {
        const pulse = 0.24 + 0.5 * (0.5 + 0.5 * Math.sin((frame + index * 13) / 16));
        return (
          <div
            key={`${left}-${top}`}
            style={{
              position: "absolute",
              left: `${left}%`,
              top: `${top}%`,
              width: index % 3 === 0 ? 7 : 4,
              height: index % 3 === 0 ? 7 : 4,
              borderRadius: 99,
              background: color,
              opacity: pulse,
              boxShadow: `0 0 ${10 + pulse * 18}px ${alpha(color, "88")}`,
            }}
          />
        );
      })}
    </>
  );
};

const Backdrop: React.FC<{slide: BriefingSlide}> = ({slide}) => {
  const frame = useCurrentFrame();
  const tone = accent(slide);
  const stableSurface = slide.kind === "intro";
  const driftX = stableSurface ? 0 : Math.sin(frame / 52) * 18;
  const driftY = stableSurface ? 0 : Math.cos(frame / 61) * 12;
  return (
    <>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "linear-gradient(126deg,#fbfffe 0%,#edf8f6 48%,#fff7f0 100%)",
        }}
      />
      <div
        style={{
          position: "absolute",
          width: 780,
          height: 780,
          right: -220 + driftX,
          top: -350 + driftY,
          borderRadius: "50%",
          background: `radial-gradient(circle,${alpha(tone, "30")},${alpha(tone, "0c")} 48%,transparent 72%)`,
          filter: "blur(4px)",
        }}
      />
      <div
        style={{
          position: "absolute",
          width: 660,
          height: 660,
          left: 180 - driftX,
          bottom: -420 - driftY,
          borderRadius: "50%",
          background: `radial-gradient(circle,${alpha(T.orange, "25")},transparent 68%)`,
        }}
      />
      <div
        style={{
          position: "absolute",
          left: RAIL_WIDTH,
          right: 0,
          top: 0,
          bottom: TIMELINE_HEIGHT,
          opacity: 0.52,
          backgroundImage:
            "linear-gradient(rgba(22,111,109,.075) 1px,transparent 1px),linear-gradient(90deg,rgba(22,111,109,.055) 1px,transparent 1px)",
          backgroundSize: "56px 56px",
          maskImage: "linear-gradient(to bottom,rgba(0,0,0,.8),rgba(0,0,0,.12))",
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: `0 0 ${TIMELINE_HEIGHT}px ${RAIL_WIDTH}px`,
          overflow: "hidden",
        }}
      >
        <SignalDots frame={stableSurface ? 0 : frame} color={tone} />
      </div>
    </>
  );
};

const Rail: React.FC<{slide: BriefingSlide}> = ({slide}) => {
  const displayIndex =
    slide.kind === "news" && (slide.storyPosition || 0) > 0
      ? slide.storyPosition || 0
      : slide.kind === "intro"
        ? 0
        : storyCount(slide);
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        top: 0,
        bottom: TIMELINE_HEIGHT,
        width: RAIL_WIDTH,
        borderRight: `1px solid ${T.line}`,
        background: "rgba(255,255,255,.74)",
        backdropFilter: "blur(16px)",
      }}
    >
      <div
        style={{
          position: "absolute",
          left: 34,
          top: 42,
          width: 28,
          height: 28,
          border: `7px solid ${T.cyan}`,
          borderTopColor: T.orange,
          transform: "rotate(45deg)",
          boxShadow: `0 7px 20px ${alpha(T.cyan, "33")}`,
        }}
      />
      <div
        style={{
          position: "absolute",
          left: "50%",
          top: "48%",
          transform: "translate(-50%,-50%) rotate(-90deg)",
          whiteSpace: "nowrap",
          letterSpacing: 7,
          fontSize: 14,
          fontWeight: 900,
          color: T.cyan,
        }}
      >
        AI DAILY BRIEF / 24H NEWSROOM
      </div>
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 28,
          textAlign: "center",
        }}
      >
        <div style={{fontSize: 40, lineHeight: 1, fontWeight: 950, color: T.text}}>
          {String(displayIndex).padStart(2, "0")}
        </div>
        <div style={{fontSize: 10, letterSpacing: 2, color: T.muted, marginTop: 8}}>
          SIGNAL
        </div>
      </div>
    </div>
  );
};

const TopSignalBar: React.FC<{slide: BriefingSlide; frame: number}> = ({slide, frame}) => {
  const items = normalizedTimeline(slide.timelineItems || []).filter((item) => item.kind === "news");
  const ticker = items.map((item) => clean(item.entity || item.label)).filter(Boolean);
  const tickerRows = ticker.length ? ticker : ["AI DAILY BRIEF", "24H EDITION"];
  return (
    <div
      style={{
        position: "absolute",
        left: RAIL_WIDTH + 38,
        right: 42,
        top: 22,
        height: 45,
        display: "flex",
        alignItems: "center",
        gap: 20,
        borderBottom: `1px solid ${T.line}`,
        color: T.muted,
        fontSize: 12,
        letterSpacing: 2.2,
        fontWeight: 780,
      }}
    >
      <div style={{display: "flex", alignItems: "center", gap: 10, color: accent(slide), flex: "0 0 auto"}}>
        <span
          style={{
            width: 9,
            height: 9,
            borderRadius: 99,
            background: accent(slide),
            boxShadow: `0 0 16px ${accent(slide)}`,
            opacity: 0.65 + Math.sin(frame / 7) * 0.25,
          }}
        />
        LIVE / AI 日报 · 24 小时 AI 新闻
      </div>
      <div style={{height: 14, width: 1, background: T.line}} />
      <div style={{flex: 1, overflow: "hidden", whiteSpace: "nowrap"}}>
        <div style={{display: "inline-flex", gap: 28}}>
          {tickerRows.map(
            (label, index) => (
              <span key={`${label}-${index}`}>
                <b style={{color: index % 2 ? T.electric : T.text}}>+</b>&nbsp; {cut(label, 18)}
              </span>
            ),
          )}
        </div>
      </div>
      <div style={{flex: "0 0 auto", color: T.text}}>{clean(slide.runLabel) || "MORNING EDITION"}</div>
    </div>
  );
};

const entityColor = (label: string, index: number) => {
  const palette = [T.cyan, T.electric, T.orange, T.yellow, "#7967d9", "#2baf69"];
  const hash = [...clean(label)].reduce((total, char) => total + char.charCodeAt(0), index * 7);
  return palette[Math.abs(hash) % palette.length];
};

const timelineClock = (seconds: number) => {
  const whole = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
};

const Timeline: React.FC<{slide: BriefingSlide; compositionFrame: number}> = ({slide, compositionFrame}) => {
  const {fps} = useVideoConfig();
  const items = normalizedTimeline(slide.timelineItems || []);
  const total = Math.max(1, slide.timelineTotal || slide.end || 1);
  const globalTime = Math.min(total, compositionFrame / fps);
  const track = timelineTrackGeometry(DESIGN_WIDTH, TIMELINE_TRACK_INSET);
  const progressX = timelineXAtTime(globalTime, total, DESIGN_WIDTH, TIMELINE_TRACK_INSET);
  const activeIndex = timelineItemIndexAtTime(items, globalTime);
  const activeItem = activeIndex >= 0 ? items[activeIndex] : undefined;
  const activeLabel = activeItem?.kind === "intro"
    ? "START"
    : clean(activeItem?.entity || activeItem?.label || "").replace(/^\d{1,2}\s*/, "");
  const fontSize = items.length >= 9 ? 11 : items.length >= 7 ? 12 : 14;
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        bottom: 0,
        height: TIMELINE_HEIGHT,
        background: "rgba(255,255,255,.94)",
        borderTop: `1px solid ${T.line}`,
        boxShadow: "0 -14px 38px rgba(24,75,74,.055)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          left: 14,
          right: 14,
          top: 4,
          height: 17,
          display: "flex",
          alignItems: "center",
          gap: 12,
          zIndex: 6,
        }}
      >
        <b style={{color: T.cyan, fontSize: 12, letterSpacing: 2.5}}>NEWS MAP</b>
        <span style={{color: T.muted, fontSize: 11}}>
          {timelineClock(globalTime)} · {activeLabel || "START"}
        </span>
      </div>
      <div
        style={{
          position: "absolute",
          left: track.leftPx,
          width: track.widthPx,
          bottom: 4,
          height: 3,
          borderRadius: 99,
          background: T.line,
          overflow: "hidden",
          zIndex: 3,
        }}
      >
        <div
          style={{
            width: Math.max(0, progressX - track.leftPx),
            height: "100%",
            background: `linear-gradient(90deg,${T.cyan},${accent(slide)})`,
          }}
        />
      </div>
      {items.map((item, index) => {
          const active = index === activeIndex;
          const color = item.kind === "intro" ? T.muted : entityColor(item.entity || item.label, index);
          const label = item.kind === "intro" ? "START" : clean(item.entity || item.label).replace(/^\d{1,2}\s*/, "");
          const geometry = timelineItemPixelGeometry(item, total, DESIGN_WIDTH, TIMELINE_TRACK_INSET);
          return (
            <div
              key={`${item.start}-${item.label}`}
              style={{
                position: "absolute",
                left: geometry.leftPx,
                width: geometry.widthPx,
                top: 25,
                bottom: 5,
                paddingLeft: index === 0 ? 0 : 4,
                paddingRight: index === items.length - 1 ? 0 : 4,
                paddingBottom: 6,
                boxSizing: "border-box",
                minWidth: 0,
              }}
            >
              <div
                style={{
                  position: "relative",
                  width: "100%",
                  height: "100%",
                  borderRadius: 12,
                  border: `1px solid ${active ? alpha(color, "88") : T.line}`,
                  background: active ? alpha(color, "12") : "rgba(240,247,246,.62)",
                  overflow: "hidden",
                  boxShadow: active ? `0 7px 20px ${alpha(color, "1f")}` : "none",
                  boxSizing: "border-box",
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    left: 10,
                    right: 8,
                    top: 0,
                    bottom: 0,
                    display: "flex",
                    alignItems: "center",
                    gap: 7,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                  }}
                >
                  <span style={{width: 7, height: 7, borderRadius: 99, background: color, flex: "0 0 auto"}} />
                  <b style={{fontSize, color: active ? T.text : T.muted, overflow: "hidden", textOverflow: "ellipsis"}}>
                    {cut(label, items.length >= 9 ? 8 : 13)}
                  </b>
                </div>
              </div>
            </div>
          );
      })}
    </div>
  );
};

const Kicker: React.FC<{
  slide: BriefingSlide;
  label: string;
  frame: number;
  delay?: number;
  animated?: boolean;
}> = ({slide, label, frame, delay = 0, animated = true}) => (
  <MotionBlock frame={frame} delay={delay} fromX={-22} fromY={0} animated={animated}>
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 13,
        marginBottom: 16,
        color: accent(slide),
        fontSize: 14,
        fontWeight: 900,
        letterSpacing: 3.4,
      }}
    >
      <span style={{width: 34, height: 3, borderRadius: 99, background: accent(slide)}} />
      {label} / {cut(slide.kicker || slide.activeTab, 28)}
    </div>
  </MotionBlock>
);

const Title: React.FC<{
  slide: BriefingSlide;
  size?: number;
  frame: number;
  delay?: number;
}> = ({slide, size = 72, frame, delay = 0}) => {
  const progress = reveal(frame, delay);
  const text = cut(slide.title, 58);
  const adaptiveSize = Math.min(size, text.length > 42 ? 50 : text.length > 31 ? 58 : text.length > 23 ? 65 : size);
  const titleFamily = T.display;
  return (
    <div style={{position: "relative", paddingLeft: 22, maxWidth: "100%"}}>
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 4,
          bottom: 4,
          width: 7,
          borderRadius: 99,
          background: accent(slide),
          transform: `scaleY(${progress})`,
          transformOrigin: "top center",
          boxShadow: `0 0 18px ${alpha(accent(slide), "55")}`,
        }}
      />
      <div style={{overflow: "hidden", padding: "2px 0 5px"}}>
        <div
          style={{
            fontWeight: 950,
            fontSize: adaptiveSize,
            fontFamily: titleFamily,
            lineHeight: 1.08,
            letterSpacing: -2.2,
            opacity: progress,
            transform: `translate3d(0, ${(1 - progress) * 36}px, 0)`,
            clipPath: `inset(0 ${(1 - progress) * 12}% 0 0)`,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          <RichText text={text} emphasisColor={accent(slide)} />
        </div>
      </div>
    </div>
  );
};

const cardTone = (card: BriefingCard, slideTone: string, index: number) => {
  const label = `${clean(card.title)} ${clean(card.body)}`;
  if (/证据|边界|消息性质|待确认|风险/.test(label)) return T.orange;
  if (/价格|成本|指标|性能|速度|延迟|参数/.test(label)) return T.electric;
  if (/时间|来源|原文/.test(label)) return T.yellow;
  return index === 0 ? slideTone : index % 2 ? T.cyan : slideTone;
};

const FactGrid: React.FC<{
  cards: BriefingCard[];
  frame: number;
  accentColor: string;
  headline?: boolean;
}> = ({cards, frame, accentColor, headline = false}) => {
  const visible = (cards.length ? cards : [{title: "今日重点", body: "已核验的具体变化。"}]).slice(0, 6);
  const count = visible.length;
  const columns = count === 1 ? 1 : count <= 4 ? 2 : 3;
  const rows = count <= 2 ? 1 : 2;
  const splitFeature = count === 3 || count === 5;
  const height = count <= 2 ? 386 : 438;
  const templateColumns =
    count === 3
      ? "minmax(0,1.22fr) minmax(0,.78fr)"
      : count === 5
        ? "minmax(0,1.08fr) repeat(2,minmax(0,.96fr))"
        : `repeat(${columns},minmax(0,1fr))`;
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: templateColumns,
        gridTemplateRows: `repeat(${rows},minmax(0,1fr))`,
        gap: count >= 5 ? 14 : 18,
        width: "100%",
        height,
      }}
    >
      {visible.map((card, index) => {
        const progress = reveal(frame, 12 + index * 4, 128);
        const featured = index === 0 && (count === 1 || count === 3 || count === 5 || headline);
        const tone = cardTone(card, accentColor, index);
        const bodySize =
          count === 1 ? 40 : count === 2 ? 29 : featured ? 30 : count >= 5 ? 20 : 24;
        return (
          <div
            key={`${clean(card.title)}-${index}`}
            style={{
              position: "relative",
              minWidth: 0,
              overflow: "hidden",
              gridRow: splitFeature && index === 0 ? "1 / span 2" : undefined,
              borderRadius: featured ? 26 : 18,
              border: `1px solid ${featured ? alpha(tone, "66") : T.line}`,
              background: featured
                ? `linear-gradient(145deg,rgba(255,255,255,.97),${alpha(tone, "13")})`
                : "rgba(255,255,255,.82)",
              boxShadow: featured
                ? `0 24px 58px ${alpha(tone, "18")}`
                : "0 14px 34px rgba(24,75,74,.075)",
              padding:
                count === 1
                  ? "38px 58px"
                  : featured
                    ? "32px 38px"
                    : count >= 5
                      ? "22px 24px"
                      : "27px 31px",
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              opacity: progress,
              clipPath: `inset(0 ${(1 - progress) * 16}% 0 0 round ${featured ? 26 : 18}px)`,
              transform: `translate3d(0, ${(1 - progress) * 34}px,0) scale(${0.972 + progress * 0.028})`,
              transformOrigin: "center bottom",
              willChange: "transform,opacity,clip-path",
            }}
          >
            <div
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                bottom: 0,
                width: featured ? 7 : 4,
                background: tone,
              }}
            />
            <div style={{position: "relative", zIndex: 1}}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  marginBottom: count === 1 ? 22 : featured ? 18 : 12,
                }}
              >
                <span
                  style={{
                    width: featured ? 34 : 28,
                    height: featured ? 34 : 28,
                    borderRadius: 9,
                    background: alpha(tone, "18"),
                    color: tone,
                    display: "grid",
                    placeItems: "center",
                    fontWeight: 950,
                    fontSize: featured ? 18 : 15,
                    flex: "0 0 auto",
                  }}
                >
                  {clean(card.icon) || "+"}
                </span>
                <b
                  style={{
                    color: tone,
                    fontSize: count === 1 ? 24 : featured ? 22 : count >= 5 ? 17 : 20,
                    letterSpacing: 0.5,
                    flex: 1,
                  }}
                >
                  {cut(card.title || `信息 ${index + 1}`, 22)}
                </b>
                <span
                  style={{
                    color: alpha(tone, "88"),
                    fontSize: count === 1 ? 45 : featured ? 34 : 24,
                    lineHeight: 1,
                    fontWeight: 950,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {String(index + 1).padStart(2, "0")}
                </span>
              </div>
              <div
                style={{
                  color: T.text,
                  fontSize: bodySize,
                  lineHeight: count === 1 ? 1.43 : 1.48,
                  fontWeight: featured ? 720 : 610,
                  letterSpacing: bodySize >= 29 ? -0.4 : 0,
                }}
              >
                <RichText text={clean(card.body)} emphasisColor={tone} />
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

export const ScrollViewport: React.FC<{cards: BriefingCard[]; frame: number}> = ({cards, frame}) => {
  const travel = Math.max(0, cards.length * 138 - 490);
  const y = interpolate(frame, [18, 90, 150, 230], [0, 0, -travel * 0.55, -travel], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const progress = travel ? clamp(-y / travel) : 0;
  return (
    <div
      style={{
        height: 620,
        borderRadius: 24,
        overflow: "hidden",
        background: T.panel,
        border: `1px solid ${T.line}`,
        boxShadow: "0 30px 90px rgba(8,35,38,.26)",
      }}
    >
      <div
        style={{
          height: 52,
          borderBottom: `1px solid ${T.line}`,
          display: "flex",
          alignItems: "center",
          gap: 9,
          padding: "0 18px",
          color: T.muted,
          fontSize: 13,
        }}
      >
        <i style={{width: 11, height: 11, borderRadius: 9, background: T.orange}} />
        <i style={{width: 11, height: 11, borderRadius: 9, background: T.yellow}} />
        <i style={{width: 11, height: 11, borderRadius: 9, background: T.cyan}} />
        <span style={{marginLeft: 16, background: T.panel2, borderRadius: 9, padding: "7px 18px", flex: 1}}>
          BACKGROUND / 新闻资料
        </span>
      </div>
      <div style={{position: "relative", height: 568, overflow: "hidden"}}>
        <div style={{padding: "20px 38px", transform: `translateY(${y}px)`}}>
          {cards.map((card, index) => (
            <div key={`${card.title}-${index}`} style={{padding: "19px 0 23px", borderBottom: `1px solid ${T.line}`}}>
              <b style={{color: index % 2 ? T.text : T.cyan, fontSize: 22}}>
                {cut(card.title || `证据 ${index + 1}`, 34)}
              </b>
              <p style={{color: T.muted, fontSize: 19, lineHeight: 1.55, marginBottom: 0}}>
                {clean(card.body)}
              </p>
            </div>
          ))}
        </div>
        <div style={{position: "absolute", right: 9, top: 12, bottom: 12, width: 5, background: "#dceae7", borderRadius: 4}}>
          <div style={{height: 82, transform: `translateY(${progress * 456}px)`, background: T.cyan, borderRadius: 4}} />
        </div>
      </div>
    </div>
  );
};

const sourceHost = (url?: string) => {
  try {
    return new URL(url || "").hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
};

export const EvidenceImage: React.FC<{
  visual?: EvidenceVisual | null;
  compact?: boolean;
  height?: number;
}> = ({visual, compact = false, height}) => {
  if (!visual?.asset) return null;
  return (
    <div
      style={{
        height: height ?? (compact ? 430 : 710),
        borderRadius: 24,
        overflow: "hidden",
        background: "#fff",
        border: "1px solid rgba(255,255,255,.74)",
        boxShadow: "0 34px 110px rgba(4,24,27,.36)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          height: 54,
          borderBottom: `1px solid ${T.line}`,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 20px",
          fontSize: 13,
          color: T.muted,
          flex: "0 0 auto",
        }}
      >
        <div style={{display: "flex", alignItems: "center", gap: 9}}>
          <i style={{width: 10, height: 10, borderRadius: 99, background: T.orange}} />
          <i style={{width: 10, height: 10, borderRadius: 99, background: T.yellow}} />
          <i style={{width: 10, height: 10, borderRadius: 99, background: T.cyan}} />
          <b style={{marginLeft: 10, color: T.orange, letterSpacing: 2}}>
            {visual.required ? "ORIGINAL POST / 原始帖文" : "NEWS SOURCE / 原文画面"}
          </b>
        </div>
        <span>{cut(sourceHost(visual.url) || visual.source, 38)}</span>
      </div>
      <div style={{position: "relative", flex: 1, minHeight: 0, padding: compact ? 10 : 14, background: "#edf3f2", overflow: "hidden"}}>
        <Img
          src={staticFile(visual.asset)}
          style={{width: "100%", height: "100%", objectFit: "contain", objectPosition: "center", background: "#fff"}}
        />
      </div>
      <div
        style={{
          height: 44,
          padding: "11px 17px",
          fontSize: 13,
          color: T.muted,
          borderTop: `1px solid ${T.line}`,
          flex: "0 0 auto",
        }}
      >
        {cut(visual.source || "来源页面", 52)}
      </div>
    </div>
  );
};

const Opening = ({slide, frame}: {slide: BriefingSlide; frame: number}) => {
  const cards = (slide.page.cards || slide.page.background_cards || []).slice(0, 4);
  const lead = clean(slide.page.lead);
  const count = storyCount(slide);
  const ring = interpolate(frame, [0, 300], [-8, 20], {extrapolateRight: "clamp"});
  return (
    <div
      style={{
        position: "absolute",
        left: RAIL_WIDTH + 60,
        right: 58,
        top: 92,
        bottom: TIMELINE_HEIGHT + 28,
        display: "grid",
        gridTemplateColumns: "minmax(0,1.08fr) minmax(480px,.92fr)",
        gap: 58,
        alignItems: "center",
      }}
    >
      <div style={{position: "relative", zIndex: 2}}>
        <Kicker slide={slide} label="MORNING EDITION" frame={frame} animated={false} />
        <div style={{position: "relative"}}>
          <MotionBlock frame={frame} delay={4} fromY={45} animated={false}>
            <div style={{fontSize: 116, lineHeight: 0.92, fontWeight: 950, letterSpacing: -6, color: T.text}}>
              今日
            </div>
          </MotionBlock>
          <MotionBlock frame={frame} delay={8} fromX={48} fromY={0} animated={false}>
            <div style={{fontSize: 116, lineHeight: 0.98, fontWeight: 950, letterSpacing: -6, color: T.cyan, fontFamily: T.display}}>
              AI 日报
            </div>
          </MotionBlock>
          <MotionBlock frame={frame} delay={13} fromScale={0.86} fromY={0} style={{position: "absolute", right: 14, top: 38}}>
            <div
              style={{
                width: 156,
                height: 156,
                borderRadius: "50%",
                border: `2px solid ${alpha(T.orange, "66")}`,
                display: "grid",
                placeItems: "center",
                transform: `rotate(${ring}deg)`,
                background: "rgba(255,255,255,.64)",
                boxShadow: `0 20px 50px ${alpha(T.orange, "16")}`,
              }}
            >
              <div style={{textAlign: "center", transform: `rotate(${-ring}deg)`}}>
                <b style={{display: "block", fontSize: 58, lineHeight: 1, color: T.orange}}>{String(count).padStart(2, "0")}</b>
                <span style={{display: "block", marginTop: 7, color: T.muted, fontSize: 11, letterSpacing: 2}}>STORIES</span>
              </div>
            </div>
          </MotionBlock>
        </div>
        <MotionBlock frame={frame} delay={17} fromY={18} animated={false}>
          <p style={{fontSize: 27, color: T.muted, margin: "30px 0 0", fontWeight: 620}}>
            过去 24 小时，值得你花时间了解的 AI 新闻。
          </p>
        </MotionBlock>
        {lead && (
          <MotionBlock frame={frame} delay={22} fromY={18} animated={false} style={{marginTop: 36}}>
            <div
              style={{
                borderLeft: `6px solid ${T.orange}`,
                padding: "17px 22px",
                borderRadius: "0 16px 16px 0",
                background: "rgba(255,255,255,.76)",
                boxShadow: "0 13px 34px rgba(24,75,74,.07)",
              }}
            >
              <span style={{display: "block", color: T.orange, fontSize: 12, fontWeight: 900, letterSpacing: 2.5, marginBottom: 8}}>
                TODAY&apos;S LEAD / 今日主线
              </span>
              <div style={{fontSize: 24, lineHeight: 1.45, fontWeight: 760}}>
                <RichText text={cut(lead, 70)} emphasisColor={T.orange} />
              </div>
            </div>
          </MotionBlock>
        )}
      </div>
      <div style={{position: "relative", zIndex: 2}}>
        <div
          style={{
            position: "absolute",
            width: 590,
            height: 590,
            borderRadius: "50%",
            border: `1px solid ${alpha(T.cyan, "33")}`,
            left: "50%",
            top: "50%",
            transform: `translate(-50%,-50%) rotate(${ring * 0.45}deg)`,
          }}
        >
          <div style={{position: "absolute", inset: 60, borderRadius: "50%", border: `1px dashed ${alpha(T.electric, "44")}`}} />
        </div>
        <MotionBlock frame={frame} delay={10} fromX={52} fromY={0}>
          <div
            style={{
              position: "relative",
              padding: "24px",
              borderRadius: 28,
              background: "rgba(255,255,255,.84)",
              border: `1px solid ${T.line}`,
              boxShadow: "0 30px 75px rgba(24,75,74,.12)",
              backdropFilter: "blur(12px)",
            }}
          >
            <div style={{display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 13}}>
              <b style={{fontSize: 13, letterSpacing: 2.5, color: T.cyan}}>TODAY / 今日看点</b>
              <span style={{fontSize: 12, color: T.muted}}>24H EDITION</span>
            </div>
            {(cards.length ? cards : [{title: "今日要闻", body: "正在核对今日来源。"}]).map((card, index) => {
              const progress = reveal(frame, 15 + index * 5);
              const tone = entityColor(card.title || "", index);
              return (
                <div
                  key={`${card.title}-${index}`}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "54px minmax(0,1fr)",
                    gap: 15,
                    padding: "16px 9px",
                    borderTop: index ? `1px solid ${T.line}` : "none",
                    opacity: progress,
                    transform: `translateX(${(1 - progress) * 26}px)`,
                  }}
                >
                  <div
                    style={{
                      width: 48,
                      height: 48,
                      borderRadius: 15,
                      background: alpha(tone, "16"),
                      color: tone,
                      display: "grid",
                      placeItems: "center",
                      fontSize: 19,
                      fontWeight: 950,
                    }}
                  >
                    {String(index + 1).padStart(2, "0")}
                  </div>
                  <div style={{minWidth: 0}}>
                    <b style={{display: "block", fontSize: 20, color: T.text}}>{cut(card.title, 22)}</b>
                    <span style={{display: "block", marginTop: 6, fontSize: 15, lineHeight: 1.45, color: T.muted}}>
                      {cut(card.body, 52)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </MotionBlock>
      </div>
    </div>
  );
};

const Overview = ({slide, frame}: {slide: BriefingSlide; frame: number}) => {
  const cards = (slide.page.cards || slide.page.background_cards || []).slice(0, 6);
  return (
    <div style={{position: "absolute", left: RAIL_WIDTH + 62, right: 56, top: 105, bottom: TIMELINE_HEIGHT + 40}}>
      <Kicker slide={slide} label="FRONT PAGE / 编辑部头版" frame={frame} />
      <Title slide={slide} frame={frame} delay={4} />
      <AccentRule frame={frame} delay={8} width={116} height={4} color={accent(slide)} style={{margin: "20px 0 26px 22px"}} />
      <FactGrid cards={cards} frame={frame} accentColor={accent(slide)} headline />
    </div>
  );
};

const NewsBoard = ({slide, frame, brief = false}: {slide: BriefingSlide; frame: number; brief?: boolean}) => {
  const cards = (slide.page.cards || []).length
    ? slide.page.cards || []
    : [{title: "核心变化", body: slide.page.lead || slide.caption}];
  const lead = clean(slide.page.lead);
  const showLead = Boolean(lead) && cards.length <= 4;
  const sourceProgress = reveal(frame, 8);
  const storyNumber = String(Math.max(1, slide.storyPosition || 1)).padStart(2, "0");
  return (
    <div
      style={{
        position: "absolute",
        left: RAIL_WIDTH + 58,
        right: 50,
        top: 86,
        bottom: TIMELINE_HEIGHT + 25,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          position: "absolute",
          right: -8,
          top: -42,
          fontSize: 230,
          lineHeight: 1,
          fontWeight: 950,
          letterSpacing: -15,
          color: alpha(accent(slide), "0d"),
          pointerEvents: "none",
        }}
      >
        {storyNumber}
      </div>
      <div style={{position: "relative", zIndex: 1}}>
        <Kicker slide={slide} label={brief ? "NEWS DESK / 24 小时快讯" : "LEAD STORY / 今日新闻"} frame={frame} />
        <div style={{display: "grid", gridTemplateColumns: "minmax(0,1fr) 330px", gap: 34, alignItems: "end"}}>
          <Title slide={slide} size={brief ? 68 : 74} frame={frame} delay={4} />
          <div
            style={{
              padding: "13px 16px",
              marginBottom: 4,
              borderRadius: 15,
              border: `1px solid ${alpha(accent(slide), "55")}`,
              background: "rgba(255,255,255,.72)",
              textAlign: "right",
              opacity: sourceProgress,
              transform: `translateX(${(1 - sourceProgress) * 25}px)`,
            }}
          >
            <b style={{display: "block", color: accent(slide), fontSize: 20}}>
              {storyNumber} / {String(Math.max(1, slide.storyTotal || storyCount(slide))).padStart(2, "0")}
            </b>
            <span style={{display: "block", marginTop: 5, color: T.muted, fontSize: 13, letterSpacing: 1.2}}>
              SOURCE / {cut(slide.page.source || slide.activeTab, 32)}
            </span>
          </div>
        </div>
        {showLead && (
          <MotionBlock frame={frame} delay={9} fromY={14} animated={false} style={{margin: "18px 0"}}>
            <div
              style={{
                display: "flex",
                gap: 16,
                alignItems: "center",
                padding: "13px 19px",
                borderRadius: 15,
                background: "rgba(250,252,251,.98)",
                border: `1px solid ${alpha(accent(slide), "36")}`,
              }}
            >
              <b style={{color: accent(slide), fontSize: 12, letterSpacing: 2.3, whiteSpace: "nowrap"}}>WHAT CHANGED</b>
              <span style={{height: 24, width: 1, background: alpha(accent(slide), "55")}} />
              <div style={{fontSize: 19, lineHeight: 1.42, color: T.text, fontWeight: 660}}>
                <RichText text={cut(lead, 96)} emphasisColor={accent(slide)} />
              </div>
            </div>
          </MotionBlock>
        )}
        {!showLead && <AccentRule frame={frame} delay={9} width={128} height={4} color={accent(slide)} style={{margin: "18px 0"}} />}
        <DispatchLedger cards={cards} frame={frame} accentColor={accent(slide)} />
      </div>
    </div>
  );
};

const Evidence = ({slide, frame}: {slide: BriefingSlide; frame: number}) => {
  const cards = slide.page.cards || slide.page.background_cards || [];
  const visual = slide.page.evidenceVisual;
  const {fps} = useVideoConfig();
  const pop = clamp(
    spring({frame: Math.max(0, frame - 10), fps, config: {damping: 17, stiffness: 142, mass: 0.68}}),
  );
  const shade = interpolate(frame, [3, 15], [0, 0.62], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div style={{position: "absolute", inset: `68px 0 ${TIMELINE_HEIGHT}px ${RAIL_WIDTH}px`}}>
      <div style={{position: "absolute", inset: 0, opacity: 1 - shade * 0.62, filter: `blur(${shade * 3}px)`}}>
        <NewsBoard slide={slide} frame={frame + 42} />
      </div>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: `rgba(8,31,34,${shade})`,
          backdropFilter: `blur(${shade * 3}px)`,
        }}
      />
      <div
        style={{
          position: "absolute",
          left: "50%",
          top: "50%",
          width: visual?.asset ? 1540 : 1260,
          transform: `translate3d(-50%,calc(-50% + ${(1 - pop) * 58}px),0) scale(${0.84 + pop * 0.16})`,
          opacity: Math.min(1, pop * 1.08),
          willChange: "transform,opacity",
        }}
      >
        {visual?.asset ? (
          <>
            <EvidenceImage visual={visual} height={720} />
            <div
              style={{
                marginTop: 14,
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                color: "rgba(255,255,255,.94)",
                fontSize: 14,
                letterSpacing: 1.5,
              }}
            >
              <b style={{display: "flex", alignItems: "center", gap: 10}}>
                <span style={{width: 9, height: 9, borderRadius: 99, background: T.cyan, boxShadow: `0 0 14px ${T.cyan}`}} />
                SOURCE / 原文画面
              </b>
              <span>{cut(visual.source || sourceHost(visual.url), 48)}</span>
            </div>
          </>
        ) : (
          <ScrollViewport cards={cards.length ? cards : [{title: slide.title, body: slide.caption}]} frame={frame} />
        )}
      </div>
    </div>
  );
};

const InlineClosing: React.FC<{slide: BriefingSlide; frame: number; duration: number}> = ({slide, frame, duration}) => {
  const {fps} = useVideoConfig();
  const closingFrames = Math.max(1, Math.round((slide.closingDuration || 0) * fps));
  if (!slide.closingDuration) return null;
  const local = frame - (duration - closingFrames);
  if (local < -2) return null;
  const progress = reveal(local, 0, 128);
  return (
    <div
      style={{
        position: "absolute",
        right: 58,
        top: 96,
        width: 610,
        borderRadius: 22,
        background: "rgba(255,255,255,.95)",
        border: `1px solid ${alpha(accent(slide), "77")}`,
        borderLeft: `8px solid ${accent(slide)}`,
        padding: "22px 28px 23px",
        boxShadow: `0 26px 70px ${alpha(accent(slide), "28")}`,
        opacity: progress,
        transform: `translate3d(${(1 - progress) * 48}px,0,0) scale(${0.96 + progress * 0.04})`,
        zIndex: 20,
      }}
    >
      <div style={{color: accent(slide), fontSize: 12, fontWeight: 900, letterSpacing: 2.6, marginBottom: 8}}>
        SIGNALS COMPLETE / 播送完毕
      </div>
      <div style={{fontSize: 30, lineHeight: 1.35, fontWeight: 850, color: T.text}}>
        {slide.closingText || "今天的新闻播送完毕，我们明天见。"}
      </div>
    </div>
  );
};

// ---- AI DAILY BRIEF house style: dispatch ledger, not card boxes. ----------
// Facts read as numbered telegraph lines with dashed stop-rules; the source
// is a rotated postmark. Visually distinct from rounded-card briefing shows.

const DispatchRow: React.FC<{
  card: BriefingCard;
  index: number;
  frame: number;
  tone: string;
  compact?: boolean;
}> = ({card, index, frame, tone, compact = false}) => {
  const progress = reveal(frame, 12 + index * 5, 128);
  return (
    <div
      style={{
        position: "relative",
        display: "flex",
        alignItems: "flex-start",
        gap: compact ? 14 : 18,
        padding: compact ? "16px 6px 16px 16px" : "20px 8px 20px 20px",
        borderBottom: `2px dashed ${T.line}`,
        opacity: progress,
        transform: `translate3d(${(1 - progress) * 26}px,0,0)`,
        willChange: "transform,opacity",
        minWidth: 0,
      }}
    >
      <div style={{position: "absolute", left: 0, top: 14, bottom: 14, width: 4, borderRadius: 4, background: alpha(tone, "cc")}} />
      <span
        style={{
          color: alpha(tone, "99"),
          fontWeight: 950,
          fontSize: compact ? 19 : 21,
          fontVariantNumeric: "tabular-nums",
          letterSpacing: 1,
          flex: "0 0 auto",
          paddingTop: 2,
        }}
      >
        {String(index + 1).padStart(2, "0")}
      </span>
      <div style={{minWidth: 0, flex: 1}}>
        <div style={{display: "flex", alignItems: "baseline", gap: 12, marginBottom: compact ? 6 : 8, minWidth: 0}}>
          <b style={{color: tone, fontSize: compact ? 24 : 27, letterSpacing: 0.4, whiteSpace: "nowrap"}}>
            {cut(card.title || `要点 ${index + 1}`, 18)}
          </b>
          <span style={{flex: 1, minWidth: 24, borderBottom: `2px dotted ${alpha(tone, "44")}`, transform: "translateY(-4px)"}} />
        </div>
        <div style={{fontSize: compact ? 26 : 29, lineHeight: 1.52, fontWeight: 640, color: T.text}}>
          <RichText text={card.body || ""} emphasisColor={tone} />
        </div>
      </div>
    </div>
  );
};

const SourcePostmark: React.FC<{card: BriefingCard; frame: number; tone: string}> = ({card, frame, tone}) => {
  const progress = reveal(frame, 34, 128);
  return (
    <div
      style={{
        position: "absolute",
        right: 4,
        bottom: 2,
        maxWidth: 560,
        padding: "14px 22px",
        border: `2.5px dashed ${alpha(tone, "77")}`,
        borderRadius: 14,
        background: "rgba(255,255,255,.6)",
        transform: `rotate(-1.6deg) scale(${0.94 + progress * 0.06})`,
        opacity: progress * 0.96,
        willChange: "transform,opacity",
      }}
    >
      <div style={{fontSize: 13, fontWeight: 950, letterSpacing: 3.5, color: alpha(tone, "aa"), marginBottom: 5}}>
        SOURCE / 原文与时间
      </div>
      <div style={{fontSize: 22, fontWeight: 760, color: T.text, lineHeight: 1.4}}>
        <RichText text={card.body || ""} emphasisColor={tone} />
      </div>
    </div>
  );
};

const DispatchLedger: React.FC<{
  cards: BriefingCard[];
  frame: number;
  accentColor: string;
}> = ({cards, frame, accentColor}) => {
  const rows = cards.filter((card) => clean(card.title) !== "原文与时间");
  const source = cards.find((card) => clean(card.title) === "原文与时间");
  const visible = (rows.length ? rows : [{title: "核心变化", body: "已核验的具体变化。"}]).slice(0, 6);
  const twoColumns = visible.length >= 5;
  return (
    <div style={{position: "relative", width: "100%", minHeight: 438}}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: twoColumns ? "repeat(2,minmax(0,1fr))" : "minmax(0,1fr)",
          columnGap: 46,
          alignContent: "start",
          paddingBottom: source ? 96 : 0,
        }}
      >
        {visible.map((card, index) => (
          <DispatchRow
            key={`${clean(card.title)}-${index}`}
            card={card}
            index={index}
            frame={frame}
            tone={cardTone(card, accentColor, index)}
            compact={twoColumns}
          />
        ))}
      </div>
      {source ? <SourcePostmark card={source} frame={frame} tone={accentColor} /> : null}
    </div>
  );
};

const TickerBoard = ({slide, frame}: {slide: BriefingSlide; frame: number}) => {
  const cards = (slide.page.cards || []).slice(0, 10);
  const roomy = cards.length <= 6;
  return (
    <div
      style={{
        position: "absolute",
        left: RAIL_WIDTH + 62,
        right: 56,
        top: 105,
        bottom: TIMELINE_HEIGHT + 40,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Kicker slide={slide} label="WIRE DIGEST / 快讯速览" frame={frame} />
      <Title slide={slide} frame={frame} delay={4} />
      <AccentRule frame={frame} delay={8} width={116} height={4} color={accent(slide)} style={{margin: "20px 0 10px 22px"}} />
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(2,minmax(0,1fr))",
          columnGap: 52,
          alignContent: "center",
          flex: 1,
          marginTop: 6,
        }}
      >
        {cards.map((card, index) => {
          const tone = entityColor(card.title || "", index);
          const progress = reveal(frame, 10 + index * 4, 128);
          return (
            <div
              key={`${clean(card.title)}-${index}`}
              style={{
                position: "relative",
                display: "flex",
                alignItems: "baseline",
                gap: 16,
                padding: roomy ? "34px 8px 34px 20px" : "22px 8px 22px 18px",
                borderBottom: `2px dashed ${T.line}`,
                opacity: progress,
                transform: `translate3d(${(1 - progress) * 26}px,0,0)`,
                minWidth: 0,
              }}
            >
              <div style={{position: "absolute", left: 0, top: 16, bottom: 16, width: 4, borderRadius: 4, background: alpha(tone, "cc")}} />
              <span style={{color: alpha(tone, "99"), fontWeight: 950, fontSize: roomy ? 21 : 19, fontVariantNumeric: "tabular-nums", letterSpacing: 1}}>
                {String(index + 1).padStart(2, "0")}
              </span>
              <b style={{color: tone, fontSize: roomy ? 30 : 25, letterSpacing: 0.4, whiteSpace: "nowrap"}}>{cut(card.title || "", 14)}</b>
              <span style={{flex: 1, minWidth: 20, borderBottom: `2px dotted ${alpha(tone, "44")}`, transform: "translateY(-5px)"}} />
              <span style={{fontSize: roomy ? 28 : 24, lineHeight: 1.45, fontWeight: 660, color: T.text, textAlign: "right", maxWidth: "62%"}}>
                <RichText text={card.body || ""} emphasisColor={tone} />
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
};

const Scene: React.FC<{slide: BriefingSlide; duration: number; compositionFrame: number}> = ({slide, duration, compositionFrame}) => {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const canvasScale = Math.min(width / DESIGN_WIDTH, height / DESIGN_HEIGHT);
  const kind = clean(slide.page.kind || slide.kind || "news").toLowerCase();
  const content =
    slide.kind === "intro" ? (
      <Opening slide={slide} frame={frame} />
    ) : kind === "ticker" || slide.kind === "ticker" ? (
      <TickerBoard slide={slide} frame={frame} />
    ) : kind === "overview" ? (
      <Overview slide={slide} frame={frame} />
    ) : kind === "evidence" ? (
      <Evidence slide={slide} frame={frame} />
    ) : kind === "brief" ? (
      <NewsBoard slide={slide} frame={frame} brief />
    ) : (
      <NewsBoard slide={slide} frame={frame} />
    );
  return (
    <AbsoluteFill style={{background: T.bg, overflow: "hidden"}}>
      <FontFaces />
      <div
        style={{
          position: "absolute",
          left: "50%",
          top: "50%",
          width: DESIGN_WIDTH,
          height: DESIGN_HEIGHT,
          transform: `translate(-50%,-50%) scale(${canvasScale})`,
          transformOrigin: "center center",
          color: T.text,
          fontFamily: T.sans,
          overflow: "hidden",
        }}
      >
        <Backdrop slide={slide} />
        <Rail slide={slide} />
        <TopSignalBar slide={slide} frame={frame} />
        <div style={{position: "absolute", inset: 0}}>{content}</div>
        <InlineClosing slide={slide} frame={frame} duration={duration} />
        <Timeline slide={slide} compositionFrame={compositionFrame} />
      </div>
    </AbsoluteFill>
  );
};

const mergeOutroIntoFinalStory = (slides: BriefingSlide[]) => {
  const merged: BriefingSlide[] = [];
  for (const slide of slides || []) {
    if (slide.kind === "outro" && merged.length) {
      const previous = merged[merged.length - 1];
      merged[merged.length - 1] = {
        ...previous,
        duration: previous.duration + slide.duration,
        end: Math.max(previous.end, slide.end),
        closingDuration: slide.duration,
        // Respect the outro segment's own line (kept in sync with the spoken
        // narration); the fallback mirrors render.py's outro CTA.
        closingText: slide.closingText || "今天的新闻播完了。置顶评论有个问题等你聊，觉得有用就点个关注，我们明天早上见。",
      };
      continue;
    }
    merged.push({...slide});
  }
  return merged;
};

export const BriefingVideo: React.FC<BriefingVideoProps> = (props) => {
  const compositionFrame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const slides = mergeOutroIntoFinalStory(props.slides || []);
  return (
    <AbsoluteFill style={{background: T.bg}}>
      <TransitionSeries>
        {slides.map((slide, index) => {
          const duration = Math.max(24, Math.round((slide.duration || 1) * fps));
          return (
            <React.Fragment key={`${slide.index}-${index}`}>
              <TransitionSeries.Sequence durationInFrames={index < slides.length - 1 ? duration + TRANSITION_FRAMES : duration}>
                {props.visualStyle === 'classic' ? <Scene slide={slide} duration={duration} compositionFrame={compositionFrame} /> : <NewsroomScene slide={slide} duration={duration} compositionFrame={compositionFrame} />}
              </TransitionSeries.Sequence>
              {index < slides.length - 1 && (
                <TransitionSeries.Transition
                  timing={linearTiming({durationInFrames: TRANSITION_FRAMES})}
                  presentation={wipe({direction: index % 2 ? "from-right" : "from-bottom-right"})}
                />
              )}
            </React.Fragment>
          );
        })}
      </TransitionSeries>
    </AbsoluteFill>
  );
};
