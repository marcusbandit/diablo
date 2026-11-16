import { useState } from "react";
import { Icon } from "../ui/Icon";
import { num, str } from "../lib/format";
import { arr, Card, useBlockCtx, type BlockProps, type Rec } from "./core";
import { barLayout } from "./bars";

export function Stats({ block }: BlockProps) {
  const { open } = useBlockCtx();
  const items = arr<Rec>(block.items);
  if (!items.length) return null;
  const plain = !block.title && !block.caption;
  return (
    <Card block={plain ? { ...block, bare: true } : block} className="flush">
      <div className="stats">
        {items.map((item, i) => {
          const value = `${str(item.value)}${item.unit ? ` ${str(item.unit)}` : ""}`;
          const label = str(item.label);
          const inner = (
            <>
              <span className="statlabel">
                {item.icon ? <Icon name={str(item.icon)} size={14} /> : null}
                {label}
              </span>
              <span className="statvalue">{str(item.value)}</span>
              {item.unit ? <span className="statunit">{str(item.unit)}</span> : null}
            </>
          );
          return item.open ? (
            <button key={i} type="button" className="stat link" title={`${label}: ${value}. Open ${str(item.open)}`} onClick={() => open(str(item.open))}>
              {inner}
            </button>
          ) : (
            <div key={i} className="stat" title={`${label}: ${value}`}>
              {inner}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

export function Progress({ block }: BlockProps) {
  const value = num(block.value);
  const max = num(block.max) || 1;
  const share = Math.max(0, Math.min(1, value / max));
  return (
    <Card block={block} title={str(block.label)} right={<span className="mono">{`${value} / ${max}`}</span>}>
      <div className="meter" title={`${value} of ${max}, ${Math.round(share * 100)}%`}>
        <span style={{ width: `${share * 100}%` }} />
      </div>
    </Card>
  );
}

export function Goal({ block }: BlockProps) {
  const value = num(block.value);
  const max = num(block.max) || 1;
  const share = Math.max(0, Math.min(1, value / max));
  const r = 42;
  const c = 2 * Math.PI * r;
  const badges = arr<Rec>(block.badges);
  return (
    <Card block={block} className={block.stale ? "stale" : ""}>
      <div className="goal">
        <svg viewBox="0 0 100 100" className="ring" role="img" aria-label={`${value} of ${max}`}>
          <title>{`${value} of ${max}, ${Math.round(share * 100)}%`}</title>
          <circle cx="50" cy="50" r={r} className="track" />
          <circle cx="50" cy="50" r={r} className="fill" strokeDasharray={`${c * share} ${c}`} transform="rotate(-90 50 50)" />
          <text x="50" y="48" className="ringvalue">
            {value}
          </text>
          <text x="50" y="64" className="ringmax">
            of {max}
          </text>
        </svg>
        <ul className="badges">
          {badges.map((b, i) => (
            <li key={i} title={str(b.label)}>
              <Icon name={str(b.icon)} size={14} />
              {str(b.label)}
            </li>
          ))}
        </ul>
      </div>
    </Card>
  );
}

export function Bars({ block }: BlockProps) {
  const labels = arr<unknown>(block.labels).map(str);
  const series = arr<Rec>(block.series).map((s) => ({
    name: str(s.name),
    color: s.color ? str(s.color) : undefined,
    tone: s.tone ? str(s.tone) : undefined,
    values: arr<unknown>(s.values).map(num),
  }));
  const [hover, setHover] = useState<number | null>(null);
  const layout = barLayout(labels.length, series.map((s) => s.values), block.stacked === true);
  const unit = str(block.unit);
  if (!labels.length || layout.max === 0) {
    return (
      <Card block={block}>
        <div className="empty-line">{str(block.empty) || "Nothing here yet"}</div>
      </Card>
    );
  }
  const colour = (i: number) => {
    const s = series[i];
    if (s.tone) return `var(--${s.tone === "accent" ? "accent" : s.tone})`;
    return s.color ?? ["var(--accent)", "var(--ok)", "var(--warn)", "var(--t-code)"][i % 4];
  };
  return (
    <Card
      block={block}
      right={
        series.length > 1 ? (
          <span className="legend">
            {series.map((s, i) => (
              <span key={i}>
                <i style={{ background: colour(i) }} />
                {s.name}
              </span>
            ))}
          </span>
        ) : null
      }
    >
      <div className="bars" onMouseLeave={() => setHover(null)}>
        {labels.map((label, li) => {
          const tip = `${label}: ${series.map((s) => `${s.name ? s.name + " " : ""}${s.values[li] ?? 0}${unit ? " " + unit + ((s.values[li] ?? 0) === 1 ? "" : "s") : ""}`).join(", ")}`;
          return (
            <div key={li} className={`barcol ${hover === li ? "on" : ""}`} title={tip} onMouseEnter={() => setHover(li)}>
              <div className={`barstack ${block.stacked ? "stacked" : "grouped"}`}>
                {layout.columns[li].map((seg, si) => (
                  <span key={si} style={{ height: `${seg.height * 100}%`, bottom: `${seg.bottom * 100}%`, background: colour(si) }} />
                ))}
              </div>
              <span className="barlabel">{label}</span>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

export function Readout({ block }: BlockProps) {
  const cells = arr<Rec>(block.cells);
  return (
    <Card block={block} className="flush">
      <div className="readout">
        {cells.map((c, i) => (
          <div key={i} className="cell" title={`${str(c.k)}: ${str(c.v)}`}>
            <span className="v">{str(c.v)}</span>
            <span className="k">{str(c.k)}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function Table({ block }: BlockProps) {
  const head = arr<unknown>(block.head).map(str);
  const rows = arr<unknown[]>(block.rows);
  return (
    <Card block={block} className="flush">
      <div className="tablewrap">
        <table>
          {head.length ? (
            <thead>
              <tr>
                {head.map((h, i) => (
                  <th key={i}>{h}</th>
                ))}
              </tr>
            </thead>
          ) : null}
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                {(Array.isArray(r) ? r : []).map((c, j) => (
                  <td key={j} title={head[j] ? `${head[j]}: ${str(c)}` : undefined}>
                    {str(c)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function ListRow({ item }: { item: Rec }) {
  const { say, act } = useBlockCtx();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState<number | null>(null);
  const facts = arr<Rec>(item.facts);
  const actions = arr<Rec>(item.actions);
  const expands = !!item.url || facts.length > 0;
  const title = str(item.title || item.subject);
  const detail = str(item.detail || item.summary);
  const lines = arr<unknown>(item.lines).map(str);
  const onClick = () => {
    if (expands) setOpen((o) => !o);
    else if (title) say(title, false);
  };
  return (
    <li className={`row ${item.important ? "important" : ""} ${open ? "open" : ""}`}>
      <button type="button" className="rowmain" onClick={onClick} title={expands ? (open ? "Fold" : "Show more") : `Write about ${title}`}>
        <span className="rowtext">
          <span className="rowtitle">{title}</span>
          {detail ? <span className="rowdetail">{detail}</span> : null}
          {lines.map((l, i) => (
            <span key={i} className="rowdetail">
              {l}
            </span>
          ))}
        </span>
        {item.trailing ? <span className="rowtrail">{str(item.trailing)}</span> : null}
      </button>
      {open ? (
        <div className="rowmore">
          {facts.length ? (
            <dl>
              {facts.map((f, i) => (
                <div key={i}>
                  <dt>{str(f.label)}</dt>
                  <dd>{str(f.value)}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          <div className="buttons">
            {item.url ? (
              <a className="button" href={str(item.url)} target="_blank" rel="noopener noreferrer" title={str(item.url)}>
                <Icon name="open" size={14} /> Open
              </a>
            ) : null}
            {item.reply ? (
              <button type="button" className="button" onClick={() => say(str(item.reply), false)}>
                Reply
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
      {actions.length ? (
        <div className="rowactions">
          {actions.map((a, i) =>
            confirm === i ? (
              <span key={i} className="confirm">
                <button type="button" className="button danger" onClick={() => act((a.act as Rec) ?? {}).then(() => setConfirm(null))}>
                  {str(a.label)}
                </button>
                <button type="button" className="button" onClick={() => setConfirm(null)}>
                  Keep
                </button>
              </span>
            ) : (
              <button
                key={i}
                type="button"
                className={`button small ${a.danger ? "danger-soft" : ""}`}
                onClick={() => (a.danger ? setConfirm(i) : act((a.act as Rec) ?? {}))}
              >
                {str(a.label)}
              </button>
            ),
          )}
        </div>
      ) : null}
    </li>
  );
}

export function List({ block }: BlockProps) {
  const items = arr<Rec>(block.items);
  return (
    <Card block={block} right={items.length ? <span className="mono">{items.length}</span> : null}>
      {items.length ? (
        <ul className="rows">
          {items.map((item, i) => (
            <ListRow key={i} item={item} />
          ))}
        </ul>
      ) : (
        <div className="empty-line">{str(block.empty) || "Nothing here"}</div>
      )}
    </Card>
  );
}

export function Devices({ block }: BlockProps) {
  const items = arr<Rec>(block.items);
  return (
    <Card block={block} right={<span className="mono">{`${items.filter((d) => d.online).length} of ${items.length} online`}</span>}>
      <ul className="rows">
        {items.map((d, i) => (
          <li key={i} className="row static" title={`${str(d.name)}, ${str(d.os)}, ${d.online ? str(d.how) : "last seen " + str(d.last_seen)}`}>
            <span className={`dot ${d.online ? "ok" : ""}`} />
            <Icon name={str(d.kind)} size={14} />
            <span className="rowtext">
              <span className="rowtitle">{str(d.name)}</span>
              <span className="rowdetail">{str(d.os)}</span>
            </span>
            <span className="rowtrail">{d.online ? str(d.how) : str(d.last_seen)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function Machines({ block }: BlockProps) {
  const items = arr<Rec>(block.items);
  return (
    <Card block={block}>
      <ul className="rows">
        {items.map((m, i) => {
          const mem = m.mem_total ? `${num(m.mem_used).toFixed(1)} of ${num(m.mem_total).toFixed(0)} GB` : "";
          const warnings = arr<unknown>(m.warnings).map(str);
          return (
            <li key={i} className="row static" title={[str(m.name), m.online ? `up ${str(m.uptime)}` : "offline", mem, ...warnings].filter(Boolean).join(", ")}>
              <span className={`dot ${m.online ? "ok" : ""}`} />
              <span className="rowtext">
                <span className="rowtitle">{str(m.name)}</span>
                <span className="rowdetail">{m.online ? [`load ${m.load ?? "?"}`, mem, m.disk_used_pct != null ? `disk ${num(m.disk_used_pct)}%` : ""].filter(Boolean).join(", ") : "offline"}</span>
                {warnings.map((w, j) => (
                  <span key={j} className="rowdetail warn">
                    {w}
                  </span>
                ))}
              </span>
              <span className="rowtrail">{str(m.uptime)}</span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
