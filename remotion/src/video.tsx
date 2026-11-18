import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {TransitionSeries, linearTiming} from '@remotion/transitions';
import {fade} from '@remotion/transitions/fade';
import {wipe} from '@remotion/transitions/wipe';
import {ChapterTrack, Masthead} from './chrome';
import {mergeOutroIntoFinalStory, slideDurationFrames, totalFrames} from './layout';
import {EvidenceScene, OpeningScene, StoryScene, TickerScene} from './scenes';
import {DESIGN_HEIGHT, DESIGN_WIDTH, FontFaces, theme} from './theme';
import type {BriefingSlide, BriefingVideoProps, CoverCopy} from './types';

export {totalFrames};
export type {BriefingSlide, BriefingVideoProps, CoverCopy};

const STORY_WIPE_FRAMES = 12;
const PAGE_FADE_FRAMES = 8;

const isSameStoryContinuation = (current: BriefingSlide, next?: BriefingSlide): boolean => {
  if (!next || current.kind !== 'news' || next.kind !== 'news') return false;
  const currentKey = current.storyPosition || current.segmentIndex;
  const nextKey = next.storyPosition || next.segmentIndex;
  return Boolean(currentKey && currentKey === nextKey);
};

const SlideViewport: React.FC<{
  slide: BriefingSlide;
  allSlides: BriefingSlide[];
  duration: number;
  compositionFrame: number;
}> = ({slide, allSlides, duration, compositionFrame}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const scale = Math.min(width / DESIGN_WIDTH, height / DESIGN_HEIGHT);
  const isEvidence = slide.page?.kind === 'evidence';
  const isTicker = slide.kind === 'ticker' || slide.page?.kind === 'ticker';

  return (
    <AbsoluteFill style={{background: isEvidence ? theme.slateBg : theme.paper, overflow: 'hidden'}}>
      <FontFaces />
      <div
        style={{
          position: 'absolute',
          left: '50%',
          top: '50%',
          width: DESIGN_WIDTH,
          height: DESIGN_HEIGHT,
          transform: `translate(-50%, -50%) scale(${scale})`,
          transformOrigin: 'center center',
          fontFamily: theme.sans,
          color: isEvidence ? theme.slateInk : theme.ink,
          overflow: 'hidden',
        }}
      >
        <Masthead slide={slide} allSlides={allSlides} dark={isEvidence} />
        {slide.kind === 'intro' ? (
          <OpeningScene slide={slide} frame={frame} duration={duration} />
        ) : isTicker ? (
          <TickerScene slide={slide} frame={frame} duration={duration} />
        ) : isEvidence ? (
          <EvidenceScene slide={slide} frame={frame} duration={duration} />
        ) : (
          <StoryScene slide={slide} frame={frame} duration={duration} />
        )}
        <ChapterTrack slide={slide} compositionFrame={compositionFrame} fps={fps} dark={isEvidence} />
      </div>
    </AbsoluteFill>
  );
};

export const BriefingVideo: React.FC<BriefingVideoProps> = (props) => {
  const compositionFrame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const slides = mergeOutroIntoFinalStory(props.slides || []);

  return (
    <AbsoluteFill style={{background: theme.paper}}>
      <TransitionSeries>
        {slides.map((slide, index) => {
          const duration = slideDurationFrames(slide.duration, fps);
          const nextSlide = index < slides.length - 1 ? slides[index + 1] : undefined;
          const sameStory = isSameStoryContinuation(slide, nextSlide);
          const transitionFrames = nextSlide ? (sameStory ? PAGE_FADE_FRAMES : STORY_WIPE_FRAMES) : 0;

          return (
            <React.Fragment key={`${slide.index}-${index}`}>
              <TransitionSeries.Sequence durationInFrames={duration + transitionFrames}>
                <SlideViewport
                  slide={slide}
                  allSlides={slides}
                  duration={duration}
                  compositionFrame={compositionFrame}
                />
              </TransitionSeries.Sequence>
              {nextSlide ? (
                sameStory ? (
                  <TransitionSeries.Transition
                    timing={linearTiming({durationInFrames: transitionFrames})}
                    presentation={fade()}
                  />
                ) : (
                  <TransitionSeries.Transition
                    timing={linearTiming({durationInFrames: transitionFrames})}
                    presentation={wipe({direction: 'from-right'})}
                  />
                )
              ) : null}
            </React.Fragment>
          );
        })}
      </TransitionSeries>
    </AbsoluteFill>
  );
};
