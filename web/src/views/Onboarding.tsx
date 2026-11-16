import { Hub, HubError, normalizePairCode } from "@diablo/hub";
import { useEffect, useRef, useState } from "react";
import { deviceLabel, doorShut, shutDoor } from "../lib/auth";
import { Icon } from "../ui/Icon";
import { Brand } from "../ui/Brand";

type Status = { text: string; tone?: "ok" | "bad" | "busy" } | null;

function Preview() {
  return (
    <div className="preview" aria-hidden>
      <div className="previewrail">
        {[
          ["Diablo", "var(--accent)"],
          ["Workout", "var(--ok)"],
          ["Home", "var(--t-lights)"],
          ["Projects", "var(--t-search)"],
        ].map(([name, colour], i) => (
          <div key={name} className={`previewsection ${i === 0 ? "on" : ""}`}>
            <i style={{ background: colour }} />
            {name}
          </div>
        ))}
      </div>
      <div className="previewchat">
        <div className="previewyou">Dim the desk lamp and remind me to stretch at four</div>
        <div className="previewreply">Done. The desk lamp is at 30%, and I will remind you at 16:00.</div>
        <div className="previewcard">
          <span className="mark" />
          <span>Reminders</span>
          <span className="mono">16:00</span>
        </div>
      </div>
    </div>
  );
}

function Intro({ onNext, onClose }: { onNext?: () => void; onClose?: () => void }) {
  return (
    <div className="onboard-step">
      <h1>Diablo in this browser</h1>
      <Preview />
      <ul className="payoff">
        <li>
          <Icon name="done" /> Every section and its history, live as he answers
        </li>
        <li>
          <Icon name="done" /> The same Diablo as Telegram, the phone and your voice
        </li>
        <li>
          <Icon name="done" /> Panels for the lamps, training and mail, with taps that act
        </li>
      </ul>
      <div className="onboard-actions">
        {onNext ? (
          <button type="button" className="button primary" onClick={onNext}>
            Pair this browser <Icon name="send" size={14} />
          </button>
        ) : null}
        {onClose ? (
          <button type="button" className="button" onClick={onClose}>
            Close
          </button>
        ) : null}
      </div>
    </div>
  );
}

function Pair({ revoked, onPaired, onBack }: { revoked: boolean; onPaired: (token: string) => void; onBack: () => void }) {
  const hub = useRef(new Hub("")).current;
  const [code, setCode] = useState("");
  const [status, setStatus] = useState<Status>(null);
  const [door, setDoor] = useState<"asking" | "shut" | "open">(doorShut() ? "shut" : "asking");
  const input = useRef<HTMLInputElement>(null);
  const name = deviceLabel(navigator.userAgent);

  const done = (token: string, device: string, where: string) => {
    setStatus({ text: `Paired as ${device}${where ? ` on ${where}` : ""}`, tone: "ok" });
    setTimeout(() => onPaired(token), 700);
  };

  // Through Diablo's own tailnet name the hub knows who this is and pairs with no code.
  useEffect(() => {
    if (door !== "asking") return;
    let live = true;
    hub.pairTailnet(name).then(
      (out) => live && (setDoor("open"), done(out.token, out.device, out.hub)),
      (err: unknown) => {
        if (!live) return;
        if (err instanceof HubError && err.status === 403) shutDoor();
        setDoor("shut");
      },
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (door === "shut") input.current?.focus();
  }, [door]);

  const redeem = async () => {
    const normal = normalizePairCode(code);
    if (!normal) {
      setStatus({ text: "A code is eight letters, like ABCD-EFGH", tone: "bad" });
      return;
    }
    setStatus({ text: "Pairing", tone: "busy" });
    try {
      const out = await hub.pair(normal, name);
      done(out.token, out.device, out.hub);
    } catch (err) {
      const text = err instanceof HubError ? (err.status === 0 ? "The hub is out of reach. Is Tailscale on?" : err.message) : String(err);
      setStatus({ text, tone: "bad" });
    }
  };

  return (
    <div className="onboard-step">
      <h1>Pair this browser</h1>
      {revoked ? <div className="status bad">The hub no longer knows this browser. Pair it again.</div> : null}
      {door === "asking" ? (
        <div className="status busy">Asking the hub through the tailnet</div>
      ) : door === "open" ? null : (
        <>
          <ol className="steps">
            <li>
              Ask Diablo for a code: send him <code>/pair</code> on Telegram, or press <b>Pair a device</b> under Devices in a browser that is paired.
            </li>
            <li>Type the eight letters here. A code lasts ten minutes and works once.</li>
          </ol>
          <form
            className="pairform"
            onSubmit={(e) => {
              e.preventDefault();
              redeem();
            }}
          >
            <input
              ref={input}
              className="codeinput"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              placeholder="ABCD-EFGH"
              autoComplete="off"
              spellCheck={false}
              maxLength={12}
              aria-label="Pairing code"
            />
            <button type="submit" className="button primary" disabled={status?.tone === "busy"}>
              Pair
            </button>
          </form>
        </>
      )}
      {status ? <div className={`status ${status.tone ?? ""}`}>{status.text}</div> : null}
      <div className="onboard-actions">
        <button type="button" className="button ghost" onClick={onBack}>
          Back
        </button>
      </div>
    </div>
  );
}

/** First run: what the app is for, then pairing. Shown whenever this browser has no token. */
export function Onboarding({ revoked, onPaired }: { revoked: boolean; onPaired: (token: string) => void }) {
  const [step, setStep] = useState(revoked ? 1 : 0);
  return (
    <div className="onboard">
      <div className="onboard-frame">
        <div className="onboard-head">
          <Brand />
          <div className="dots" aria-label={`Step ${step + 1} of 2`}>
            {[0, 1].map((i) => (
              <i key={i} className={i === step ? "on" : ""} />
            ))}
          </div>
        </div>
        {step === 0 ? <Intro onNext={() => setStep(1)} /> : <Pair revoked={revoked} onPaired={onPaired} onBack={() => setStep(0)} />}
      </div>
    </div>
  );
}

/** The introduction again, from the sidebar, with a way out. */
export function IntroAgain({ onClose }: { onClose: () => void }) {
  return (
    <div className="onboard inline">
      <div className="onboard-frame">
        <div className="onboard-head">
          <Brand />
          <button type="button" className="ghost" onClick={onClose} title="Close">
            <Icon name="close" />
          </button>
        </div>
        <Intro onClose={onClose} />
      </div>
    </div>
  );
}
