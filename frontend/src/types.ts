export type Inspection = {
  target: { input: string; tic_id: string; resolved_name: string }
  label: string
  detection: { period_days: number; duration_days: number; transit_time: number; bls_power: number }
  observation_count: number
  curve: { phase: number[]; flux: number[] }
  analysis: {
    classification: string; score: number; signal_to_noise: number; depth: number; depth_ppm: number
    phase_center: number; duration_phase: number; in_transit_points: number; noise: number; reasons: string[]; caveat: string
  }
  catalog: { status: string; host_name: string | null; planets: Record<string, string>[]; tois: Record<string, string>[]; message?: string }
  data_source: string
  is_synthetic: boolean
}
