import { useLocalSearchParams } from "expo-router";

import { Onboarding } from "../src/screens/Onboarding";

export default function OnboardingRoute() {
  const { again } = useLocalSearchParams<{ again?: string }>();
  return <Onboarding again={again === "1"} />;
}
