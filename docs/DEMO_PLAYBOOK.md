# Live MCP Demo Playbook — Sunfield Campus

A ~10-minute live demo: the operator watches FUXA (`http://localhost:1881/home`)
while Claude drives both plants through the `volttron-fuxa` MCP, narrating each
action before triggering it. A guide, not a cage — interrupt with "what if X?"
any time.

**Pacing.** Every change travels MCP → VOLTTRON (5 s scrape) → bridge → FUXA poll,
plus the sim's ~5 s first-order lag, so each beat takes **~5–10 s to fully land**;
faults cascade over ~3–8 s. Narrate the action, then watch it settle. The day is
time-lapsed (`T_DAY ≈ 5 min` = 24 h).

**Reading offset values on tiles:** `grid_power − 100` = MW (+export/−import);
`battery_power − 50` and `mvar − 50` = MW/MVAR; `tracker − 60` = °; `POI kV` and
`Hz` are ÷10; `PF` is ÷100. (The dials already account for these.)

---

## Pre-flight
- Stack up (`docker compose up -d`, wait for 81 points) and dashboard pushed.
- `reset_sim(plant="power_plant")` + `load_scenario("peak_load", plant="heat_station")`
  (gives the campus a visible district-heating load).
- Open the **Campus Overview** (landing/home view).

## 1 · Orient — *Campus Overview*
> "One site, two coupled plants: a 150 MW solar + 37.5 MW/150 MWh BESS plant
> supplies a campus bus; the district-heating substation is an electrical load on
> that same bus. Right now solar covers the whole campus and exports the surplus."

Watch: **Solar Gen / Campus Load / Grid (export)** dials, the energy-flow diagram
(SOLAR+BESS → BUS → buildings + district heating + grid tie), Solar Share 100%.

## 2 · Run the day & scrub time — *Solar Plant*
> "Let me run a day and jump to solar noon."
- `load_scenario("day_in_the_life", plant="power_plant")`
- `set_sim_point("time_set_hhmm", 1200, plant="power_plant")`

Watch: clock → **12:00**; **DC climbs past 150, AC clips flat at 150**, the
DC-coupled battery banks the clip (charging, SOC rising), clip ≈ 0.

> "Pause it… now jump to night."
- `set_sim_point("time_rate", 0, plant="power_plant")`  → freeze
- `set_sim_point("time_set_hhmm", 2200, plant="power_plant")`  → 22:00

Watch: clock **22:00**, the plant goes **dark** — inverters off, export 0, sun dim.

> "Back to morning, and play."
- `set_sim_point("time_set_hhmm", 800, plant="power_plant")`
- `set_sim_point("time_rate", 1, plant="power_plant")`

## 3 · Battery firms a passing cloud — *Solar Plant* (or *BESS Detail*)
> "A cloud passes — watch the battery discharge to hold export, then recover."
- `load_scenario("cloud_passing", plant="power_plant")`  *(deep dip, ~35 s arc)*

Watch: irradiance dips, PV/AC sag, **battery discharges (green, +MW) to soften the
dip**, SOC dips and recovers. The BESS-Firming / SOC-vs-Power trend tells it best.

## 4 · Faults & protection — *Solar Plant* / *One-Line*
> "Trip an inverter block."
- `inject_fault("inverter2", True, plant="power_plant")`

Watch: inverter **#2 turns red**, `Inverter Fault` lamp, **Plant MW steps down ~25 MW (to ~125)**,
surplus PV charges the battery. Then: `inject_fault("inverter2", False, plant="power_plant")`.

> "Grid under-frequency — the plant must *ride through*, not trip."
- `load_scenario("grid_fault", plant="power_plant")`  *(moderate, 59.3 Hz)*

Watch: **Hz** dial off the green band, `Grid Under-Freq` lamp, breaker **stays
closed**, export continues.

> "Now a severe, sustained excursion — protection opens the breaker."
- `inject_fault("grid_severe", True, plant="power_plant")`

Watch: ~7 s later the **breaker trips** (red gap), export → 0, `Breaker Trip` +
`Grid Over-Voltage` lamps. (The Hz glides down for ~3–4 s to cross 58.5, then a 4 s
sustained-excursion clock latches the trip — so budget ~7 s from the inject.)
Recover: `reset_sim(plant="power_plant")`  *(clears the **latched** trip — a bare
`breaker_cmd=1` will NOT re-close while the trip is latched).*

## 5 · The microgrid tie — *Campus Overview*
> "Here's the coupling: trip a **substation** pump and watch the **plant's grid
> export rise** — less campus load, more surplus to the grid."
- `inject_fault("circ_pump1", True, plant="heat_station")`

Watch (Campus view): **District Heating load drops (~3 MW) → Campus Load drops →
Grid export rises ~3 MW**. Restore: `inject_fault("circ_pump1", False, plant="heat_station")`.

## 6 · Reset
- `reset_sim(plant="power_plant")` + `reset_sim(plant="heat_station")`

---

## Quick reference
- **power_plant scenarios:** `normal, day_in_the_life, sunrise, evening, peak_sun,
  cloud_passing, curtailment, inverter_trip, grid_fault, bess_dispatch, low_soc`
- **power_plant faults:** `inverter1..6, grid_under_freq, grid_severe,
  dc_ground_fault, comms_loss, battery_over_temp`
- **heat_station faults:** `circ_pump1, circ_pump2, makeup_pump, primary`
- **Controls (`set_sim_point`, RAW values):** `power_setpoint_mw`, `mvar_setpoint`
  (+50), `voltage_setpoint_kv` (×10), `bess_mode` (0 auto/1 charge/2 discharge),
  `bess_power_cmd_mw` (+50), `breaker_cmd` (0/1), `tracker_enable` (0/1),
  `time_rate` (0 pause/1 play/N fast), `time_set_hhmm` (0000–2359; 9999 = no-op).
- **Spare crowd-pleasers:** `bess_dispatch` (force +25 MW to the grid),
  `curtailment` (cap export at 90 MW, surplus charges the BESS then curtails PV),
  `low_soc` (negative-space proof — depleted battery can't firm a cloud).
