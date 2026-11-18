export type BriefingCard = {
  icon?: string;
  title?: string;
  body?: string;
  meta?: string;
  accent?: string;
  component?: string;
};

export type EvidenceVisual = {
  kind?: string;
  source?: string;
  title?: string;
  url?: string;
  publishedAt?: string;
  published_at?: string;
  excerpt?: string;
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
  publishedAt?: string;
  published_at?: string;
  evidenceVisual?: EvidenceVisual | null;
  cards?: BriefingCard[];
  background_cards?: BriefingCard[];
};

export type TimelineRange = {
  start: number;
  end: number;
};

export type TimelineItem = {
  headline?: string;
  label: string;
  start: number;
  end: number;
  duration?: number;
  kind?: string;
  activeTab?: string;
  icon?: string;
  entity?: string;
};

export type BriefingSlide = {
  index: number;
  segmentIndex?: number;
  pageIndex?: number;
  storyPosition?: number;
  storyTotal?: number;
  runLabel?: string;
  start: number;
  end: number;
  duration: number;
  width?: number;
  height?: number;
  kind: string;
  activeTab: string;
  accent: string;
  title: string;
  caption: string;
  hook?: string;
  kicker?: string;
  editorialTier?: string;
  editorial_tier?: string;
  publishedAt?: string;
  published_at?: string;
  signalKind?: string;
  signal_kind?: string;
  disclaimer?: string;
  risk?: string;
  bottomTabs: string[];
  bottomActive: string;
  timelineItems?: TimelineItem[];
  timelineTotal?: number;
  page: BriefingPage;
  closingDuration?: number;
  closingText?: string;
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

export type BriefingVideoProps = {
  visualStyle?: string;
  renderLayers?: string;
  fps: number;
  width: number;
  height: number;
  slides: BriefingSlide[];
  cover?: CoverCopy;
};
