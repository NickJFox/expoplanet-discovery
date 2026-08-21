import { FormEvent, useEffect, useState } from 'react'
import { Activity, AlertTriangle, ArrowRight, Database, Dices, Orbit, Search, Sparkles, Telescope } from 'lucide-react'
import LightCurve from './LightCurve'
import type { Inspection } from './types'

const label = (value:string) => value.replaceAll('_',' ').replace(/\b\w/g, c => c.toUpperCase())
const candidateLabel = (value:string) => ({
  strong_candidate: 'Strong Candidate for Planet',
  possible_candidate: 'Possible Candidate for Planet',
  weak_signal: 'Weak Signal for Planet',
  no_signal: 'No Signal for Planet',
  insufficient_data: 'Insufficient Data',
}[value] ?? label(value))
const catalogFindingLabel = (value:string, planets:number, tois:number) => {
  if (value === 'confirmed') return `NASA lists ${planets} confirmed planet${planets === 1 ? '' : 's'} for this star`
  if (value === 'candidate') return `NASA lists ${tois} planet candidate${tois === 1 ? '' : 's'} for this star`
  if (value === 'cataloged_toi') return `NASA is tracking ${tois} signal${tois === 1 ? '' : 's'} for this star`
  if (value === 'no_match') return 'Not listed in the NASA catalogs checked'
  if (value === 'unavailable') return 'NASA catalog check unavailable'
  return label(value)
}
const durationLabel = (days:number) => days < 2 ? `${(days*24).toFixed(1)} hours` : `${days.toFixed(2)} days`

export default function App() {
  const [query,setQuery]=useState(''), [data,setData]=useState<Inspection|null>(null)
  const [loading,setLoading]=useState(false), [error,setError]=useState('')
  // Render's free API sleeps when idle. Wake it in the background as soon as
  // the static page opens, without delaying or changing the initial UI.
  useEffect(() => { void fetch('/api/health').catch(() => undefined) }, [])
  async function load(path:string) { setLoading(true); setError(''); try { const r=await fetch(path); const text=await r.text(); let body; try { body=JSON.parse(text) } catch { throw new Error('The astronomy server stopped before completing the analysis. Please try again with another star.') } if(!r.ok) throw new Error(body.detail||'The request failed'); setData(body) } catch(e) { setError(e instanceof Error?e.message:'The request failed') } finally { setLoading(false) } }
  function submit(e:FormEvent){e.preventDefault(); if(query.trim()) load(`/api/targets/${encodeURIComponent(query.trim())}/inspect`)}
  const tone=data?.analysis.classification==='strong_candidate'?'strong':data?.analysis.classification==='possible_candidate'?'possible':'quiet'
  return <main>
    <nav><div className="brand"><span className="brand-mark"><Orbit size={20}/></span>Exoplanet Finder</div><div className=""><span/>Live astronomy data</div></nav>
    <header>
      <h1>Search the stars<br/><em>discover new worlds</em></h1>
      <h3>How it works</h3>
      <p>Search for a star or choose one at random.</p>
      <p>Observations are retrieved from NASA’s TESS telescope, which measure how a star’s brightness changes over time.</p>
      <p>The observations are plotted in a light curve graph, and then analyzed for repeating dips of brightness over time that may occur when an orbiting planet passes in front of its star.</p>
      <p>The analysis is then compared with NASA’s catalog of known planets and candidates to identify potential new expoplanets!</p>
      <form onSubmit={submit}><Search size={20}/><input value={query} onChange={e=>setQuery(e.target.value)} aria-label="Star name or TIC ID" placeholder="Try any star name like TOI-1077 or Wasp-46"/><button disabled={loading}>Inspect Star</button></form>
      <p className="search-hint">Hint: Try a star with no known exoplanets to see whether the latest data reveals a potential new discovery.</p>
      <p className="random-label">Don&apos;t know any star names? That&apos;s fine! We can randomly generate one for you.</p>
      <button className="random" onClick={()=>load('/api/targets/random')} disabled={loading}><Dices size={16}/>{loading?'Reading the sky…':'Surprise me with a random star'}</button>
      {error&&<div className="error"><AlertTriangle size={18}/>{error}</div>}
    </header>

    {!data&&!loading&&<section className="features"><article><Telescope/><b>Inspect</b><span>NASA space-telescope observations</span></article><article><Activity/><b>Measure</b><span>Independent transit signal scoring</span></article><article><Database/><b>Compare</b><span>Confirmed planets and TOI candidates</span></article></section>}
    {loading&&<section className="loading"><div className="loader"/><h2>Gathering data</h2><p>Plotting light curve graph and analyzing patterns…</p></section>}
    {data&&!loading&&<section className="results">
      <div className="result-heading"><div><h2>{data.target.resolved_name}</h2><p>TIC {data.target.tic_id}</p></div><div className="result-comparison"><div className={`result-card signal-card ${tone}`}><small>OUR GRAPH ANALYSIS</small><div><b>{candidateLabel(data.analysis.classification)}</b></div></div><div className="result-card nasa-card"><small>NASA&apos;S OFFICIAL CATALOG</small><b>{catalogFindingLabel(data.catalog.status,data.catalog.planets.length,data.catalog.tois.length)}</b><span>{data.catalog.planets.length} confirmed · {data.catalog.tois.length} candidate records</span></div>{data.catalog.planets.length>0&&!['strong_candidate','possible_candidate'].includes(data.analysis.classification)&&<p className="comparison-note"><AlertTriangle size={16}/><span><b>These results do not conflict.</b> Our graph only checks for planets that cross in front of the star from our viewpoint. Not finding a strong transit-like signal does not mean no planets exist; NASA may have confirmed them using another method.</span></p>}</div></div>
      <div className="chart-card"><div className="card-head"><div><h3>Brightness around the possible transit</h3><p>Our search found its strongest repeating pattern every {data.detection.period_days.toFixed(2)} days. The highlighted area shows the strongest dip.</p></div><span className="pill">{data.observation_count.toLocaleString()} usable brightness measurements</span></div><LightCurve phase={data.curve.phase} flux={data.curve.flux} center={data.analysis.phase_center} width={data.analysis.duration_phase}/></div>
      <div className="metrics metrics-three"><article><small>SIGNAL CLARITY</small><b>{data.analysis.signal_to_noise.toFixed(1)} <i>SNR</i></b><span>How clearly the combined dip stands out from normal measurement noise. A higher value means the dip is easier to distinguish from random noise.</span></article><article><small>BRIGHTNESS DIP</small><b>{(data.analysis.depth*100).toFixed(3)}<i>%</i></b><span>How much dimmer the star became in the highlighted area ({data.analysis.depth_ppm.toLocaleString()} ppm). A larger orbiting object would usually produce a deeper dip. Star size, crossing angle, and nearby light can also affect the measured depth.</span></article><article><small>ESTIMATED DIP LENGTH</small><b>{durationLabel(data.analysis.duration_phase)}</b><span>How long the highlighted dimming event appears to last.</span></article></div>
    </section>}
    <footer>Transit Lens is an educational screening tool. Candidate scores require human review and follow-up observations.</footer>
  </main>
}
