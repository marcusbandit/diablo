import type { Block, HubEvent, Message, Section } from "@diablo/hub";
import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { BlockContext, Blocks, knownBlocks, type BlockCtx } from "../blocks";
import { clock, day, fullDate, sameDay } from "../lib/format";
import { errorText, useHub, useHubEvents } from "../lib/hub";
import { blocksOf, clientIdOf, mergeMessages, newClientId, panelTouched, patchQuestion, shown, stepsOf } from "../lib/messages";
import { useToast } from "../lib/toast";
import { Icon } from "../ui/Icon";
import { Markdown } from "../ui/Markdown";
import { Composer } from "./Composer";
import { turnWords, type Turn } from "./turns";

const PAGE = 60;
const OLDER = 40;
const SOURCE = "web";

type Pending = { clientId: string; text: string; ts: number; state: "sending" | "failed"; error?: string };

function Steps({ steps }: { steps: { step: string; tool: string }[] }) {
  if (!steps.length) return null;
  const tools = [...new Set(steps.map((s) => s.tool).filter(Boolean))];
  return (
    <details className="msgsteps">
      <summary title="The steps that made this reply">
        {steps.length === 1 ? "1 step" : `${steps.length} steps`}
        {tools.length ? `: ${tools.slice(0, 4).join(", ")}` : ""}
      </summary>
      <ol>
        {steps.map((s, i) => (
          <li key={i}>
            <span className="tool">{s.tool}</span> {s.step}
          </li>
        ))}
      </ol>
    </details>
  );
}

function Attachments({ meta }: { meta: Message["meta"] }) {
  const files = Array.isArray(meta?.attachments) ? (meta.attachments as { id: string; name: string; size?: number }[]) : [];
  if (!files.length) return null;
  return (
    <div className="attachments">
      {files.map((f) => (
        <span key={f.id} className="tag" title={`${f.name}${f.size ? `, ${Math.round(f.size / 1024)} KB` : ""}`}>
          {f.name}
        </span>
      ))}
    </div>
  );
}

function Entry({ message }: { message: Message }) {
  const blocks = blocksOf(message);
  const role = message.role === "user" || message.role === "assistant" ? message.role : "system";
  return (
    <article className={`msg ${role}`}>
      <time className="gutter" title={fullDate(message.ts)} dateTime={new Date(message.ts * 1000).toISOString()}>
        {clock(message.ts)}
      </time>
      <div className="msgbody">
        {role === "user" ? (
          <>
            <div className="you">{message.text}</div>
            <Attachments meta={message.meta} />
          </>
        ) : role === "assistant" ? (
          <>
            {message.text ? <Markdown source={message.text} /> : null}
            <Steps steps={stepsOf(message)} />
            {knownBlocks(blocks).length ? (
              <div className="results">
                <Blocks blocks={blocks} />
              </div>
            ) : null}
          </>
        ) : (
          <div className="sysline">{message.text}</div>
        )}
      </div>
    </article>
  );
}

