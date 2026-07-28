import React from 'react';
import {Composition} from 'remotion';
import {BriefingVideo, BriefingVideoProps, totalFrames} from './video';
import {BriefingCover, BriefingCover43} from './cover';

const fallbackProps: BriefingVideoProps = {
  fps: 30,
  width: 1920,
  height: 1080,
  slides: [
    {
      index: 0,
      start: 0,
      end: 6,
      duration: 6,
      kind: 'intro',
      activeTab: 'Intro',
      accent: '4E7C68',
      title: 'AI 日报',
      caption: '正在生成今日 AI 动态',
      kicker: 'AI 日报',
      bottomTabs: ['开场', '收尾'],
      bottomActive: '开场',
      page: {
        kind: 'overview',
        title: '资讯概览',
        cards: [
          {title: '今日看点', body: '模型、工具、开源与产业动态'},
          {title: '来源优先', body: '优先采用官方发布、更新日志和可查页面'}
        ]
      }
    }
  ]
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
          return {
            durationInFrames: totalFrames(props as BriefingVideoProps),
            fps: (props as BriefingVideoProps).fps || 30,
            width: (props as BriefingVideoProps).width || 1920,
            height: (props as BriefingVideoProps).height || 1080
          };
        }}
      />
      <Composition
        id="BriefingCover"
        component={BriefingCover}
        durationInFrames={1}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={fallbackProps}
        calculateMetadata={() => ({durationInFrames: 1, fps: 30, width: 1920, height: 1080})}
      />
      <Composition
        id="BriefingCover43"
        component={BriefingCover43}
        durationInFrames={1}
        fps={30}
        width={1440}
        height={1080}
        defaultProps={fallbackProps}
        calculateMetadata={() => ({durationInFrames: 1, fps: 30, width: 1440, height: 1080})}
      />
    </>
  );
};
