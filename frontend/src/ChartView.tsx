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
        font: {
          family: '"Noto Sans SC", "PingFang SC", sans-serif',
          color: '#4d5b68',
        },
        colorway: ['#0c5b4d', '#c76d36', '#56756b', '#d5a85c', '#5f7791'],
      },
      { responsive: true, displaylogo: false },
    )
    return () => {
      if (ref.current) Plotly.purge(ref.current)
    }
  }, [chart])

  return <div className="chart" ref={ref} aria-label={chart.title} />
}