function Working({ turn, onStop }: { turn: Turn; onStop: () => void }) {
  return (
    <div className="msg assistant working" aria-live="polite">
      <span className="gutter">{clock(turn.since)}</span>
      <div className="msgbody">
        <div className="workcard">
          <div className="workhead">
            <span className="pulse" />
            <span>{turnWords(turn)}</span>
            <button type="button" className="ghost small" onClick={onStop} title="Stop this turn">
              <Icon name="stop" size={14} /> Stop
            </button>
          </div>
          {turn.steps.length ? (
            <ol className="worksteps">
              {turn.steps.map((s, i) => (
                <li key={i} className={i === turn.steps.length - 1 ? "now" : ""}>
                  <span className="tool">{s.tool}</span> {s.step}
                </li>
              ))}
            </ol>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function SectionView({
  section,
  turn,
  sections,
  go,
}: {
  section: Section;
  turn: Turn | undefined;
  sections: Section[];
  go: (sectionId: string) => void;
}) {
  const { hub, link } = useHub();
  const toast = useToast();
  const id = section.id;
  const [messages, setMessages] = useState<Message[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [more, setMore] = useState(false);
  const [older, setOlder] = useState(false);
  const [pending, setPending] = useState<Pending[]>([]);
  const [panel, setPanel] = useState<Block[]>([]);
  const [showPanel, setShowPanel] = useState(false);
  const [draft, setDraft] = useState("");
  const [focusKey, setFocusKey] = useState(0);
  const stream = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const anchor = useRef<{ height: number; top: number } | null>(null);
  const stick = useRef(true);
  const lastTop = useRef(0);
  const current = useRef(id);
  const lastId = useRef(0);
  current.current = id;
  lastId.current = messages.length ? messages[messages.length - 1].id : 0;
  const hasPanel = (section.panels?.length ?? 0) > 0;

  useEffect(() => {
    setMessages([]);
    setPending([]);
    setLoaded(false);
    setMore(false);
    setPanel([]);
    setShowPanel(false);
    stick.current = true;
    hub.messages(id, { limit: PAGE }).then(
      (page) => {
        if (current.current !== id) return;
        setMessages(page);
        setMore(page.length >= PAGE);
        setLoaded(true);
      },
      (e) => {
        setLoaded(true);
        toast(errorText(e), "fail");
      },
    );
  }, [hub, id, toast]);

  const loadPanel = useCallback(() => {
    if (!hasPanel) return;
    hub.panel(id).then((blocks) => current.current === id && setPanel(blocks), () => {});
  }, [hub, id, hasPanel]);
  useEffect(loadPanel, [loadPanel]);

  const refreshNew = useCallback(async () => {
    const after = lastId.current;
    try {
      const fresh = await hub.messages(id, after ? { after } : { limit: PAGE });
      if (current.current !== id || !fresh.length) return;
      setMessages((have) => mergeMessages(have, fresh));
      const sent = new Set(fresh.map(clientIdOf).filter(Boolean));
      setPending((p) => p.filter((x) => !sent.has(x.clientId)));
    } catch {
      // the event stream brings it on the next change
    }
  }, [hub, id]);

  const wasLive = useRef(link === "live");
  useEffect(() => {
    if (link === "live" && !wasLive.current) refreshNew();
    wasLive.current = link === "live";
  }, [link, refreshNew]);

  useHubEvents((e: HubEvent) => {
    if (e.kind === "message" && e.section === id) refreshNew();
    else if (e.kind === "turn" && e.section === id && (e.state === "done" || e.state === "failed")) refreshNew();
    else if (e.kind === "question" && e.question && typeof e.question === "object") {
      const question = e.question as Block;
      const mid = typeof e.message === "number" ? e.message : undefined;
      setMessages((have) => patchQuestion(have, mid, question));
    } else if (e.kind === "panel" && panelTouched(section.panels, e.modules)) loadPanel();
  });

  const loadOlder = useCallback(async () => {
    if (older || !more || !messages.length) return;
    setOlder(true);
    try {
      const page = await hub.messages(id, { before: messages[0].id, limit: OLDER });
      if (current.current !== id) return;
      const el = stream.current;
      if (el) anchor.current = { height: el.scrollHeight, top: el.scrollTop };
      setMessages((have) => mergeMessages(page, have));
      setMore(page.length >= OLDER);
    } catch (e) {
      toast(errorText(e), "fail");
    } finally {
      setOlder(false);
    }
  }, [hub, id, messages, more, older, toast]);

  useLayoutEffect(() => {
    const el = stream.current;
    if (!el) return;
    if (anchor.current) {
      el.scrollTop = el.scrollHeight - anchor.current.height + anchor.current.top;
      anchor.current = null;
    } else if (stick.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, pending, turn, loaded]);

  // Pictures and pages grow after they load; stay at the bottom while he is there.
  useEffect(() => {
    const el = stream.current;
    const body = inner.current;
    if (!el || !body || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(() => {
      if (stick.current && !anchor.current) el.scrollTop = el.scrollHeight;
    });
    watch.observe(body);
    return () => watch.disconnect();
  }, []);

  const onScroll = () => {
    const el = stream.current;
    if (!el) return;
    // Only a scroll up lets go of the bottom: content that grows under a programmatic scroll
    // must not read as him scrolling away.
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (near) stick.current = true;
    else if (el.scrollTop < lastTop.current - 4) stick.current = false;
    lastTop.current = el.scrollTop;
    if (el.scrollTop < 400) loadOlder();
  };

  const send = useCallback(
    async (text: string, clientId = newClientId()) => {
      const body = text.trim();
      if (!body) return;
      stick.current = true;
      setPending((p) => [...p.filter((x) => x.clientId !== clientId), { clientId, text: body, ts: Date.now() / 1000, state: "sending" }]);
      try {
        await hub.chat(id, body, clientId, SOURCE);
        await refreshNew();
        setPending((p) => p.filter((x) => x.clientId !== clientId));
      } catch (e) {
        setPending((p) => p.map((x) => (x.clientId === clientId ? { ...x, state: "failed", error: errorText(e) } : x)));
      }
    },
    [hub, id, refreshNew],
  );

  const stop = useCallback(() => {
    hub.stopTurn(id).then(
      (stopped) => !stopped && toast("Nothing was running here"),
      (e) => toast(errorText(e), "fail"),
    );
  }, [hub, id, toast]);

  const ctx = useMemo<BlockCtx>(
    () => ({
      hub,
      section: id,
      act: async (body) => {
        try {
          const out = await hub.act(body, id);
          if (out.panel) setPanel(out.panel);
          if (out.message && out.message !== "done") toast(out.message);
          return true;
        } catch (e) {
          toast(errorText(e), "fail");
          return false;
        }
      },
      say: (text, now) => {
        if (now) send(text);
        else {
          setDraft(text);
          setFocusKey((k) => k + 1);
        }
      },
      open: (moduleId) => {
        const target = sections.find((s) => s.panels?.includes(moduleId) && s.id !== id) ?? sections.find((s) => s.panels?.includes(moduleId));
        if (target) go(target.id);
      },
      answer: async (qid, answer) => {
        stick.current = true;
        try {
          await hub.answerAndWait(qid, answer, newClientId(), SOURCE);
          await refreshNew();
          return true;
        } catch (e) {
          toast(errorText(e), "fail");
          return false;
        }
      },
    }),
    [hub, id, toast, send, sections, go, refreshNew],
  );

  const visible = shown(messages);
  const sent = new Set(messages.map(clientIdOf).filter(Boolean));
  const waiting = pending.filter((p) => !sent.has(p.clientId));

  return (
    <BlockContext.Provider value={ctx}>
      <div className={`sectionview ${hasPanel ? "has-panel" : ""} ${showPanel ? "panel-open" : ""}`} style={{ "--section": section.accent } as React.CSSProperties}>
        <header className="sectionhead">
          <span className="accent" />
          <h1 title={section.brief || section.name}>{section.name}</h1>
          {turn ? (
            <span className="headstate" title={turn.steps.map((s) => s.step).join("\n") || "Thinking"}>
              <span className="pulse" /> {turnWords(turn)}
            </span>
          ) : null}
          <span className="headright">
            {turn ? (
              <button type="button" className="button small" onClick={stop} title="Stop the turn running here">
                <Icon name="stop" size={14} /> Stop
              </button>
            ) : null}
            {hasPanel ? (
              <button
                type="button"
                className={`button small panelswitch ${showPanel ? "on" : ""}`}
                onClick={() => setShowPanel((s) => !s)}
                title={showPanel ? "Back to the conversation" : `Show the ${section.name} panel`}
              >
                {showPanel ? "Chat" : "Panel"}
              </button>
            ) : null}
          </span>
        </header>
        <div className="sectionbody">
          <div className="chatcol">
            <div className="stream" ref={stream} onScroll={onScroll}>
              <div className="streaminner" ref={inner}>
                {loaded && !more ? <div className="streamstart">The start of {section.name}</div> : null}
                {older ? <div className="streamstart">Reading older messages</div> : null}
                {visible.map((m, i) => {
                  const prev = visible[i - 1];
                  return (
                    <Fragment key={m.id}>
                      {!prev || !sameDay(prev.ts, m.ts) ? (
                        <div className="daysep">
                          <span title={fullDate(m.ts)}>{day(m.ts)}</span>
                        </div>
                      ) : null}
                      <Entry message={m} />
                    </Fragment>
                  );
                })}
                {waiting.map((p) => (
                  <article key={p.clientId} className={`msg user pending ${p.state}`}>
                    <span className="gutter">{clock(p.ts)}</span>
                    <div className="msgbody">
                      <div className="you">{p.text}</div>
                      {p.state === "failed" ? (
                        <div className="sendfail">
                          <span>{p.error}</span>
                          <button type="button" className="button small" onClick={() => send(p.text, p.clientId)}>
                            <Icon name="retry" size={14} /> Send again
                          </button>
                          <button type="button" className="ghost small" onClick={() => setPending((all) => all.filter((x) => x.clientId !== p.clientId))}>
                            Drop
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </article>
                ))}
                {turn ? <Working turn={turn} onStop={stop} /> : null}
                {loaded && !visible.length && !waiting.length && !turn ? (
                  <div className="emptystate">
                    <span className="accent" />
                    <h2>{section.name}</h2>
                  </div>
                ) : null}
              </div>
            </div>
            <Composer
              value={draft}
              onChange={setDraft}
              onSend={(text) => {
                setDraft("");
                send(text);
              }}
              running={!!turn}
              onStop={stop}
              placeholder={`Write to Diablo in ${section.name}`}
              focusKey={focusKey}
              sectionKey={id}
            />
          </div>
          {hasPanel ? (
            <aside className="panel" aria-label={`${section.name} panel`}>
              {panel.length ? <Blocks blocks={panel} /> : <div className="empty-line">Reading the panel</div>}
            </aside>
          ) : null}
        </div>
      </div>
    </BlockContext.Provider>
  );
}
