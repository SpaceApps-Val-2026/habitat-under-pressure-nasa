import type { ComponentType } from "react";

import { EvidenceBadge } from "./EvidenceBadge";
import { Hypotheses } from "./Hypotheses";
import { KeyFigure } from "./KeyFigure";
import { LimitsBox } from "./LimitsBox";
import { MethodBox } from "./MethodBox";
import { NormCard } from "./NormCard";
import { ScenarioFan } from "./ScenarioFan";
import { SourcesTable } from "./SourcesTable";
import { TrendChart } from "./TrendChart";
import { UncertaintySplit } from "./UncertaintySplit";
import { BeforeAfterMap } from "./BeforeAfterMap";
import { ExposureCounter } from "./ExposureCounter";
import { RingsDecay } from "./RingsDecay";
import { ScanMatrix } from "./ScanMatrix";
import type { BlockProps, StoryBlock } from "./shared";

const REGISTRY: Record<string, ComponentType<BlockProps>> = {
  KeyFigure,
  EvidenceBadge,
  Hypotheses,
  SourcesTable,
  LimitsBox,
  MethodBox,
  TrendChart,
  NormCard,
  ScenarioFan,
  UncertaintySplit,
  BeforeAfterMap,
  RingsDecay,
  ScanMatrix,
  ExposureCounter,
};

// Blocks that read as a figure in the article (a rule above, no card); the rest are prose or boxes.
export const FIGURES = new Set(["TrendChart", "ScenarioFan", "BeforeAfterMap", "RingsDecay", "ScanMatrix", "ExposureCounter"]);

export function Block(props: BlockProps) {
  if (!Object.hasOwn(REGISTRY, props.block.block)) {
    // An unknown block is skipped with a warning; the rest of the report still reads.
    console.warn(`Unknown report block: ${props.block.block}`);
    return null;
  }
  const Component = REGISTRY[props.block.block];
  return <Component {...props} />;
}

export function ReportBlocks({
  blocks,
  results,
  findings,
  style,
  language,
  catalogSources,
  accessed,
  expanded,
}: {
  blocks: StoryBlock[];
  results: BlockProps["results"];
  findings: BlockProps["findings"];
  style: string;
  language: string;
  catalogSources?: BlockProps["catalogSources"];
  accessed?: string;
  expanded?: boolean;
}) {
  return (
    <>
      {blocks.map((block, index) => (
        <div
          key={`${block.block}-${index}`}
          className="report-block"
          data-block={block.block}
          data-figure={FIGURES.has(block.block) ? "true" : undefined}
        >
          <Block
            block={block}
            results={results}
            findings={findings}
            style={style}
            language={language}
            catalogSources={catalogSources}
            accessed={accessed}
            expanded={expanded}
          />
        </div>
      ))}
    </>
  );
}
