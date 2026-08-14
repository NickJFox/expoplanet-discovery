import { useMemo, useState } from 'react'

export default function LightCurve({ phase, flux, center, width }: { phase: number[]; flux: number[]; center: number; width: number }) {
  const [cursor, setCursor] = useState<number | null>(null)
  const plot = useMemo(() => {
    if (!phase.length) return null
    const [xMin,xMax] = phase.reduce(([low,high],value) => [Math.min(low,value),Math.max(high,value)], [Infinity,-Infinity])
    const sorted = [...flux].sort((a,b) => a-b)
    const yMin = sorted[Math.floor(sorted.length * .01)] ?? Math.min(...flux)
    const yMax = sorted[Math.floor(sorted.length * .99)] ?? Math.max(...flux)
    const x = (v:number) => 55 + (v-xMin)/(xMax-xMin || 1)*890
    const y = (v:number) => 25 + (yMax-v)/(yMax-yMin || 1)*270
    const phaseSpan = xMax-xMin || 1
    const binsForEvent = Math.ceil(phaseSpan * 3 / Math.max(width, phaseSpan / 800))
    const binCount = Math.min(800, Math.max(60, Math.floor(Math.sqrt(phase.length)), binsForEvent))
    const bins = Array.from({length:binCount}, () => [] as {phase:number;flux:number}[])
    phase.forEach((value, i) => {
      const bin = Math.min(binCount-1, Math.floor((value-xMin)/(xMax-xMin || 1)*binCount))
      bins[bin].push({phase:value,flux:flux[i]})
    })
    const trend = bins.filter(bin => bin.length).map(bin => {
      const values = bin.map(point => point.flux).sort((a,b) => a-b)
      return {phase:bin.reduce((sum,point) => sum+point.phase,0)/bin.length,flux:values[Math.floor(values.length/2)]}
    })
    return {xMin,xMax,yMin,yMax,x,y,trend}
  }, [phase, flux, width])
  if (!plot) return <div className="empty">No curve data available</div>
  const hovered = cursor === null ? null : phase.reduce((best, value, i) => Math.abs(value-cursor) < Math.abs(phase[best]-cursor) ? i : best, 0)
  return <div className="chart-wrap">
    <svg viewBox="0 0 1000 350" role="img" aria-label="Phase-folded relative flux light curve"
      onMouseMove={e => { const r=e.currentTarget.getBoundingClientRect(); setCursor(plot.xMin + ((e.clientX-r.left)/r.width*1000-55)/890*(plot.xMax-plot.xMin)) }} onMouseLeave={() => setCursor(null)}>
      <defs><linearGradient id="transit" x1="0" x2="1"><stop stopColor="#8b5cf6" stopOpacity=".05"/><stop offset=".5" stopColor="#8b5cf6" stopOpacity=".3"/><stop offset="1" stopColor="#8b5cf6" stopOpacity=".05"/></linearGradient></defs>
      {[0,1,2,3,4].map(i => {
        const value = plot.yMax - (plot.yMax - plot.yMin) * i / 4
        return <g key={i}><line x1="55" x2="945" y1={25+i*67.5} y2={25+i*67.5} className="grid"/><text x="48" y={29+i*67.5} className="tick" textAnchor="end">{(value*100).toFixed(2)}%</text></g>
      })}
      <rect x={plot.x(center-width/2)} y="25" width={Math.max(2,plot.x(center+width/2)-plot.x(center-width/2))} height="270" fill="url(#transit)"/>
      <line x1="55" x2="945" y1={plot.y(0)} y2={plot.y(0)} className="zero" />
      {phase.map((p,i) => <circle key={i} cx={plot.x(p)} cy={plot.y(flux[i])} r="1.45" className="point" />)}
      <path d={plot.trend.map((point,i) => `${i?'L':'M'} ${plot.x(point.phase)} ${plot.y(point.flux)}`).join(' ')} className="trend-line"/>
      <text x="500" y="338" className="axis-label">Days from predicted transit</text><text x="16" y="170" className="axis-label" transform="rotate(-90 16 170)">Brightness change</text>
      <text x="55" y="316" className="tick">{plot.xMin.toFixed(2)}</text><text x="500" y="316" className="tick" textAnchor="middle">{((plot.xMin+plot.xMax)/2).toFixed(2)}</text><text x="945" y="316" className="tick" textAnchor="end">{plot.xMax.toFixed(2)}</text>
      {hovered !== null && <g><line x1={plot.x(phase[hovered])} x2={plot.x(phase[hovered])} y1="25" y2="295" className="cursor"/><circle cx={plot.x(phase[hovered])} cy={plot.y(flux[hovered])} r="5" className="active-point"/><g transform={`translate(${Math.min(810,Math.max(60,plot.x(phase[hovered])-65))},34)`}><rect width="150" height="44" rx="7" className="tooltip-bg"/><text x="10" y="18" className="tooltip-text">day {phase[hovered].toFixed(4)}</text><text x="10" y="35" className="tooltip-text">brightness {(flux[hovered]*100).toFixed(4)}%</text></g></g>}
    </svg>
    <div className="legend"><span><i className="dot"/>Individual measurements</span><span><i className="trend-key"/>Typical brightness trend</span><span><i className="band"/>Dip found by our analysis</span></div>
    <p className="chart-help"><b>How to read this graph:</b> Day 0 is the center of the strongest repeating dip found by our search. Negative numbers are days before it, and positive numbers are days after it. Measurements from every suspected cycle are placed onto this same timeline so a repeating dip is easier to see. A consistent dip near the same day could be caused by an orbiting object passing in front of the star, but noise and stellar activity can produce similar patterns. The vertical axis shows how much the star&apos;s brightness changed from normal.</p>
  </div>
}
