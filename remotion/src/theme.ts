import React from 'react';

export const DESIGN_WIDTH = 1920;
export const DESIGN_HEIGHT = 1080;
export const COVER_43_WIDTH = 1440;

export const SAFE_X = 88;
export const SAFE_TOP = 148;
// Ensures y <= 872px for all scene body copy, leaving y = 880..985px completely clear
// for burned-in ASS subtitles and y = 996..1052px for ChapterTrack.
export const SUBTITLE_SAFE_BOTTOM = 208;
export const CHAPTER_BOTTOM = 28;
export const CHAPTER_HEIGHT = 52;

export const EDITORIAL_WORD_WRAP: React.CSSProperties = {
  wordBreak: 'keep-all',
  overflowWrap: 'break-word',
};

export const theme = {
  paper: '#F6F4EE',
  card: '#FFFFFF',
  tint: '#EDE9DF',
  ink: '#16181A',
  inkSecondary: '#525860',
  muted: '#767D86',
  hairline: 'rgba(22, 24, 26, 0.14)',
  ruleStrong: '#16181A',
  vermilion: '#C83E2B',
  vermilionSoft: 'rgba(200, 62, 43, 0.10)',
  vermilionBorder: 'rgba(200, 62, 43, 0.34)',
  emerald: '#1B6B5A',
  emeraldSoft: 'rgba(27, 107, 90, 0.10)',
  emeraldBorder: 'rgba(27, 107, 90, 0.32)',
  amber: '#B4691E',
  amberSoft: 'rgba(180, 105, 30, 0.12)',
  amberBorder: 'rgba(180, 105, 30, 0.38)',
  // Soft breathable slate for primary-source evidence inspection (never pitch black)
  slateBg: '#1F242D',
  slateSurface: '#272D38',
  slateElevated: '#303846',
  slateInk: '#EEF2F7',
  slateSecondary: '#B8C2D0',
  slateMuted: '#8B96A6',
  slateHairline: 'rgba(238, 242, 247, 0.14)',
  sans: '"Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", system-ui, sans-serif',
  serif: '"Noto Serif SC", "Source Han Serif SC", "Songti SC", "SimSun", Georgia, serif',
  mono: '"JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New", monospace',
} as const;

export const FontFaces: React.FC = () =>
  React.createElement(
    'style',
    {},
    `
@font-face {
  font-family: "Noto Sans SC";
  src: local("Noto Sans SC");
  font-weight: 100 900;
  font-display: block;
}
@font-face {
  font-family: "Noto Serif SC";
  src: local("Noto Serif SC"), local("Source Han Serif SC");
  font-weight: 100 900;
  font-display: block;
}
@font-face {
  font-family: "Microsoft YaHei UI";
  src: local("Microsoft YaHei UI"), local("Microsoft YaHei");
  font-weight: 100 900;
  font-display: block;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  background: ${theme.paper};
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
`,
  );
