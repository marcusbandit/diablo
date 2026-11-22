import { router, useFocusEffect } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useMemo, useState } from "react";
import { BackHandler, Pressable, ScrollView, Text, TextInput, View, useWindowDimensions } from "react-native";
import { useReanimatedKeyboardAnimation } from "react-native-keyboard-controller";
import Animated, { useAnimatedStyle } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { doing, needsYou } from "../src/engine/derive";
import type { Block } from "../src/engine/wire";
import { mood } from "../src/mood";
import { MoodBlock } from "../src/MoodBlock";
import { color, font, radius, space, type, type State } from "../src/tokens";
import { engine, useEngine } from "../src/useEngine";
import { amount, clamp, type Exercise, logAct, readLogger, searchExercises, type Session } from "../src/workout";

const SECTION = "workout";
const WIDE = 600;

/** Every line this room shows, in one place. */
const T = {
  state: { resting: "Resting", listening: "Listening", thinking: "Thinking", speaking: "Speaking", trouble: "Trouble" } as Record<State, string>,
  back: "Back to the den",
  title: "Workout",
  setsToday: (n: number) => (n === 1 ? "set today" : "sets today"),
  search: "Search exercises",
  recent: "Recent",
  more: "More",
  noMatch: "No matching exercises",
  change: "Change",
  sets: "Sets",
  reps: "Reps",
  seconds: "Seconds",
  log: (sets: number, reps: string) => (sets === 1 ? `Log ${reps}` : `Log ${sets} × ${reps}`),
  done: "Finish",
  today: "Today",
  nothingToday: "No sets today",
  now: "Now",
  offline: "I can't reach the hub, sir.",
  talk: "Talk to Diablo",
} as const;

type Picked = Pick<Exercise, "id" | "name" | "timed">;

function Chip({ label, onPress, strong }: { label: string; onPress: () => void; strong?: boolean }) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      style={({ pressed }) => ({
        backgroundColor: strong ? color.text : color.raised,
        opacity: pressed ? 0.7 : 1,
        borderRadius: radius.control,
        paddingHorizontal: space(3),
        paddingVertical: space(2.5),
      })}
    >
      <Text style={{ color: strong ? color.bg : color.text, fontFamily: font.medium, ...type.body }}>{label}</Text>
    </Pressable>
  );
}

function Heading({ children }: { children: string }) {
  return <Text style={{ color: color.dim, fontFamily: font.medium, ...type.small, textTransform: "uppercase", letterSpacing: 0.6 }}>{children}</Text>;
}

/** A number with a minus and a plus beside it; the number itself takes typing too. */
function Stepper({ label, value, onChange, low, high, step = 1 }: { label: string; value: number; onChange: (n: number) => void; low: number; high: number; step?: number }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const button = (sign: "−" | "+", delta: number) => (
    <Pressable
      onPress={() => onChange(clamp(value + delta, low, high))}
      accessibilityRole="button"
      accessibilityLabel={`${label} ${sign === "+" ? "+" : "-"}${Math.abs(delta)}`}
      style={({ pressed }) => ({ width: 52, height: 52, borderRadius: radius.field, backgroundColor: color.raised, opacity: pressed ? 0.7 : 1, alignItems: "center", justifyContent: "center" })}
    >
      <Text style={{ color: color.text, fontFamily: font.heavy, fontSize: 26, lineHeight: 30 }}>{sign}</Text>
    </Pressable>
  );
  return (
    <View style={{ flex: 1, minWidth: 0, gap: space(1.5) }}>
      <Heading>{label}</Heading>
      <View style={{ flexDirection: "row", alignItems: "center", gap: space(2) }}>
        {button("−", -step)}
        <TextInput
          value={text}
          onChangeText={(t) => {
            const digits = t.replace(/[^0-9]/g, "");
            if (!digits) return setText("");
            const n = clamp(Number(digits), low, high);
            setText(String(n));
            onChange(n);
          }}
          onBlur={() => setText(String(value))}
          keyboardType="number-pad"
          selectTextOnFocus
          accessibilityLabel={label}
          style={{ flex: 1, minWidth: 0, textAlign: "center", color: color.text, fontFamily: font.heavy, ...type.head, paddingVertical: space(1) }}
        />
        {button("+", step)}
      </View>
    </View>
  );
}

function SessionRow({ s }: { s: Session }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "baseline", gap: space(3), paddingVertical: space(2.5), borderBottomWidth: 1, borderBottomColor: color.line }}>
      <View style={{ flex: 1, minWidth: 0, gap: space(0.5) }}>
        <Text style={{ color: color.text, fontFamily: font.medium, ...type.body }}>{s.name}</Text>
        <Text style={{ color: color.sub, fontFamily: font.regular, ...type.sub }}>{s.sets.map((n) => amount(n, s.timed)).join("  ·  ")}</Text>
      </View>
      <Text style={{ color: s.open ? color.text : color.dim, fontFamily: font.medium, ...type.sub }}>{s.open ? T.now : s.at}</Text>
    </View>
  );
}

