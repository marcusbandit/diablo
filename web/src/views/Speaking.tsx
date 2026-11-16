import { useEffect, useState } from "react";
import { errorText, useHub } from "../lib/hub";
import { useToast } from "../lib/toast";
import { Icon } from "../ui/Icon";

/** Speaking mode: whether Diablo speaks and listens at the desk. */
export function SpeakingSwitch({ wide = false }: { wide?: boolean }) {
  const { hub } = useHub();
  const toast = useToast();
  const [on, setOn] = useState<boolean | null>(null);

  useEffect(() => {
    hub.setting("speaking_mode").then(
      (value) => setOn(value === "on"),
      () => setOn(false),
    );
  }, [hub]);

  const flip = async () => {
    const next = !on;
    setOn(next);
    try {
      await hub.putSetting("speaking_mode", next ? "on" : "off");
    } catch (e) {
      setOn(!next);
      toast(errorText(e), "fail");
    }
  };

  return (
    <button
      type="button"
      className={`toggle ${wide ? "wide" : ""} ${on ? "on" : ""}`}
      role="switch"
      aria-checked={!!on}
      disabled={on == null}
      onClick={flip}
      title={on ? "Speaking is on: Diablo speaks and listens at the desk. Turn it off" : "Speaking is off. Turn it on"}
    >
      <Icon name={on ? "speaking" : "muted"} size={16} />
      <span className="togglelabel">Speaking</span>
      <span className="knob" aria-hidden />
    </button>
  );
}
