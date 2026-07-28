import React from "react";

export const theme = {
  bg: "#f3f8f7",
  panel: "#ffffff",
  panel2: "#e8f5f3",
  text: "#10272c",
  muted: "#526c70",
  cyan: "#00a98f",
  electric: "#178ed1",
  orange: "#ff6f45",
  yellow: "#e6b93f",
  line: "rgba(19, 91, 91, 0.18)",
  sans: '"Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", DengXian, sans-serif',
  display: '"Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif',
  serif: '"Noto Serif SC", "Songti SC", SimSun, serif',
};

export const FontFaces: React.FC = () =>
  React.createElement(
    "style",
    {},
    `
@font-face { font-family: "Noto Sans SC"; src: local("Noto Sans SC"); font-weight: 100 900; font-display: block; }
@font-face { font-family: "Noto Serif SC"; src: local("Noto Serif SC"); font-weight: 100 900; font-display: block; }
* { box-sizing: border-box; }
body { margin: 0; background: #f3f8f7; }
`,
  );