export default function Workout() {
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();
  const wide = width >= WIDE;
  const s = useEngine((x) => x);
  const now = doing(s, false);
  const m = mood(now, needsYou(s), s.link);
  const tick = s.panelTick[SECTION] ?? 0;

  const [blocks, setBlocks] = useState<Block[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Picked | null>(null);
  const [changing, setChanging] = useState(false);
  const [sets, setSets] = useState(3);
  const [reps, setReps] = useState(10);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");

  const logger = useMemo(() => readLogger(blocks), [blocks]);
  const open = logger?.current ?? null;
  const target: Picked | null = picked ?? (open && !changing ? { id: open.exercise, name: open.name, timed: open.timed } : null);
  const onOpen = !!open && !!target && target.id === open.exercise;

  const load = useCallback(() => {
    void engine.panel(SECTION).then((got) => {
      if (got) setBlocks(got);
      setFailed(got === null);
    });
  }, []);
  useEffect(load, [load, tick, s.link]);

  // The open session sets the steppers: one more set, about the size of the last ones.
  useEffect(() => {
    if (open && !picked && !changing) {
      setSets(1);
      setReps(open.suggest ?? open.sets[open.sets.length - 1] ?? 10);
    }
  }, [open?.id, open?.sets.length, picked, changing]);

  const pick = (e: Picked) => {
    setPicked(e);
    setChanging(false);
    setQuery("");
    setError("");
    const same = logger?.last && logger.last.exercise === e.id;
    setSets(open && open.exercise === e.id ? 1 : 3);
    setReps(same ? logger!.last!.did : e.timed ? 30 : 10);
  };

  // Back steps out of what is open in the room first: a search, then a pick, then the room.
  useFocusEffect(
    useCallback(() => {
      const sub = BackHandler.addEventListener("hardwareBackPress", () => {
        if (query) return setQuery(""), true;
        if (picked || changing) return setPicked(null), setChanging(false), true;
        return false;
      });
      return () => sub.remove();
    }, [query, picked, changing]),
  );

  const log = async () => {
    if (!target || busy) return;
    setBusy(true);
    setError("");
    const out = await engine.act(logAct(target.id, sets, reps), SECTION);
    setBusy(false);
    if ("error" in out) return setError(out.error);
    if (out.panel) setBlocks(out.panel);
    else load();
    setPicked(null);
    setChanging(false);
  };

  const close = async () => {
    if (busy) return;
    setBusy(true);
    const out = await engine.act({ module: "workout", action: "close", args: {} }, SECTION);
    setBusy(false);
    if ("error" in out) return setError(out.error);
    if (out.panel) setBlocks(out.panel);
    else load();
  };

  const keyboard = useReanimatedKeyboardAnimation();
  const pad = space(3.5);
  const inset = insets.bottom;
  const lift = useAnimatedStyle(() => ({ paddingBottom: -keyboard.height.value }));
  const bar = useAnimatedStyle(() => ({ paddingBottom: pad + inset * (1 - keyboard.progress.value) }));

  const send = () => {
    const text = draft.trim();
    setDraft("");
    if (text) void engine.send(text, SECTION);
  };

  const hits = logger && query ? searchExercises(logger.library, query) : [];

  const entry = target ? (
    <View style={{ gap: space(4) }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: space(3) }}>
        <Text style={{ flex: 1, minWidth: 0, color: color.text, fontFamily: font.heavy, ...type.title }}>{target.name}</Text>
        <Chip label={T.change} onPress={() => (setPicked(null), setChanging(true))} />
      </View>
      {onOpen && open ? (
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space(2) }}>
          {open.sets.map((n, i) => (
            <View key={i} style={{ borderRadius: radius.control, borderWidth: 1, borderColor: color.line, paddingHorizontal: space(2.5), paddingVertical: space(1.5) }}>
              <Text style={{ color: color.sub, fontFamily: font.medium, ...type.body }}>{amount(n, open.timed)}</Text>
            </View>
          ))}
        </View>
      ) : null}
      <View style={{ flexDirection: "row", gap: space(4) }}>
        <Stepper label={T.sets} value={sets} onChange={setSets} low={1} high={20} />
        <Stepper label={target.timed ? T.seconds : T.reps} value={reps} onChange={setReps} low={1} high={1000} step={target.timed ? 5 : 1} />
      </View>
      <View style={{ flexDirection: "row", gap: space(3) }}>
        <Pressable
          onPress={log}
          disabled={busy}
          accessibilityRole="button"
          style={({ pressed }) => ({ flex: 1, alignItems: "center", backgroundColor: color.text, opacity: busy ? 0.5 : pressed ? 0.8 : 1, borderRadius: radius.field, paddingVertical: space(3.5) })}
        >
          <Text style={{ color: color.bg, fontFamily: font.heavy, ...type.body }}>{T.log(sets, amount(reps, target.timed))}</Text>
        </Pressable>
        {onOpen ? (
          <Pressable
            onPress={close}
            disabled={busy}
            accessibilityRole="button"
            style={({ pressed }) => ({ alignItems: "center", backgroundColor: color.raised, opacity: pressed ? 0.8 : 1, borderRadius: radius.field, paddingVertical: space(3.5), paddingHorizontal: space(5) })}
          >
            <Text style={{ color: color.text, fontFamily: font.medium, ...type.body }}>{T.done}</Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  ) : null;

  const picker =
    !target && logger ? (
      <View style={{ gap: space(4) }}>
        <TextInput
          value={query}
          onChangeText={setQuery}
          placeholder={T.search}
          placeholderTextColor={color.dim}
          autoCorrect={false}
          autoCapitalize="none"
          returnKeyType="search"
          onSubmitEditing={() => hits[0] && pick(hits[0])}
          style={{ backgroundColor: color.raised, borderRadius: radius.field, paddingHorizontal: space(3), paddingVertical: space(3), color: color.text, fontFamily: font.regular, ...type.body }}
        />
        {query ? (
          hits.length ? (
            <View>
              {hits.map((e) => (
                <Pressable
                  key={e.id}
                  onPress={() => pick(e)}
                  accessibilityRole="button"
                  style={({ pressed }) => ({ paddingVertical: space(3), borderBottomWidth: 1, borderBottomColor: color.line, opacity: pressed ? 0.6 : 1 })}
                >
                  <Text style={{ color: color.text, fontFamily: font.medium, ...type.body }}>{e.name}</Text>
                </Pressable>
              ))}
            </View>
          ) : (
            <Text style={{ color: color.sub, fontFamily: font.regular, ...type.body }}>{T.noMatch}</Text>
          )
        ) : (
          <>
            {logger.recent.length ? (
              <View style={{ gap: space(2) }}>
                <Heading>{T.recent}</Heading>
                <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space(2) }}>
                  {logger.recent.map((e) => (
                    <Chip key={e.id} label={e.name} onPress={() => pick(e)} />
                  ))}
                </View>
              </View>
            ) : null}
            <View style={{ gap: space(2) }}>
              <Heading>{T.more}</Heading>
              <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space(2) }}>
                {logger.defaults.map((e) => (
                  <Chip key={e.id} label={e.name} onPress={() => pick(e)} />
                ))}
              </View>
            </View>
          </>
        )}
      </View>
    ) : null;

  const big = (
    <View>
      <Text style={{ color: color.text, fontFamily: font.heavy, ...type.big, letterSpacing: -1 }}>{logger?.setsToday ?? 0}</Text>
      <Text style={{ color: color.sub, fontFamily: font.medium, ...type.body }}>{T.setsToday(logger?.setsToday ?? 0)}</Text>
    </View>
  );

  const today = (
    <View style={{ gap: space(1) }}>
      <Heading>{T.today}</Heading>
      {logger?.today.length ? logger.today.map((x) => <SessionRow key={x.id} s={x} />) : (
        <Text style={{ color: color.sub, fontFamily: font.regular, ...type.body, paddingVertical: space(2) }}>{T.nothingToday}</Text>
      )}
    </View>
  );

  const work = (
    <View style={{ gap: space(6) }}>
      {big}
      {failed && !logger ? <Text style={{ color: color.attention, fontFamily: font.regular, ...type.body }}>{T.offline}</Text> : null}
      {entry ?? picker}
      {error ? <Text style={{ color: color.attention, fontFamily: font.regular, ...type.body }}>{error}</Text> : null}
    </View>
  );

  return (
    <Animated.View style={[{ flex: 1, backgroundColor: color.bg }, lift]}>
      <StatusBar style={m.fill === color.attention ? "dark" : "light"} />
      <MoodBlock
        mood={{ ...m, headline: T.title }}
        label={T.state[now]}
        height={118}
        inset={insets.top}
        onBack={() => (router.canGoBack() ? router.back() : router.replace("/"))}
        backLabel={T.back}
      />
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: space(3.5), paddingBottom: space(6) }} keyboardShouldPersistTaps="handled">
        {wide ? (
          <View style={{ flexDirection: "row", gap: space(8) }}>
            <View style={{ flex: 1.2, minWidth: 0 }}>{work}</View>
            <View style={{ flex: 1, minWidth: 0, paddingTop: space(2) }}>{today}</View>
          </View>
        ) : (
          <View style={{ gap: space(8) }}>
            {work}
            {today}
          </View>
        )}
      </ScrollView>
      <Animated.View style={[{ paddingHorizontal: space(3.5), paddingTop: space(2) }, bar]}>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          onSubmitEditing={send}
          submitBehavior="submit"
          returnKeyType="send"
          placeholder={T.talk}
          placeholderTextColor={color.dim}
          style={{ backgroundColor: color.raised, borderRadius: radius.field, paddingHorizontal: space(3), paddingVertical: space(2.5), color: color.text, fontFamily: font.regular, ...type.body }}
        />
      </Animated.View>
    </Animated.View>
  );
}

