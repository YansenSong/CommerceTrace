import { useEffect, useRef } from 'react'
import Plotly from 'plotly.js-dist-min'

import type { Chart } from './types'

export function ChartView({ chart }: { chart: Chart }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!ref.current) return
    void Plotly.react(
      ref.current,
      chart.figure.data ?? [],
      {
        ...chart.figure.layout,
        autosize: true,
        margin: { l: 48, r: 20, t: 52, b: 48 },
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        font: { family: 'Inter, "Noto Sans SC", sans-serif', color: '#dce8ee' },
      },
      { responsive: true, displaylogo: false },
    )
    return () => {
      if (ref.current) Plotly.purge(ref.current)
    }
  }, [chart])

  return <div className="chart" ref={ref} aria-label={chart.title} />
}
