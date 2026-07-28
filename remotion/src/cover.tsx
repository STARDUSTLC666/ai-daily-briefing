import React from "react";
import {AbsoluteFill} from "remotion";

import type {BriefingSlide, BriefingVideoProps, CoverCopy} from "./video";

const C = {
  bg: "#f4f8f7",
  text: "#102c2d",
  muted: "#587071",
  line: "#cddbd8",
  teal: "#00a98f",
  orange: "#ff6a3d",
  yellow: "#f2bd2e",
  white: "#ffffff",
};

const clean = (value: unknown) => String(value ?? "").replace(/\s+/g, " ").trim();
const cut = (value: unknown, limit: number) => {
  const text = clean(value);
  return text.length <= limit ? text : `${text.slice(0, Math.max(1, limit - 1))}…`;
};

const uniqueNews = (slides: BriefingSlide[]) => {
  const seen = new Set<string>();
  return slides.filter((slide) => {
    if (slide.kind !== "news") return false;
    const key = clean(slide.storyPosition || slide.title || slide.index);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};

const derivedCover = (props: BriefingVideoProps): CoverCopy => {
  const news = uniqueNews(props.slides || []);
  const entities = (props.slides?.[0]?.timelineItems || [])
    .filter((item) => item.kind === "news")
    .map((item) => clean(item.entity || item.label))
    .filter(Boolean);
  return {
    eyebrow: "AI DAILY BRIEF / AI 日报",
    headline: cut(news.at(-1)?.title || "今天的 AI 变化", 24),
    subheadline: cut(news[Math.min(3, Math.max(0, news.length - 1))]?.title || "模型、产品与开发者动态", 32),
    badge: news.length ? `${String(news.length).padStart(2, "0")} 条 AI 动态` : "AI 动态",
    date: clean(props.slides?.[0]?.runLabel),
    storyCount: news.length,
    highlights: news.slice(0, 3).map((slide) => cut(slide.title, 28)),
    entities: entities.slice(0, 6),
  };
};

const headlineSize = (text: string) => (text.length > 18 ? 82 : text.length > 13 ? 98 : 116);

export const BriefingCover: React.FC<BriefingVideoProps> = (props) => {
  const fallback = derivedCover(props);
  const copy = {...fallback, ...(props.cover || {})};
  const highlights = (copy.highlights || fallback.highlights || []).filter(Boolean).slice(0, 3);
  const entities = (copy.entities || fallback.entities || []).filter(Boolean).slice(0, 6);
  const headline = clean(copy.headline || fallback.headline);
  const subheadline = clean(copy.subheadline || fallback.subheadline);
  const storyCount = Math.max(0, Math.round(Number(copy.storyCount ?? fallback.storyCount ?? 0)));

  return (
    <AbsoluteFill
      style={{
        background: C.bg,
        color: C.text,
        fontFamily: '"Noto Sans SC","Microsoft YaHei UI",sans-serif',
        overflow: "hidden",
      }}
    >
      <div style={{position: "absolute", inset: 0, backgroundImage: `linear-gradient(${C.line}55 1px, transparent 1px), linear-gradient(90deg, ${C.line}55 1px, transparent 1px)`, backgroundSize: "72px 72px"}} />
      <div style={{position: "absolute", left: 0, top: 0, bottom: 0, width: 92, background: C.text}}>
        <div style={{position: "absolute", top: 44, left: 25, width: 42, height: 42, transform: "rotate(45deg)", background: C.teal}}>
          <div style={{position: "absolute", inset: 11, background: C.bg}} />
        </div>
        <div style={{position: "absolute", left: 31, bottom: 54, color: C.white, fontSize: 15, fontWeight: 900, writingMode: "vertical-rl", letterSpacing: 0}}>
          AI DAILY BRIEF
        </div>
      </div>

      <div style={{position: "absolute", left: 142, right: 76, top: 46, display: "flex", alignItems: "center", justifyContent: "space-between", borderBottom: `2px solid ${C.line}`, paddingBottom: 20}}>
        <div style={{fontSize: 22, fontWeight: 900, color: C.teal, letterSpacing: 0}}>{clean(copy.eyebrow)}</div>
        <div style={{fontSize: 20, fontWeight: 850, color: C.muted}}>{clean(copy.date || fallback.date || "TODAY")}</div>
      </div>

      <div style={{position: "absolute", left: 142, top: 148, width: 1110}}>
        <div style={{display: "flex", alignItems: "baseline", gap: 24}}>
          <span style={{fontSize: 154, lineHeight: 0.9, fontWeight: 950, color: C.teal}}>AI</span>
          <span style={{fontSize: 74, lineHeight: 1, fontWeight: 950}}>这一天</span>
        </div>
        <div style={{marginTop: 42, borderLeft: `14px solid ${C.orange}`, paddingLeft: 28}}>
          <div style={{fontSize: headlineSize(headline), lineHeight: 1.08, fontWeight: 950, maxWidth: 1030}}>{headline}</div>
          <div style={{marginTop: 24, fontSize: 38, lineHeight: 1.3, fontWeight: 760, color: C.muted, maxWidth: 1000}}>{subheadline}</div>
        </div>
      </div>

      <div style={{position: "absolute", right: 76, top: 162, width: 500, bottom: 174, borderLeft: `2px solid ${C.line}`, paddingLeft: 42}}>
        <div style={{display: "flex", alignItems: "center", gap: 20}}>
          <div style={{fontSize: 126, lineHeight: 1, fontWeight: 950, color: C.orange}}>{String(storyCount).padStart(2, "0")}</div>
          <div>
            <div style={{fontSize: 22, fontWeight: 900, color: C.muted}}>STORIES</div>
            <div style={{fontSize: 25, fontWeight: 900, marginTop: 6}}>{clean(copy.badge)}</div>
          </div>
        </div>
        <div style={{marginTop: 44}}>
          {highlights.map((item, index) => (
            <div key={`${item}-${index}`} style={{display: "grid", gridTemplateColumns: "54px 1fr", gap: 18, padding: "22px 0", borderTop: `2px solid ${C.line}`}}>
              <div style={{fontSize: 25, fontWeight: 950, color: index === 0 ? C.orange : index === 1 ? C.teal : C.yellow}}>{String(index + 1).padStart(2, "0")}</div>
              <div style={{fontSize: 27, lineHeight: 1.34, fontWeight: 850}}>{item}</div>
            </div>
          ))}
        </div>
      </div>

      <div style={{position: "absolute", left: 142, right: 76, bottom: 54, height: 82, display: "flex", alignItems: "center", gap: 12, borderTop: `2px solid ${C.line}`, paddingTop: 20}}>
        {entities.map((entity, index) => (
          <div key={`${entity}-${index}`} style={{height: 52, display: "flex", alignItems: "center", gap: 10, padding: "0 18px", border: `2px solid ${C.line}`, background: C.white, borderRadius: 6, fontSize: 21, fontWeight: 850}}>
            <span style={{width: 9, height: 9, borderRadius: 99, background: [C.teal, C.orange, C.yellow][index % 3]}} />
            {cut(entity, 15)}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

const headline43Size = (text: string) => (text.length > 28 ? 64 : text.length > 22 ? 72 : 82);

export const BriefingCover43: React.FC<BriefingVideoProps> = (props) => {
  const fallback = derivedCover(props);
  const copy = {...fallback, ...(props.cover || {})};
  const highlights = (copy.highlights || fallback.highlights || []).filter(Boolean).slice(0, 3);
  const entities = (copy.entities || fallback.entities || []).filter(Boolean).slice(0, 6);
  const headline = clean(copy.headline || fallback.headline);
  const subheadline = clean(copy.subheadline || fallback.subheadline);
  const storyCount = Math.max(0, Math.round(Number(copy.storyCount ?? fallback.storyCount ?? 0)));

  return (
    <AbsoluteFill
      style={{
        background: C.bg,
        color: C.text,
        fontFamily: '"Noto Sans SC","Microsoft YaHei UI",sans-serif',
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage: `linear-gradient(${C.line}44 1px, transparent 1px), linear-gradient(90deg, ${C.line}44 1px, transparent 1px)`,
          backgroundSize: "64px 64px",
        }}
      />

      <div
        style={{
          position: "absolute",
          left: 64,
          right: 64,
          top: 44,
          height: 64,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          borderBottom: `2px solid ${C.line}`,
        }}
      >
        <div style={{display: "flex", alignItems: "center", gap: 18}}>
          <div style={{width: 34, height: 34, transform: "rotate(45deg)", background: C.teal}}>
            <div style={{position: "absolute", inset: 9, background: C.bg}} />
          </div>
          <div style={{fontSize: 22, fontWeight: 900, color: C.teal}}>{clean(copy.eyebrow)}</div>
        </div>
        <div style={{display: "flex", alignItems: "baseline", gap: 22}}>
          <div style={{fontSize: 20, fontWeight: 850, color: C.muted}}>{clean(copy.date || fallback.date || "TODAY")}</div>
          <div style={{fontSize: 22, fontWeight: 950, color: C.orange}}>{String(storyCount).padStart(2, "0")} STORIES</div>
        </div>
      </div>

      <div style={{position: "absolute", left: 64, right: 64, top: 150}}>
        <div style={{display: "flex", alignItems: "center", gap: 18}}>
          <div style={{fontSize: 60, lineHeight: 1, fontWeight: 950, color: C.teal}}>AI</div>
          <div style={{height: 42, width: 8, background: C.orange}} />
          <div style={{fontSize: 28, fontWeight: 900, color: C.muted}}>这一天</div>
          <div style={{marginLeft: "auto", padding: "10px 18px", border: `2px solid ${C.line}`, background: C.white, borderRadius: 6, fontSize: 21, fontWeight: 900}}>
            {clean(copy.badge)}
          </div>
        </div>

        <div
          style={{
            marginTop: 24,
            maxWidth: 1312,
            fontSize: headline43Size(headline),
            lineHeight: 1.08,
            fontWeight: 950,
            overflowWrap: "break-word",
          }}
        >
          {headline}
        </div>
        <div style={{marginTop: 20, maxWidth: 1240, fontSize: 35, lineHeight: 1.3, fontWeight: 780, color: C.muted}}>
          {subheadline}
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: 568,
          height: 270,
          background: C.white,
          borderTop: `3px solid ${C.text}`,
          borderBottom: `2px solid ${C.line}`,
        }}
      >
        <div style={{position: "absolute", left: 64, right: 64, top: 24, fontSize: 20, fontWeight: 950, color: C.muted}}>TODAY'S SIGNALS</div>
        <div style={{position: "absolute", left: 64, right: 64, top: 70, display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 34}}>
          {highlights.map((item, index) => (
            <div key={`${item}-${index}`} style={{minWidth: 0, borderTop: `7px solid ${[C.orange, C.teal, C.yellow][index]}`, paddingTop: 17}}>
              <div style={{fontSize: 21, fontWeight: 950, color: C.muted}}>{String(index + 1).padStart(2, "0")}</div>
              <div style={{marginTop: 12, fontSize: 27, lineHeight: 1.32, fontWeight: 880, overflowWrap: "break-word"}}>{item}</div>
            </div>
          ))}
        </div>
      </div>

      <div style={{position: "absolute", left: 64, right: 190, top: 878}}>
        <div style={{fontSize: 19, fontWeight: 950, color: C.muted}}>COVERED TODAY</div>
        <div style={{marginTop: 17, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10}}>
          {entities.map((entity, index) => (
            <div
              key={`${entity}-${index}`}
              style={{
                height: 50,
                display: "flex",
                alignItems: "center",
                gap: 9,
                padding: "0 16px",
                border: `2px solid ${C.line}`,
                background: C.white,
                borderRadius: 6,
                fontSize: 20,
                fontWeight: 850,
                whiteSpace: "nowrap",
              }}
            >
              <span style={{width: 8, height: 8, borderRadius: 99, background: [C.teal, C.orange, C.yellow][index % 3]}} />
              {entity}
            </div>
          ))}
        </div>
      </div>

      <div style={{position: "absolute", right: 64, bottom: 55, width: 82, height: 12, background: C.orange}} />
    </AbsoluteFill>
  );
};
