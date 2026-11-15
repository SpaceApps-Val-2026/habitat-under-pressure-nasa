// The shape of the editorial pages' words. The words live in `content/*.{es,en}.ts` so the team can
// rewrite them without touching the layout; the screens only lay them out.

export type ContentSection = {
  heading: string;
  paragraphs: string[];
  /** A short list after the paragraphs, when the section needs one. */
  items?: string[];
};

export type AboutContent = {
  kicker: string;
  title: string;
  lead: string;
  sections: ContentSection[];
  /** Heading and lines around the generated parts (evidence levels, data classes, sources). */
  evidenceHeading: string;
  evidenceIntro: string;
  classesHeading: string;
  classesIntro: string;
  sourcesHeading: string;
  sourcesIntro: string;
  closing: ContentSection;
};

export type ManifestoContent = {
  kicker: string;
  title: string;
  lead: string;
  principles: { title: string; text: string }[];
  closing: string;
};
