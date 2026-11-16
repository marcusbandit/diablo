import { Redirect } from "expo-router";

import { Shell } from "../src/screens/Shell";
import { useModel } from "../src/use";

export default function Home() {
  const onboarded = useModel((s) => s.onboarded);
  if (!onboarded) return <Redirect href="/onboarding" />;
  return <Shell />;
}
