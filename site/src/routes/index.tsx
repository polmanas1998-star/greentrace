import { createFileRoute } from "@tanstack/react-router";

import { Closing, Flip, Hero, Honest, HowItDecides, Proof } from "../components/gt/home";
import { Page } from "../components/gt/shell";
import { TimeMachine } from "../components/gt/TimeMachine";
import { PLOTS } from "../lib/plots";

export const Route = createFileRoute("/")({
  component: Home,
});

function Home() {
  return (
    <Page>
      <Hero map={PLOTS.p042} />
      <TimeMachine map={PLOTS.p042} frames={PLOTS.frames} />
      <HowItDecides />
      <Flip map={PLOTS.p042} />
      <Honest map={PLOTS.p041} />
      <Proof />
      <Closing />
    </Page>
  );
}
