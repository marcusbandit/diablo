import { useEffect, useState } from "react";
import { Icon } from "../ui/Icon";
import { CodeBlock } from "../ui/Markdown";
import { str } from "../lib/format";
import { arr, Card, useBlockCtx, type BlockProps, type Rec } from "./core";
import { diffLines, sandboxDoc } from "./pure";

export function Code({ block }: BlockProps) {
  return (
    <Card block={block} title={str(block.title) || (block.lang ? str(block.lang) : "Code")} tone="var(--t-code)" className="flush">
      <CodeBlock code={str(block.code)} lang={str(block.lang)} />
    </Card>
  );
}

export function Diff({ block }: BlockProps) {
  const lines = diffLines(str(block.diff));
  const added = lines.filter((l) => l.kind === "add").length;
  const removed = lines.filter((l) => l.kind === "del").length;
  return (
    <Card block={block} title={str(block.title) || "Diff"} tone="var(--t-git)" right={<span className="mono" title={`${added} lines added, ${removed} removed`}>{`+${added} -${removed}`}</span>}>
      <pre className="diff">
        {lines.map((l, i) => (
          <span key={i} className={`dl ${l.kind}`}>
            {l.text || " "}
            {"\n"}
          </span>
        ))}
      </pre>
    </Card>
  );
}

/** A stored file needs the token header, so it is fetched into a blob URL. */
function useFileUrl(fileId: string | null): { url: string | null; failed: boolean } {
  const { hub } = useBlockCtx();
  const [state, setState] = useState<{ url: string | null; failed: boolean }>({ url: null, failed: false });
  useEffect(() => {
    if (!fileId) return;
    let url: string | null = null;
    let live = true;
    const { uri, headers } = hub.file(fileId);
    fetch(uri, { headers })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((blob) => {
        url = URL.createObjectURL(blob);
        if (live) setState({ url, failed: false });
        else URL.revokeObjectURL(url);
      })
      .catch(() => live && setState({ url: null, failed: true }));
    return () => {
      live = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [hub, fileId]);
  return state;
}

export function Image({ block }: BlockProps) {
  const fileId = block.file ? str(block.file) : null;
  const remote = !fileId && /^https?:\/\//.test(str(block.url)) ? str(block.url) : null;
  const stored = useFileUrl(fileId);
  const src = remote ?? stored.url;
  const title = str(block.title) || str(block.name) || "Picture";
  return (
    <Card block={block} title={title} tone="var(--t-doc)" className="flush" right={src ? <a href={src} target="_blank" rel="noopener noreferrer" title="Open the picture in a new tab"><Icon name="open" size={14} /></a> : null}>
      {src ? (
        <a href={src} target="_blank" rel="noopener noreferrer" className="picture" title={str(block.caption) || title}>
          <img src={src} alt={str(block.caption) || title} loading="lazy" />
        </a>
      ) : (
        <div className="empty-line">{stored.failed ? "The hub did not send the picture" : "Loading the picture"}</div>
      )}
    </Card>
  );
}

export function Html({ block }: BlockProps) {
  const height = Math.max(80, Math.min(4000, Number(block.height) || 420));
  const doc = sandboxDoc(str(block.html), getComputedStyle(document.documentElement));
  const openTab = () => {
    const url = URL.createObjectURL(new Blob([doc], { type: "text/html" }));
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };
  return (
    <Card
      block={block}
      title={str(block.title) || "Page"}
      tone="var(--t-doc)"
      className="flush"
      right={
        <button type="button" className="ghost small" onClick={openTab} title="Open the page in a new tab, still without scripts">
          <Icon name="open" size={14} />
        </button>
      }
    >
      <iframe className="htmlframe" sandbox="" srcDoc={doc} title={str(block.title) || "Page"} style={{ height }} />
    </Card>
  );
}

export function Notice({ block }: BlockProps) {
  const { act } = useBlockCtx();
  const fail = block.tone === "fail";
  const [busy, setBusy] = useState(false);
  return (
    <Card block={block} tone={fail ? "var(--fail)" : block.tone === "warn" ? "var(--warn)" : "var(--faint)"} className={`notice ${fail ? "fail" : ""}`}>
      <div className="noticebody">
        <p>{str(block.text)}</p>
        {block.retry && !block.setup ? (
          <button
            type="button"
            className="button small"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              act({ module: str(block.module), action: "retry", args: {} }).finally(() => setBusy(false));
            }}
          >
            <Icon name="retry" size={14} /> {block.state ? "Check again" : "Try again"}
          </button>
        ) : null}
      </div>
    </Card>
  );
}

