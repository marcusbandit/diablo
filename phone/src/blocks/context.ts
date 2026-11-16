import { createContext, useContext } from "react";

/** What a block can do when tapped. The screens provide it; a block outside them only shows. */
export type BlockActions = {
  say: (text: string, send: boolean) => void;
  act: (act: Record<string, unknown>) => void;
  answer: (question: string, answer: { choices?: number[]; text?: string }) => void;
  file: (id: string) => { uri: string; headers: Record<string, string> } | null;
  open: (module: string) => void;
};

const none: BlockActions = {
  say: () => undefined,
  act: () => undefined,
  answer: () => undefined,
  file: () => null,
  open: () => undefined,
};

export const BlockActionsContext = createContext<BlockActions>(none);

export const useBlockActions = () => useContext(BlockActionsContext);
