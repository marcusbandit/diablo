import type { Device, PairCode, PairStatus } from "@diablo/hub";
import QRCode from "qrcode";
import { useCallback, useEffect, useState } from "react";
import { ago, countdown, fullDate } from "../lib/format";
import { errorText, useHub, useHubEvents } from "../lib/hub";
import { useToast } from "../lib/toast";
import { Icon } from "../ui/Icon";
import { routeHash } from "../lib/route";
import { SpeakingSwitch } from "./Speaking";

type Me = { master: boolean; device: { id: number; name: string } | null; hub: string };

function Minted({ code, onPaired }: { code: PairCode; onPaired: () => void }) {
  const { hub } = useHub();
  const [svg, setSvg] = useState("");
  const [status, setStatus] = useState<PairStatus>({ state: "waiting", expires_at: code.expires_at });
  const [now, setNow] = useState(Date.now() / 1000);

  useEffect(() => {
    QRCode.toString(code.url, { type: "svg", margin: 2, errorCorrectionLevel: "M", color: { dark: "#000000", light: "#ffffff" } }).then(setSvg, () => setSvg(""));
  }, [code.url]);

  useEffect(() => {
    if (status.state !== "waiting") return;
    const tick = setInterval(() => setNow(Date.now() / 1000), 1000);
    const poll = setInterval(() => {
      hub.pairStatus(code.code).then((s) => {
        setStatus(s);
        if (s.state === "paired") onPaired();
      }, () => {});
    }, 2000);
    return () => {
      clearInterval(tick);
      clearInterval(poll);
    };
  }, [hub, code.code, status.state, onPaired]);

  const left = code.expires_at - now;
  const state = status.state === "waiting" && left <= 0 ? "expired" : status.state;
  return (
    <div className="minted">
      <a className="qr" href={code.url} target="_blank" rel="noopener noreferrer" title={code.url} dangerouslySetInnerHTML={{ __html: svg }} />
      <div className="mintedside">
        <div className="letters" title="The pairing code">
          {code.code}
        </div>
        <ol className="steps">
          <li>On the phone, open the Diablo app at its pairing step.</li>
          <li>Tap Scan QR code and point it here, or type the letters.</li>
        </ol>
        {state === "paired" ? (
          <div className="status ok">Paired with {"device" in status ? status.device : "a device"}</div>
        ) : state === "expired" || state === "unknown" ? (
          <div className="status bad">This code ran out. Make a new one.</div>
        ) : (
          <div className="status busy" title={`Runs out at ${fullDate(code.expires_at)}`}>
            Waiting for a device, {countdown(left)} left
          </div>
        )}
      </div>
    </div>
  );
}

function DeviceRow({ device, now, onRevoke }: { device: Device; now: number; onRevoke: (d: Device) => Promise<void> }) {
  const [asking, setAsking] = useState(false);
  const seen = device.last_seen ?? device.created ?? 0;
  return (
    <li className="device">
      <div className="devicetext">
        <span className="devicename">
          {device.name}
          {device.current ? <span className="tag">this browser</span> : null}
        </span>
        <span className="devicemeta" title={`Paired ${device.created ? fullDate(device.created) : "?"}, last seen ${seen ? fullDate(seen) : "never"}`}>
          {device.kind ? `${device.kind}, ` : ""}seen {seen ? ago(seen, now) : "never"}
        </span>
      </div>
      {asking ? (
        <span className="confirm">
          <span>Lock {device.name} out?</span>
          <button type="button" className="button danger small" onClick={() => onRevoke(device).finally(() => setAsking(false))}>
            Revoke
          </button>
          <button type="button" className="button small" onClick={() => setAsking(false)}>
            Keep
          </button>
        </span>
      ) : (
        <button type="button" className="button small" onClick={() => setAsking(true)} title={`Lock ${device.name} out of the hub`}>
          Revoke
        </button>
      )}
    </li>
  );
}

export function Devices() {
  const { hub, signOut } = useHub();
  const toast = useToast();
  const [me, setMe] = useState<Me | null>(null);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [code, setCode] = useState<PairCode | null>(null);
  const [minting, setMinting] = useState(false);
  const [leaving, setLeaving] = useState(false);

  const load = useCallback(() => {
    hub.devices().then(setDevices, (e) => toast(errorText(e), "fail"));
  }, [hub, toast]);

  useEffect(() => {
    hub.whoami().then(setMe, () => {});
    load();
  }, [hub, load]);

  useHubEvents((e) => {
    if (e.kind === "devices") load();
  });

  const mint = async () => {
    setMinting(true);
    try {
      setCode(await hub.mintPairCode());
    } catch (e) {
      toast(`The hub did not make a code: ${errorText(e)}`, "fail");
    } finally {
      setMinting(false);
    }
  };

  const revoke = async (d: Device) => {
    try {
      await hub.revokeDevice(d.id);
      if (d.current) {
        signOut();
        return;
      }
      toast(`${d.name} is locked out`);
      load();
    } catch (e) {
      toast(errorText(e), "fail");
    }
  };

  const unpair = async () => {
    if (me?.device) {
      try {
        await hub.revokeDevice(me.device.id);
      } catch (e) {
        toast(errorText(e), "fail");
        return;
      }
    }
    signOut();
  };

  const now = Date.now() / 1000;
  return (
    <div className="page">
      <header className="pagehead">
        <h1>Devices</h1>
        {me ? <span className="pagemeta">{me.device ? `This browser is ${me.device.name} on ${me.hub}` : `This browser holds the hub token for ${me.hub}`}</span> : null}
      </header>

      <section className="pagesection">
        <h2>Pairing</h2>
        {code ? <Minted key={code.code} code={code} onPaired={load} /> : null}
        <div className="buttons">
          <button type="button" className="button primary" onClick={mint} disabled={minting}>
            <Icon name="add" size={14} /> {code ? "Make a new code" : "Pair a device"}
          </button>
        </div>
      </section>

      <section className="pagesection">
        <h2>
          Paired <span className="mono count">{devices?.length ?? ""}</span>
        </h2>
        {devices == null ? (
          <div className="empty-line">Reading the list</div>
        ) : devices.length ? (
          <ul className="devices">
            {devices.map((d) => (
              <DeviceRow key={d.id} device={d} now={now} onRevoke={revoke} />
            ))}
          </ul>
        ) : (
          <div className="empty-line">Nothing paired yet</div>
        )}
      </section>

      <section className="pagesection">
        <h2>Voice</h2>
        <SpeakingSwitch wide />
      </section>

      <section className="pagesection">
        <h2>This browser</h2>
        {leaving ? (
          <span className="confirm">
            <span>Forget the key and lock this browser out?</span>
            <button type="button" className="button danger small" onClick={unpair}>
              Unpair
            </button>
            <button type="button" className="button small" onClick={() => setLeaving(false)}>
              Keep
            </button>
          </span>
        ) : (
          <div className="buttons">
            <button type="button" className="button" onClick={() => setLeaving(true)}>
              Unpair this browser
            </button>
          </div>
        )}
      </section>

      <a className="quietlink" href={routeHash({ view: "character" })}>
        character
      </a>
    </div>
  );
}
