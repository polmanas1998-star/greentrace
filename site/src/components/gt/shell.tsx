import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden>
      <rect width="28" height="28" rx="8" fill="#14402B" />
      <path d="M6.5 8.5 L19.5 6.5 L21.5 19.5 L8 21.5 Z" fill="none" stroke="#9BE3B4" strokeWidth="1.8" strokeLinejoin="round" />
      <rect x="14" y="9.5" width="4" height="4" fill="#D9622B" />
    </svg>
  );
}

const LINKS = [
  { to: "/check", label: "Check plots" },
  { to: "/investigate", label: "Investigate a claim" },
] as const;

export function Nav() {
  return (
    <header className="fixed inset-x-0 top-0 z-50 border-b border-gt-line/60 bg-gt-ground/65 backdrop-blur-xl backdrop-saturate-150">
      <div className="mx-auto flex h-16 max-w-[1400px] items-center justify-between gap-6 px-6 md:px-12">
        <Link to="/" className="flex items-center gap-2.5 text-gt-text" aria-label="GreenTrace home">
          <Mark />
          <span className="text-[15px] font-semibold tracking-tight">GreenTrace</span>
        </Link>
        <nav className="hidden items-center gap-1 rounded-full border border-gt-line bg-gt-ground/70 p-1 backdrop-blur-xl md:flex">
          {LINKS.map((l) => (
            <Link
              key={l.to}
              to={l.to}
              className="rounded-full px-4 py-1.5 text-[13px] text-gt-muted transition-colors hover:text-gt-text"
              activeProps={{ className: "rounded-full px-4 py-1.5 text-[13px] bg-gt-raised-2 text-gt-text" }}
            >
              {l.label}
            </Link>
          ))}
          <Link to="/" hash="how" className="rounded-full px-4 py-1.5 text-[13px] text-gt-muted transition-colors hover:text-gt-text">
            How it works
          </Link>
        </nav>
        <Link
          to="/check"
          className="group hidden items-center gap-2 bg-gt-signal px-4 py-2 text-[13px] font-semibold text-gt-ground transition-transform active:translate-y-px sm:flex"
        >
          Check your plots
          <span className="transition-transform group-hover:translate-x-0.5" aria-hidden>
            →
          </span>
        </Link>
        <MobileMenu />
      </div>
    </header>
  );
}

function MobileMenu() {
  return (
    <details className="relative md:hidden">
      <summary className="list-none rounded-full border border-gt-line bg-gt-ground/80 px-4 py-1.5 text-[13px] text-gt-text backdrop-blur-xl [&::-webkit-details-marker]:hidden">
        Menu
      </summary>
      <div className="absolute right-0 mt-2 w-56 rounded-[14px] border border-gt-line bg-gt-raised p-2 shadow-2xl">
        {[{ to: "/", label: "Home" }, ...LINKS].map((l) => (
          <Link key={l.to} to={l.to} className="block rounded-lg px-3 py-2 text-sm text-gt-text hover:bg-gt-raised-2">
            {l.label}
          </Link>
        ))}
      </div>
    </details>
  );
}

export function Footer() {
  return (
    <footer className="border-t border-gt-line">
      <div className="mx-auto grid max-w-[1400px] gap-10 px-6 py-14 md:grid-cols-[1.2fr_1fr_1fr] md:px-12">
        <div>
          <div className="flex items-center gap-2.5">
            <Mark size={24} />
            <span className="font-semibold tracking-tight">GreenTrace</span>
          </div>
          <p className="mt-4 max-w-[42ch] text-sm leading-relaxed text-gt-muted">
            An explainable investigator for EU Deforestation Regulation checks. Built for the Open Agent Hackathon 2026,
            track The Agent That Can Explain Why.
          </p>
        </div>
        <div className="text-sm leading-relaxed text-gt-muted">
          <div className="mb-2 text-gt-text">Data</div>
          <p>Contains modified Copernicus Sentinel data 2019-2026 (Earth Search, Element 84).</p>
          <p className="mt-2">EU JRC Global Forest Cover 2020, v4. Hansen/UMD/Google/USGS/NASA Global Forest Change v1.12.</p>
        </div>
        <div className="text-sm leading-relaxed text-gt-muted">
          <div className="mb-2 text-gt-text">Built with</div>
          <p>
            In live mode: Zetaris answers every query, Meterless World Model and H-MEM hold the memory, NVIDIA Nemotron
            reads and writes through Meterless Nightshift, one signed receipt per call. The public demo runs in sample mode: the same queries on a local copy, rules in place of the model.
          </p>
          <p className="mt-2">Companies and deliveries in the demo are fictional. Plots P-041 and P-042 are real places.</p>
        </div>
      </div>
    </footer>
  );
}

export function Page({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-gt-ground text-gt-text">
      <Nav />
      <main>{children}</main>
      <Footer />
    </div>
  );
}
