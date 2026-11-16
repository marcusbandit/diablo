import type { Block, Hub } from "@diablo/hub";
import { createContext, useContext, type ReactNode } from "react";
import { Icon } from "../ui/Icon";
import { str } from "../lib/format";

export type Answer = { choices?: number[]; text?: string };

export type BlockCtx = {
  hub: Hub;
  section: string;
  /** Runs a tap through POST /v1/act. Resolves false when it failed (already shown). */
  act: (body: Record<string, unknown>) => Promise<boolean>;
  /** Fills the composer, or sends the text at once. */
  say: (text: string, send: boolean) => void;
  /** Opens the section that shows a module. */
  open: (moduleId: string) => void;
  answer: (id: string, answer: Answer) => Promise<boolean>;
};

export const BlockContext = createContext<BlockCtx | null>(null);

export function useBlockCtx(): BlockCtx {
  const ctx = useContext(BlockContext);
  if (!ctx) throw new Error("block outside BlockContext");
  return ctx;
}

export type BlockProps = { block: Block };
export type BlockView = (props: BlockProps) => ReactNode;

/** The frame most blocks sit in: a bar with the kind's colour mark and title, then the body. */
export function Card({
  block,
  title,
  icon,
  tone,
  right,
  children,
  className = "",
}: {
  block: Block;
  title?: string;
  icon?: string;
  tone?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const heading = title ?? str(block.title);
  const bare = block.bare === true;
  return (
    <section className={`card kind-${block.kind} ${bare ? "bare" : ""} ${className}`} style={tone ? ({ "--tc": tone } as React.CSSProperties) : undefined}>
      {heading || right ? (
        <header className="cardbar">
          <span className="mark" />
          {icon || block.icon ? <Icon name={icon ?? str(block.icon)} size={14} /> : null}
          <span className="cardtitle">{heading}</span>
          {right ? <span className="cardright">{right}</span> : null}
        </header>
      ) : null}
      <div className="cardbody">{children}</div>
      {block.caption ? <footer className="cardcaption">{str(block.caption)}</footer> : null}
    </section>
  );
}

export function arr<T = Record<string, unknown>>(value: unknown): T[] {
  return Array.isArray(value) ? (value.filter((x) => x != null) as T[]) : [];
}

export type Rec = Record<string, unknown>;