export function Actions({ block }: BlockProps) {
  const { act, say } = useBlockCtx();
  const items = arr<Rec>(block.items);
  return (
    <div className="chips">
      {items.map((item, i) => {
        const label = str(item.label);
        const run = () => {
          if (item.act && typeof item.act === "object") act(item.act as Rec);
          else if (item.say) say(str(item.say), item.send !== false);
        };
        const what = item.act ? "Runs it now" : item.send === false ? `Writes "${str(item.say)}"` : `Sends "${str(item.say)}"`;
        return (
          <button key={i} type="button" className="chip" onClick={run} title={what}>
            {item.icon ? <Icon name={str(item.icon)} size={14} /> : null}
            {label}
          </button>
        );
      })}
    </div>
  );
}

export function Question({ block }: BlockProps) {
  const { answer } = useBlockCtx();
  const options = arr<Rec>(block.options).slice(0, 4);
  const pending = (block.state ?? "pending") === "pending";
  const [picked, setPicked] = useState<number[]>([]);
  const [words, setWords] = useState("");
  const [sending, setSending] = useState(false);
  const multi = block.multi === true;
  const id = str(block.id);
  const send = async (choices: number[], text = "") => {
    setSending(true);
    const ok = await answer(id, { choices, text });
    setSending(false);
    if (ok) setPicked([]);
  };
  const ans = (block.answer ?? {}) as Rec;
  const outcome =
    block.state === "answered"
      ? [arr<unknown>(ans.labels).map(str).join(", "), str(ans.text)].filter(Boolean).join(": ")
      : block.state === "expired"
        ? "No answer in time"
        : block.state === "superseded"
          ? "Replaced by a newer question"
          : "Withdrawn";
  return (
    <Card block={block} title={str(block.header) || "Question"} tone="var(--accent)" right={pending ? <span className="live">waiting for you</span> : <span className="mono">{str(block.state)}</span>}>
      <div className="question">
        <p className="qtext">{str(block.question)}</p>
        {pending ? (
          <>
            <ol className="qopts">
              {options.map((o, i) => {
                const on = picked.includes(i);
                return (
                  <li key={i}>
                    <button
                      type="button"
                      className={`qopt ${on ? "on" : ""}`}
                      disabled={sending}
                      title={multi ? (on ? "Unpick" : "Pick") : `Answer ${str(o.label)}`}
                      onClick={() => (multi ? setPicked((p) => (on ? p.filter((x) => x !== i) : [...p, i])) : send([i]))}
                    >
                      <span className="qn">{i + 1}</span>
                      <span className="qlabel">
                        <b>{str(o.label)}</b>
                        {i === block.recommended ? <em>recommended</em> : null}
                        {o.description ? <span className="qdesc">{str(o.description)}</span> : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
            {multi || block.allow_text ? (
              <form
                className="qsend"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (picked.length || words.trim()) send(picked, words.trim());
                }}
              >
                {block.allow_text ? <input value={words} onChange={(e) => setWords(e.target.value)} placeholder="In your own words" disabled={sending} /> : null}
                <button type="submit" className="button" disabled={sending || (!picked.length && !words.trim())}>
                  Answer
                </button>
              </form>
            ) : null}
          </>
        ) : (
          <p className="qdone">{outcome}</p>
        )}
      </div>
    </Card>
  );
}

export function Git({ block }: BlockProps) {
  const commits = arr<Rec>(block.commits);
  return (
    <Card block={block} title={str(block.title) || "Commits"} tone="var(--t-git)">
      <ul className="rows">
        {commits.map((c, i) => (
          <li key={i} className="row static" title={`${str(c.kind)}: ${str(c.text)}`}>
            <span className={`kdot ${["add", "chg", "fix"].includes(str(c.kind)) ? str(c.kind) : "chg"}`} />
            <span className="rowtext">
              <span className="rowtitle">{str(c.text)}</span>
            </span>
            <span className="rowtrail">{[str(c.rtime), str(c.rstate)].filter(Boolean).join(", ")}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function Asks({ block }: BlockProps) {
  const { act } = useBlockCtx();
  const items = arr<Rec>(block.items);
  return (
    <Card block={block}>
      {items.length ? (
        <ul className="rows">
          {items.map((item, i) => (
            <li key={i} className="row static ask">
              <span className="rowtext">
                <span className="rowtitle">{str(item.title)}</span>
                {item.detail ? <span className="rowdetail">{str(item.detail)}</span> : null}
                <span className="rowdetail">{[str(item.from), str(item.when), str(item.status)].filter(Boolean).join(", ")}</span>
              </span>
              <span className="rowactions">
                {arr<Rec>(item.actions).map((a, j) => (
                  <button key={j} type="button" className={`button small ${a.primary ? "primary" : ""}`} onClick={() => act((a.act as Rec) ?? {})}>
                    {str(a.label)}
                  </button>
                ))}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="empty-line">{str(block.empty) || "Nothing waits for you"}</div>
      )}
    </Card>
  );
}
