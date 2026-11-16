import { forwardRef, type ComponentRef, type ReactNode } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
  type StyleProp,
  type TextInputProps,
  type TextProps,
  type TextStyle,
  type ViewStyle,
} from "react-native";

import { fonts, size, sp, tint, useTheme, validAccent } from "../theme";
import { Icon } from "./Icon";

type TP = TextProps & { color?: string; style?: StyleProp<TextStyle> };

export function Reading({ color, style, ...rest }: TP) {
  const { c } = useTheme();
  return <Text {...rest} style={[{ fontFamily: fonts.reading, fontSize: size.normal, lineHeight: 24, color: color ?? c.ink }, style]} />;
}

export function Title({ color, style, ...rest }: TP) {
  const { c } = useTheme();
  return <Text {...rest} style={[{ fontFamily: fonts.titles, fontSize: size.normal, fontWeight: "600", color: color ?? c.ink }, style]} />;
}

export function Big({ color, style, ...rest }: TP) {
  const { c } = useTheme();
  return <Text {...rest} style={[{ fontFamily: fonts.titles, fontSize: size.large, lineHeight: 32, fontWeight: "600", color: color ?? c.ink }, style]} />;
}

export function Label({ color, style, ...rest }: TP) {
  const { c } = useTheme();
  return <Text {...rest} style={[{ fontFamily: fonts.titles, fontSize: size.small, fontWeight: "500", color: color ?? c.muted }, style]} />;
}

export function Mono({ color, style, ...rest }: TP) {
  const { c } = useTheme();
  return <Text {...rest} style={[{ fontFamily: fonts.mono, fontSize: size.small, lineHeight: 19, color: color ?? c.ink }, style]} />;
}

export type ButtonKind = "primary" | "line" | "quiet" | "danger";

export function Button({
  label,
  onPress,
  kind = "line",
  icon,
  disabled,
  busy,
  style,
  grow,
}: {
  label: string;
  onPress: () => void;
  kind?: ButtonKind;
  icon?: string;
  disabled?: boolean;
  busy?: boolean;
  style?: StyleProp<ViewStyle>;
  grow?: boolean;
}) {
  const { c } = useTheme();
  const fill = kind === "primary" ? c.accent : "transparent";
  const ink = kind === "primary" ? c.onAccent : kind === "danger" ? c.fail : kind === "quiet" ? c.accentInk : c.ink;
  const border = kind === "line" ? c.lineStrong : kind === "danger" ? c.fail : "transparent";
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={onPress}
      disabled={disabled || busy}
      android_ripple={{ color: tint(kind === "primary" ? "#000000" : "#888888", 0.2) }}
      style={({ pressed }) => [
        {
          minHeight: 44,
          paddingHorizontal: sp(4),
          flexDirection: "row",
          alignItems: "center",
          justifyContent: "center",
          gap: sp(2),
          backgroundColor: fill,
          borderWidth: border === "transparent" ? 0 : 1,
          borderColor: border,
          opacity: disabled ? 0.45 : pressed ? 0.85 : 1,
          flexGrow: grow ? 1 : 0,
        },
        style,
      ]}
    >
      {busy ? <ActivityIndicator size="small" color={ink} /> : icon ? <Icon name={icon} size={18} color={ink} /> : null}
      <Text style={{ fontFamily: fonts.titles, fontSize: 15, fontWeight: "600", color: ink }}>{label}</Text>
    </Pressable>
  );
}

export function IconButton({
  icon,
  label,
  onPress,
  color,
  size: px = 22,
  disabled,
}: {
  icon: string;
  label: string;
  onPress: () => void;
  color?: string;
  size?: number;
  disabled?: boolean;
}) {
  const { c } = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={onPress}
      disabled={disabled}
      hitSlop={6}
      android_ripple={{ color: c.line, borderless: true, radius: 22 }}
      style={{ width: 44, height: 44, alignItems: "center", justifyContent: "center", opacity: disabled ? 0.4 : 1 }}
    >
      <Icon name={icon} size={px} color={color ?? c.ink} />
    </Pressable>
  );
}

/** A section's mark: its icon on a square of its accent. */
export function Mark({ icon, accent, size: px = 36, dim }: { icon: string; accent: string; size?: number; dim?: boolean }) {
  const { c } = useTheme();
  const a = validAccent(accent, c.accent);
  return (
    <View style={{ width: px, height: px, backgroundColor: tint(a, dim ? 0.1 : 0.2), alignItems: "center", justifyContent: "center" }}>
      <Icon name={icon} size={Math.round(px * 0.56)} color={dim ? tint(a, 0.6) : a} />
    </View>
  );
}

export function Status({ ok, text, waiting }: { ok: boolean; text: string; waiting?: boolean }) {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: sp(3) }}>
      {waiting ? (
        <ActivityIndicator size="small" color={c.faint} />
      ) : (
        <View style={{ width: 8, height: 8, backgroundColor: ok ? c.ok : c.warn }} />
      )}
      <Reading style={{ flex: 1 }}>{text}</Reading>
    </View>
  );
}

export const Field = forwardRef<ComponentRef<typeof TextInput>, TextInputProps & { mono?: boolean }>(function Field({ mono, style, ...rest }, ref) {
  const { c } = useTheme();
  return (
    <TextInput
      ref={ref}
      placeholderTextColor={c.faint}
      selectionColor={c.accent}
      cursorColor={c.accent}
      {...rest}
      style={[
        {
          minHeight: 48,
          paddingHorizontal: sp(4),
          paddingVertical: sp(3),
          backgroundColor: c.sunken,
          borderWidth: 1,
          borderColor: c.line,
          color: c.ink,
          fontFamily: mono ? fonts.mono : fonts.titles,
          fontSize: mono ? 18 : size.normal,
          letterSpacing: mono ? 2 : 0,
        },
        style,
      ]}
    />
  );
});

export function Card({ children, style, bare }: { children: ReactNode; style?: StyleProp<ViewStyle>; bare?: boolean }) {
  const { c } = useTheme();
  return (
    <View
      style={[
        bare ? null : { backgroundColor: c.raised, borderWidth: StyleSheet.hairlineWidth, borderColor: c.line, padding: sp(4) },
        { gap: sp(3) },
        style,
      ]}
    >
      {children}
    </View>
  );
}

/** A card's head: an optional icon and a short title. */
export function CardHead({ title, icon, right }: { title?: string; icon?: string; right?: ReactNode }) {
  const { c } = useTheme();
  if (!title && !icon && !right) return null;
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: sp(2) }}>
      {icon ? <Icon name={icon} size={18} color={c.accentInk} /> : null}
      {title ? (
        <Title style={{ flex: 1 }} numberOfLines={2}>
          {title}
        </Title>
      ) : (
        <View style={{ flex: 1 }} />
      )}
      {right}
    </View>
  );
}
