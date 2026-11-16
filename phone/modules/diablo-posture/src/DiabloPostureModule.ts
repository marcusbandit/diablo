import { NativeModule, requireOptionalNativeModule } from "expo";

import type { DiabloPostureModuleEvents, PostureSnapshot } from "./DiabloPosture.types";

declare class DiabloPostureModule extends NativeModule<DiabloPostureModuleEvents> {
  get(): PostureSnapshot;
}

/** Null where the native side is missing (web, tests). */
export default requireOptionalNativeModule<DiabloPostureModule>("DiabloPosture");
