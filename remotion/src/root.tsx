import React from 'react';
import {Composition} from 'remotion';
import {BriefingCover, BriefingCover43} from './cover';
import {COVER_43_WIDTH, DESIGN_HEIGHT, DESIGN_WIDTH} from './theme';
import {BriefingVideo, BriefingVideoProps, totalFrames} from './video';

const fallbackProps: BriefingVideoProps = {
  fps: 30,
  width: DESIGN_WIDTH,
  height: DESIGN_HEIGHT,
  slides: [
    {
      index: 0,
      start: 0,
      end: 6,
      duration: 6,
      kind: 'intro',
      activeTab: 'Intro',
      accent: 'C83E2B',
      title: 'AI 日报',
      caption: '每天几分钟，用一手证据讲清 AI 世界的新变化。',
      kicker: 'AI 日报',
      bottomTabs: ['开场', '收尾'],
      bottomActive: '开场',
      page: {
        kind: 'overview',
        title: '资讯概览',
        cards: [
          {title: '今日看点', body: '模型、工具、开源与产业一手核验动态'},
          {title: '来源优先', body: '优先采用官方发布、更新日志与原始页面证据'},
        ],
      },
    },
  ],
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="BriefingVideo"
        component={BriefingVideo}
        durationInFrames={totalFrames(fallbackProps)}
        fps={fallbackProps.fps}
        width={fallbackProps.width}
        height={fallbackProps.height}
        defaultProps={fallbackProps}
        calculateMetadata={({props}) => {
          const typed = props as BriefingVideoProps;
          return {
            durationInFrames: totalFrames(typed),
            fps: typed.fps || 30,
            width: typed.width || DESIGN_WIDTH,
            height: typed.height || DESIGN_HEIGHT,
          };
        }}
      />
      <Composition
        id="BriefingCover"
        component={BriefingCover}
        durationInFrames={1}
        fps={30}
        width={DESIGN_WIDTH}
        height={DESIGN_HEIGHT}
        defaultProps={fallbackProps}
        calculateMetadata={() => ({
          durationInFrames: 1,
          fps: 30,
          width: DESIGN_WIDTH,
          height: DESIGN_HEIGHT,
        })}
      />
      <Composition
        id="BriefingCover43"
        component={BriefingCover43}
        durationInFrames={1}
        fps={30}
        width={COVER_43_WIDTH}
        height={DESIGN_HEIGHT}
        defaultProps={fallbackProps}
        calculateMetadata={() => ({
          durationInFrames: 1,
          fps: 30,
          width: COVER_43_WIDTH,
          height: DESIGN_HEIGHT,
        })}
      />
    </>
  );
};
