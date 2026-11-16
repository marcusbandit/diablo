import type { Block } from "@diablo/hub";
import { Actions, Asks, Code, Diff, Git, Html, Image, Notice, Question } from "./content";
import { Bars, Devices, Goal, List, Machines, Progress, Readout, Stats, Table } from "./data";
import { Lamps, Scenes } from "./lights";
import type { BlockView } from "./core";

export { BlockContext, type BlockCtx } from "./core";

/** Every kind this app draws. A kind missing here is skipped, so a new kind never breaks it. */
export const registry: Record<string, BlockView> = {
  actions: Actions,
  asks: Asks,
  bars: Bars,
  code: Code,
  devices: Devices,
  diff: Diff,
  git: Git,
  goal: Goal,
  html: Html,
  image: Image,
  lamps: Lamps,
  list: List,
  machines: Machines,
  notice: Notice,
  progress: Progress,
  question: Question,
  readout: Readout,
  scenes: Scenes,
  stats: Stats,
  table: Table,
};

export function knownBlocks(blocks: Block[]): Block[] {
  return blocks.filter((b) => b && typeof b.kind === "string" && b.kind in registry);
}

export function Blocks({ blocks }: { blocks: Block[] }) {
  return (
    <>
      {knownBlocks(blocks).map((block, i) => {
        const View = registry[block.kind];
        return <View key={`${block.kind}-${String(block.id ?? i)}`} block={block} />;
      })}
    </>
  );
}
