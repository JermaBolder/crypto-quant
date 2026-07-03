"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { getJSON, type Bar, type Health, type Stats } from "@/lib/api";

// lightweight-charts is canvas + window; it has no meaning on the server.
// Dynamic import keeps it out of the SSR pass and out of the shell bundle.
const Chart = dynamic(() => import("@/components/Chart"), {
  ssr: false,
  loading: () => (
    <div className="flex h-105 items-center justify-center text-sm text-dim">
      loading chart…
    </div>
  ),
});

const POLL_MS = 3000;

// --- module-level pieces (never define components inside components) ---

function fmtPrice(x: number | null): string {
  if (x == null) return "—";
  return x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtSigned(x: number, digits = 1): string {
  return `${x >= 0 ? "+" : ""}${x.toFixed(digits)}`;
}

function StatCell({ label, value, tone }: { label: string; value: string; tone?: "buy" | "sell" }) {
  const toneCls = tone === "buy" ? "text-buy" : tone === "sell" ? "text-sell" : "text-ink";
  return (
    <div className="border-l border-line pl-4">
      <div className="text-[10px] uppercase tracking-[0.18em] text-dim">{label}</div>
      <div className={`mt-1 font-mono text-lg leading-none sm:text-xl ${toneCls}`}>{value}</div>
    </div>
  );
}

// The signature element: one thin tape where the hour's sell volume pushes
// from the left (coral) and buy volume from the right (teal). The seam IS the
// imbalance; the center tick marks perfect balance. The whole research story
// of this project lived and died within a few percent of that seam.
function FlowTape({ buyShare }: { buyShare: number | null }) {
  const share = buyShare ?? 0.5;
  return (
    <div className="flex items-center gap-3">
      <span className="text-[10px] uppercase tracking-[0.18em] text-sell">sell</span>
      <div className="relative h-2 flex-1 overflow-hidden rounded-[2px] bg-panel">
        <div
          className="absolute inset-y-0 left-0 bg-sell/75"
          style={{ width: `${(1 - share) * 100}%` }}
        />
        <div
          className="absolute inset-y-0 right-0 bg-buy/75"
          style={{ width: `${share * 100}%` }}
        />
        <div className="absolute inset-y-0 left-1/2 w-px bg-ink/50" />
      </div>
      <span className="text-[10px] uppercase tracking-[0.18em] text-buy">buy</span>
      <span className={`font-mono text-xs ${share >= 0.5 ? "text-buy" : "text-sell"}`}>
        {Math.round(share * 100)}% buy
      </span>
    </div>
  );
}

function Freshness({ health }: { health: Health | null }) {
  if (!health || health.age_s == null) {
    return <span className="text-xs text-dim">no data yet</span>;
  }
  const age = health.age_s;
  const [cls, dot, text] =
    age < 10
      ? ["text-buy", "bg-buy", `live · ${age.toFixed(1)}s ago`]
      : age < 180
        ? ["text-warn", "bg-warn", `lagging · ${Math.round(age)}s ago`]
        : ["text-sell", "bg-sell", `stalled ${Math.round(age / 60)}m — docker compose ps`];
  return (
    <span className={`flex items-center gap-2 font-mono text-xs ${cls}`}>
      <span className={`live-dot h-1.5 w-1.5 rounded-full ${dot}`} />
      {text}
    </span>
  );
}

export default function Terminal() {
  const [bars, setBars] = useState<Bar[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        // independent questions -> one parallel round-trip, no waterfall
        const [b, s, h] = await Promise.all([
          getJSON<Bar[]>("/bars?minutes=180"),
          getJSON<Stats>("/stats"),
          getJSON<Health>("/health"),
        ]);
        if (!alive) return;
        setBars(b);
        setStats(s);
        setHealth(h);
        setErr(null);
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : String(e));
      }
    }
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="flex flex-col gap-4">
      {/* top bar */}
      <header className="flex items-baseline justify-between border-b border-line pb-3">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-sm font-semibold tracking-tight">
            crypto-quant<span className="text-dim">/</span>BTCUSDT
          </span>
          <span className="hidden text-xs text-dim sm:inline">spot · binance · 1m bars</span>
        </div>
        <Freshness health={health} />
      </header>

      {err !== null ? (
        <div className="rounded-[2px] border border-warn/50 bg-warn/10 px-3 py-2 font-mono text-xs text-warn">
          api unreachable ({err}) — start it: .venv/bin/uvicorn api:app --port 8000
        </div>
      ) : null}

      {/* price hero + hour stats */}
      <section className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
        <div>
          <div className="text-[10px] uppercase tracking-[0.18em] text-dim">last</div>
          <div className="mt-1 font-mono text-4xl leading-none tracking-tight sm:text-5xl">
            {fmtPrice(stats?.last_price ?? null)}
          </div>
        </div>
        <div className="grid grid-cols-3 gap-x-6">
          <StatCell label="vol 1h" value={stats ? `${stats.vol_1h.toFixed(1)} BTC` : "—"} />
          <StatCell
            label="delta 1h"
            value={stats ? fmtSigned(stats.delta_1h) : "—"}
            tone={stats ? (stats.delta_1h >= 0 ? "buy" : "sell") : undefined}
          />
          <StatCell
            label="trades/min"
            value={stats ? String(Math.round(stats.trades_per_min)) : "—"}
          />
        </div>
      </section>

      <FlowTape buyShare={stats?.buy_share_1h ?? null} />

      {/* chart panel */}
      <section className="rounded-[3px] border border-line bg-panel/40 p-2">
        {bars.length > 0 || err === null ? (
          <Chart bars={bars} />
        ) : (
          <div className="flex h-105 items-center justify-center font-mono text-xs text-dim">
            no bars in the last 3h — was the pipeline down?
          </div>
        )}
      </section>

      <footer className="flex justify-between text-[11px] text-dim">
        <span>times local · candles 1m · Δ = buy − sell volume (aggressor side) · gaps = pipeline downtime</span>
        <span className="hidden sm:inline">own pipeline: ws → redis → questdb</span>
      </footer>
    </div>
  );
}
