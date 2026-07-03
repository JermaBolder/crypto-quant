"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

// lightweight-charts renders UTCTimestamp as UTC; the shift makes the time
// axis show the viewer's local clock. Display-only — data stays UTC.
const TZ_SHIFT_S = -new Date().getTimezoneOffset() * 60;

const BUY = "#33c6a4";
const SELL = "#e4685f";

type Bar = { t: string; o: number; h: number; l: number; c: number; vol: number; delta: number };

export default function Chart({ bars }: { bars: Bar[] }) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const deltaRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const fittedRef = useRef(false);

  // create once; the chart lives OUTSIDE React state — imperative canvas
  // objects re-created on every poll would leak and flicker
  useEffect(() => {
    const box = boxRef.current;
    if (box === null) return;
    const chart: IChartApi = createChart(box, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: "#7c8aa5",
        // canvas text can't resolve CSS variables — concrete stack only
        fontFamily: "ui-monospace, 'SF Mono', Menlo, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#181f2e" },
        horzLines: { color: "#181f2e" },
      },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#202a3c" },
      rightPriceScale: { borderColor: "#202a3c" },
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: BUY,
      downColor: SELL,
      wickUpColor: BUY,
      wickDownColor: SELL,
      borderVisible: false,
    });
    // delta histogram shares the pane but owns the bottom band, the classic
    // volume-under-candles split
    const delta = chart.addSeries(HistogramSeries, {
      priceScaleId: "delta",
      priceFormat: { type: "volume" },
    });
    chart.priceScale("delta").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.24 } });

    chartRef.current = chart;
    candlesRef.current = candles;
    deltaRef.current = delta;
    return () => {
      chartRef.current = null;
      candlesRef.current = null;
      deltaRef.current = null;
      chart.remove();
    };
  }, []);

  // data path: setData on the existing series; React never re-renders the canvas
  useEffect(() => {
    const candles = candlesRef.current;
    const delta = deltaRef.current;
    if (candles === null || delta === null || bars.length === 0) return;

    const ts = (iso: string) => (Math.floor(Date.parse(iso) / 1000) + TZ_SHIFT_S) as UTCTimestamp;
    candles.setData(
      bars.map((b) => ({ time: ts(b.t), open: b.o, high: b.h, low: b.l, close: b.c })),
    );
    delta.setData(
      bars.map((b) => ({
        time: ts(b.t),
        value: b.delta,
        color: b.delta >= 0 ? `${BUY}59` : `${SELL}59`, // 0x59 ≈ 35% alpha
      })),
    );
    if (!fittedRef.current) {
      // set the view once; later polls must not yank the user's zoom.
      // Open on the last ~2h of BARS; anything older stays scrollable left.
      // With autoSize the chart is 0px wide until ResizeObserver measures the
      // box — fitting then is a silent no-op that leaves phantom slots on the
      // left. Defer the fit until the time scale reports a real width.
      fittedRef.current = true;
      const n = bars.length;
      // fitContent is applied on the chart's NEXT internal frame; issued in
      // the same task as setData it can get dropped. So: request the fit,
      // check the result a frame later, retry if phantom slots remain.
      const fit = (tries: number) => {
        const chart = chartRef.current;
        if (chart === null || tries > 10) return;
        const scale = chart.timeScale();
        if (n > 120) {
          scale.setVisibleLogicalRange({ from: n - 120, to: n + 2 });
        } else {
          scale.fitContent();
        }
        requestAnimationFrame(() => {
          const r = chartRef.current?.timeScale().getVisibleLogicalRange();
          if (!r || r.from < -5) fit(tries + 1);
        });
      };
      fit(0);
    }
  }, [bars]);

  return <div ref={boxRef} className="h-105 w-full" />;
}
