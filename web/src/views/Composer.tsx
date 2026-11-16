import { useEffect, useLayoutEffect, useRef } from "react";
import { Icon } from "../ui/Icon";

export function Composer({
  value,
  onChange,
  onSend,
  running,
  onStop,
  placeholder,
  focusKey,
  sectionKey,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: (text: string) => void;
  running: boolean;
  onStop: () => void;
  placeholder: string;
  focusKey: number;
  sectionKey: string;
}) {
  const area = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, Math.round(window.innerHeight * 0.4))}px`;
  }, [value]);

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.focus();
    if (focusKey) el.setSelectionRange(el.value.length, el.value.length);
  }, [focusKey, sectionKey]);

  const typed = value.trim().length > 0;
  return (
    <form
      className={`composer ${typed ? "typed" : ""}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (typed) onSend(value);
      }}
    >
      <textarea
        ref={area}
        rows={1}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (typed) onSend(value);
          }
        }}
        aria-label="Message"
      />
      {running && !typed ? (
        <button type="button" className="send stop" onClick={onStop} title="Stop the turn running here">
          <Icon name="stop" size={16} />
          <span>Stop</span>
        </button>
      ) : (
        <button type="submit" className="send" disabled={!typed} title={typed ? "Send (Enter). Shift+Enter for a new line" : "Write something first"}>
          <Icon name="send" size={16} />
          <span>Send</span>
        </button>
      )}
    </form>
  );
}
