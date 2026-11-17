// Native settings the app needs on every prebuild, so android/ never needs a hand edit.
const { withAndroidManifest, withAppBuildGradle } = require("expo/config-plugins");

// The main activity survives fold, unfold and rotate: React re-lays out from the new window
// instead of the activity restarting and the screen remounting mid-gesture.
const CHANGES = ["keyboard", "keyboardHidden", "orientation", "screenSize", "screenLayout", "smallestScreenSize", "density", "uiMode"];

function withActivity(config) {
  return withAndroidManifest(config, (mod) => {
    const app = mod.modResults.manifest.application?.[0];
    const main = app?.activity?.find((a) => a.$["android:name"] === ".MainActivity");
    if (main) {
      const had = (main.$["android:configChanges"] || "").split("|").filter(Boolean);
      main.$["android:configChanges"] = [...new Set([...had, ...CHANGES])].join("|");
      main.$["android:resizeableActivity"] = "true";
    }
    return mod;
  });
}

// Sign with this machine's own debug key (~/.android/debug.keystore, or $DIABLO_DEBUG_KEYSTORE),
// the key the current app is signed with, so at the cutover in #91 this app installs over it.
// A machine without one falls back to the template's key.
const KEY = `file({ def own = new File(System.getenv("DIABLO_DEBUG_KEYSTORE") ?: (System.getProperty("user.home") + "/.android/debug.keystore")); own.exists() ? own : file("debug.keystore") }())`;

function withMachineKey(config) {
  return withAppBuildGradle(config, (mod) => {
    mod.modResults.contents = mod.modResults.contents.replace("storeFile file('debug.keystore')", `storeFile ${KEY}`);
    return mod;
  });
}

module.exports = function withDiabloNative(config) {
  return withMachineKey(withActivity(config));
};
