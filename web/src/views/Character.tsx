import type { Character as CharacterData } from "@diablo/hub";
import { useEffect, useState } from "react";
import { fullDate } from "../lib/format";
import { errorText, useHub } from "../lib/hub";
import { routeHash } from "../lib/route";
import { useToast } from "../lib/toast";

/** Marcus's own words on how Diablo speaks. Reached only from the quiet link under Devices; voice can never change it. */
export function Character() {
  const { hub } = useHub();
  const toast = useToast();
  const [data, setData] = useState<CharacterData | null>(null);
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    hub.character().then(
      (d) => {
        setData(d);
        setText(d.text);
      },
      (e) => toast(`The hub did not answer: ${errorText(e)}`, "fail"),
    );
  }, [hub, toast]);

  const save = async () => {
    setSaving(true);
    try {
      const d = await hub.saveCharacter(text);
      setData(d);
      setText(d.text);
      toast(d.text ? "Saved. He speaks this way from his next turn." : "Saved. He is the plain butler again.");
    } catch (e) {
      toast(`Not saved: ${errorText(e)}`, "fail");
    } finally {
      setSaving(false);
    }
  };

  const refused = data?.refused ?? [];
  return (
    <div className="page">
      <header className="pagehead">
        <h1>Character</h1>
      </header>
      <section className="pagesection">
        <textarea
          className="charbox"
          spellCheck={false}
          value={text}
          placeholder={data?.default ?? ""}
          onChange={(e) => setText(e.target.value)}
          disabled={!data}
        />
        <div className="buttons">
          <button type="button" className="button primary" onClick={save} disabled={!data || saving || text === data.text}>
            Save
          </button>
          <a className="button" href={routeHash({ view: "devices" })}>
            Devices
          </a>
        </div>
      </section>
      {refused.length ? (
        <section className="pagesection">
          <h2>Refused by voice</h2>
          <ul className="devices">
            {refused.map((r, i) => (
              <li key={i} className="device" title={r.ts ? fullDate(r.ts) : undefined}>
                <span>{r.said || r.tool}</span>
                {r.ts ? <span className="mono faint">{fullDate(r.ts)}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
