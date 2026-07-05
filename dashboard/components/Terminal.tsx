"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { getJSON, type Bar, type Funding, type Health, type Stats } from "@/lib/api";

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

// Funding as a bar sparkline: one thin bar per 8h interval, teal up / coral
// down from a zero baseline — the same sign language as the delta histogram.
// You SEE the carry finding here: mostly teal (funding usually pays the short),
// with the occasional coral dip. No canvas; pure SVG, so no autoSize race.
function FundingSpark({ series }: { series: Funding["series"] }) {
  if (series.length === 0) {
    return <div className="h-10 text-xs text-dim">no funding data — run backfill_futures.py</div>;
  }
  const rates = series.map((p) => p.rate_bps);
  const maxAbs = Math.max(1e-9, ...rates.map(Math.abs));
  const n = series.length;
  const bw = 0.72; // bar width in viewBox units (gap between bars = 1 - bw)
  return (
    <svg viewBox={`0 0 ${n} 40`} preserveAspectRatio="none" className="h-10 w-full">
      <line x1="0" y1="20" x2={n} y2="20" stroke="var(--color-line)" strokeWidth="0.5" />
      {series.map((p, i) => {
        const h = (Math.abs(p.rate_bps) / maxAbs) * 18;
        const up = p.rate_bps >= 0;
        return (
          <rect
            key={p.t}
            x={i + (1 - bw) / 2}
            y={up ? 20 - h : 20}
            width={bw}
            height={h}
            className={up ? "fill-buy/80" : "fill-sell/80"}
          />
        );
      })}
    </svg>
  );
}

// The visual companion to the carry study (docs/research.md): funding usually
// pays the delta-neutral short, ~1 bp/8h, which annualizes into the harvest.
function FundingPanel({ funding }: { funding: Funding | null }) {
  const tone = (x: number | null | undefined) =>
    x == null ? undefined : x >= 0 ? "buy" : "sell";
  const bps = (x: number | null | undefined, d = 2) =>
    x == null ? "—" : fmtSigned(x, d);
  return (
    <section className="rounded-[3px] border border-line bg-panel/40 p-3">
      <div className="mb-3 flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-[0.18em] text-dim">
          funding / basis · carry context · last 30d
        </span>
        <span className="font-mono text-xs text-dim">USDT-M perp · 8h</span>
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
        <StatCell
          label="funding now"
          value={funding ? `${bps(funding.latest_rate_bps)} bps` : "—"}
          tone={tone(funding?.latest_rate_bps)}
        />
        <StatCell
          label="30d mean"
          value={funding ? `${bps(funding.mean_rate_bps)} bps` : "—"}
          tone={tone(funding?.mean_rate_bps)}
        />
        <StatCell
          label="% positive"
          value={funding?.pct_positive != null ? `${Math.round(funding.pct_positive * 100)}%` : "—"}
        />
        <StatCell
          label="annualized"
          value={funding?.annualized_pct != null ? `${bps(funding.annualized_pct, 1)}%/yr` : "—"}
          tone={tone(funding?.annualized_pct)}
        />
      </div>
      <div className="mt-3">
        <FundingSpark series={funding?.series ?? []} />
      </div>
    </section>
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
  const [funding, setFunding] = useState<Funding | null>(null);
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
      // funding is historical context, not the live stream: fetch it separately
      // so a missing backfill (or its 503) never blanks the live pipeline view.
      try {
        const f = await getJSON<Funding>("/funding?intervals=90");
        if (alive) setFunding(f);
      } catch {
        if (alive) setFunding(null);
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

      <FundingPanel funding={funding} />

      <footer className="flex justify-between text-[11px] text-dim">
        <span>times local · candles 1m · Δ = buy − sell volume (aggressor side) · gaps = pipeline downtime</span>
        <span className="hidden sm:inline">own pipeline: ws → redis → questdb</span>
      </footer>
    </div>
  );
}
