import type { HubEvent, Section } from "@diablo/hub";
import { useCallback, useEffect, useState } from "react";
import { errorText, useHub, useHubEvents, type Link } from "../lib/hub";
import { parseRoute, routeHash, type Route } from "../lib/route";
import { useToast } from "../lib/toast";
import { Brand } from "../ui/Brand";
import { Icon } from "../ui/Icon";
import { Character } from "./Character";
import { Devices } from "./Devices";
import { IntroAgain } from "./Onboarding";
import { SectionView } from "./Section";
import { SpeakingSwitch } from "./Speaking";
import { applyTurnEvent, turnWords, type Turns } from "./turns";

const MAIN = "diablo";

function useRoute(): [Route, (r: Route) => void] {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash, MAIN));
  useEffect(() => {
    const on = () => setRoute(parseRoute(window.location.hash, MAIN));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = useCallback((r: Route) => {
    const hash = routeHash(r);
    if (window.location.hash !== hash) window.location.hash = hash;
    else setRoute(r);
  }, []);
  return [route, go];
}

const LINK_WORDS: Record<Link, string> = { live: "live", connecting: "reaching the hub", down: "the hub is out of reach" };

export function Shell() {
  const { hub, link } = useHub();
  const toast = useToast();
  const [route, go] = useRoute();
  const [sections, setSections] = useState<Section[] | null>(null);
  const [turns, setTurns] = useState<Turns>({});
  const [unread, setUnread] = useState<Record<string, boolean>>({});
  const [drawer, setDrawer] = useState(false);
  const [hubName, setHubName] = useState("");

  const loadSections = useCallback(() => {
    hub.sections().then(
      (all) => setSections(all.filter((s) => !s.deleted && !s.hidden).sort((a, b) => a.position - b.position || a.id.localeCompare(b.id))),
      (e) => {
        setSections((s) => s ?? []);
        toast(errorText(e), "fail");
      },
    );
  }, [hub, toast]);

  useEffect(() => {
    loadSections();
    hub.whoami().then((me) => setHubName(me.hub), () => {});
  }, [hub, loadSections]);

  const openId = route.view === "section" ? route.id : null;

  useHubEvents((e: HubEvent) => {
    if (e.kind === "sections") loadSections();
    if (e.kind === "turn") setTurns((t) => applyTurnEvent(t, e, Date.now() / 1000));
    if (e.kind === "message" && typeof e.section === "string" && e.section !== openId) {
      const s = e.section;
      setUnread((u) => (u[s] ? u : { ...u, [s]: true }));
    }
  });

  useEffect(() => {
    if (openId) setUnread((u) => (u[openId] ? { ...u, [openId]: false } : u));
    setDrawer(false);
  }, [openId, route.view]);

  useEffect(() => {
    if (!drawer) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawer(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer]);

  const section = openId ? sections?.find((s) => s.id === openId) : undefined;
  useEffect(() => {
    document.title = route.view === "devices" ? "Devices, Diablo" : route.view === "character" ? "Character, Diablo" : section ? `${section.name}, Diablo` : "Diablo";
  }, [route.view, section]);

  const goSection = useCallback((id: string) => go({ view: "section", id }), [go]);

  let main;
  if (route.view === "devices") main = <Devices />;
  else if (route.view === "character") main = <Character />;
  else if (route.view === "intro") main = <IntroAgain onClose={() => goSection(MAIN)} />;
  else if (!sections) main = <div className="page"><div className="empty-line">Reaching the hub</div></div>;
  else if (!section)
    main = (
      <div className="page">
        <h1>No such section</h1>
        <div className="buttons">
          <button type="button" className="button" onClick={() => goSection(MAIN)}>
            Open Diablo
          </button>
        </div>
      </div>
    );
  else main = <SectionView key={section.id} section={section} turn={turns[section.id]} sections={sections} go={goSection} />;

  const title = route.view === "devices" ? "Devices" : route.view === "character" ? "Character" : section?.name ?? "Diablo";

  return (
    <div className={`shell ${drawer ? "drawer-open" : ""}`}>
      <header className="topbar">
        <button type="button" className="ghost" onClick={() => setDrawer(true)} title="Sections and settings" aria-label="Open the menu">
          <Icon name="menu" size={20} />
        </button>
        <span className="toptitle" style={section ? ({ "--section": section.accent } as React.CSSProperties) : undefined}>
          {section ? <span className="accent" /> : null}
          {title}
        </span>
        <span className={`linkdot ${link}`} title={LINK_WORDS[link]} />
      </header>
      <div className="scrim" onClick={() => setDrawer(false)} />
      <nav className="sidebar" aria-label="Sections">
        <div className="sidehead">
          <a href={routeHash({ view: "section", id: MAIN })} title="Diablo">
            <Brand />
          </a>
          <button type="button" className="ghost drawerclose" onClick={() => setDrawer(false)} aria-label="Close the menu" title="Close">
            <Icon name="close" />
          </button>
        </div>
        <div className="sidegroup">Sections</div>
        <ul className="sections">
          {(sections ?? []).map((s) => {
            const turn = turns[s.id];
            const on = openId === s.id;
            return (
              <li key={s.id}>
                <a
                  href={routeHash({ view: "section", id: s.id })}
                  className={`sectionlink ${on ? "on" : ""}`}
                  style={{ "--section": s.accent } as React.CSSProperties}
                  title={turn ? `${s.name}: ${turnWords(turn)}` : s.brief ? `${s.name}: ${s.brief}` : s.name}
                  aria-current={on ? "page" : undefined}
                >
                  <span className="accent" />
                  <Icon name={s.icon} size={16} />
                  <span className="sectionname">{s.name}</span>
                  {turn ? <span className="pulse" /> : unread[s.id] ? <span className="unread" aria-label="new" /> : null}
                </a>
              </li>
            );
          })}
        </ul>
        <div className="sidefoot">
          <a href={routeHash({ view: "devices" })} className={`sidelink ${route.view === "devices" ? "on" : ""}`} title="Pair a phone or a browser, revoke one">
            <Icon name="devices" size={16} /> Devices
          </a>
          <SpeakingSwitch />
          <a href={routeHash({ view: "intro" })} className={`sidelink ${route.view === "intro" ? "on" : ""}`} title="What this app does">
            <Icon name="insights" size={16} /> Introduction
          </a>
          <div className="hubline" title={hubName ? `${hubName}: ${LINK_WORDS[link]}` : LINK_WORDS[link]}>
            <span className={`linkdot ${link}`} />
            <span>{hubName || "hub"}</span>
            <span className="mono">{LINK_WORDS[link]}</span>
          </div>
        </div>
      </nav>
      <main className="main">{main}</main>
    </div>
  );
}
