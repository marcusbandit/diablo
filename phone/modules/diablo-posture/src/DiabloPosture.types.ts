/** Jetpack WindowManager's FoldingFeature, bounds in dp of the app's window. */
export type Fold = {
  state: "flat" | "half";
  orientation: "vertical" | "horizontal";
  separating: boolean;
  occlusion: "none" | "full";
  left: number;
  top: number;
  right: number;
  bottom: number;
};

export type PostureSnapshot = {
  fold: Fold | null;
  /** Hinge angle in degrees, 180 flat, or null without a hinge sensor. */
  angle: number | null;
  hasHinge: boolean;
};

export type DiabloPostureModuleEvents = {
  onChange: (snapshot: PostureSnapshot) => void;
};
