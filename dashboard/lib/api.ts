// The dashboard's ONLY door to the backend. Everything goes through the
// FastAPI layer (see ../api.py) — the browser never talks to QuestDB.
export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Bar = {
  t: string; // ISO timestamp of the 1m bar
  o: number; h: number; l: number; c: number;
  vol: number;   // BTC traded in the bar
  delta: number; // buy volume - sell volume (aggressor side)
};

export type Stats = {
  last_price: number | null;
  vol_1h: number;
  buy_share_1h: number | null; // 0..1, buy volume share of the last hour
  delta_1h: number;
  trades_per_min: number;
};

export type Health = {
  ok: boolean;
  latest_trade: string | null;
  age_s: number | null; // seconds since the last trade hit the DB
};

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json();
}
