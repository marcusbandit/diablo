import { useEffect, useState } from "react";
import { num, str } from "../lib/format";
import { arr, Card, useBlockCtx, type BlockProps, type Rec } from "./core";

function Lamp({ lamp }: { lamp: Rec }) {
  const { act } = useBlockCtx();
  const id = str(lamp.id);
  const name = str(lamp.name);
  const on = lamp.on === true;
  const reachable = lamp.reachable !== false;
  const caps = (lamp.caps ?? {}) as Rec;
  const ct = Array.isArray(caps.ct) ? (caps.ct as number[]) : null;
  const [level, setLevel] = useState(num(lamp.level));
  const [kelvin, setKelvin] = useState(num(lamp.ct) || (ct ? Math.round((ct[0] + ct[1]) / 2) : 0));
  useEffect(() => setLevel(num(lamp.level)), [lamp.level]);
  useEffect(() => {
    if (lamp.ct != null) setKelvin(num(lamp.ct));
  }, [lamp.ct]);
  const tap = (action: string, args: Rec) => act({ module: "lights", action, args: { lamp: id, ...args } });
  const color = typeof lamp.color === "string" && /^#[0-9a-f]{6}$/i.test(lamp.color) ? lamp.color : "#ffd166";
  return (
    <li className={`lamp ${on ? "on" : ""} ${reachable ? "" : "gone"}`} style={{ "--lamp": color } as React.CSSProperties}>
      <button
        type="button"
        className="switch"
        role="switch"
        aria-checked={on}
        disabled={!reachable}
        title={reachable ? `${name}: ${on ? `on at ${level}%` : "off"}. Turn it ${on ? "off" : "on"}` : `${name} cannot be reached`}
        onClick={() => tap("toggle", {})}
      >
        <span className="bulb" />
        <span className="lampname">{name}</span>
        <span className="lampstate">{reachable ? (on ? `${level}%` : "off") : "unreachable"}</span>
      </button>
      {reachable ? (
        <div className="lampcontrols">
          <input
            type="range"
            min={0}
            max={100}
            value={level}
            title={`Brightness ${level}%`}
            aria-label={`${name} brightness`}
            onChange={(e) => setLevel(Number(e.target.value))}
            onPointerUp={() => tap("level", { level })}
            onKeyUp={() => tap("level", { level })}
          />
          {ct ? (
            <input
              type="range"
              className="ct"
              min={ct[0]}
              max={ct[1]}
              step={50}
              value={kelvin}
              title={`White ${kelvin} K`}
              aria-label={`${name} white temperature`}
              onChange={(e) => setKelvin(Number(e.target.value))}
              onPointerUp={() => tap("temperature", { kelvin })}
              onKeyUp={() => tap("temperature", { kelvin })}
            />
          ) : null}
          {caps.color === true ? (
            <input type="color" value={color} title={`Colour ${color}`} aria-label={`${name} colour`} onChange={(e) => tap("color", { hex: e.target.value })} />
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

export function Lamps({ block }: BlockProps) {
  const lamps = arr<Rec>(block.lamps);
  const on = lamps.filter((l) => l.on).length;
  return (
    <Card block={block} title={str(block.title) || "Lamps"} tone="var(--t-lights)" right={<span className="mono">{`${on} of ${lamps.length} on`}</span>}>
      <ul className="lamps">
        {lamps.map((lamp) => (
          <Lamp key={str(lamp.id)} lamp={lamp} />
        ))}
      </ul>
    </Card>
  );
}

export function Scenes({ block }: BlockProps) {
  const { act } = useBlockCtx();
  const items = arr<Rec>(block.items);
  return (
    <Card block={block} title={str(block.title) || "Scenes"} tone="var(--t-lights)">
      <div className="chips">
        {items.map((s) => {
          const colors = arr<unknown>(s.colors).map(str).slice(0, 4);
          return (
            <button key={str(s.id)} type="button" className="chip" title={`Set the scene ${str(s.name)}`} onClick={() => act({ module: "lights", action: "scene", args: { id: s.id } })}>
              {colors.length ? (
                <span className="swatch">
                  {colors.map((c, i) => (
                    <i key={i} style={{ background: c }} />
                  ))}
                </span>
              ) : null}
              {str(s.name)}
            </button>
          );
        })}
      </div>
    </Card>
  );
}
