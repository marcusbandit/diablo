import type { ImageSourcePropType } from "react-native";

// Metro needs every image as a literal require. The drawings live once, in assets/faces at the
// repo root, shared with the desktop overlay.
const FACES: Record<string, ImageSourcePropType> = {
  resting: require("../../assets/faces/resting/1.png"),
  listening: require("../../assets/faces/listening/1.png"),
  thinking: require("../../assets/faces/thinking/1.png"),
  talking: require("../../assets/faces/talking/1.png"),
  sorry: require("../../assets/faces/sorry/1.png"),
  expectant: require("../../assets/faces/expectant/1.png"),
  delighted: require("../../assets/faces/delighted/1.png"),
};

export function face(name: string): ImageSourcePropType {
  return FACES[name] ?? FACES.resting;
}
