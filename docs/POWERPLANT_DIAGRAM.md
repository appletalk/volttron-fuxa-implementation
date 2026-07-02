# Sunfield Solar — Power Plant Diagram

Mermaid diagrams describing the `Sunfield Solar — 150 MWac utility-scale PV + DC-coupled BESS`
plant and its place in the campus microgrid. See [POWERPLANT_SPEC.md](POWERPLANT_SPEC.md) for
the full physics model, point list, and FUXA view design.

## Electrical single-line (DC-coupled BESS)

The headline topology: PV array and a **DC-coupled** battery share the inverter blocks, so the
battery charges from DC **clipping** energy at peak (SOC rises) and discharges through the same
inverters to firm export on a passing cloud.

```mermaid
flowchart LR
    SUN["☀ SUN<br/>irradiance_wm2<br/>0–1200 W/m²"]

    subgraph DC["DC side — 187.5 MWdc STC (ILR 1.25)"]
        PV["PV ARRAY<br/>single-axis tracked<br/>pv_dc_power_mw"]
        BUS(("DC bus"))
        BESS["BESS<br/>37.5 MW / 150 MWh · 4 h<br/>DC/DC converter<br/>SOC 10–90%"]
    end

    subgraph AC["AC side"]
        INV["6× INVERTER BLOCKS (feeders)<br/>33 × SMA SC4600 UP-US on MVPS-S2<br/>25 MWac/block · clips flat at 150 MW · CEC η 98.5%<br/>inverter1..6_status"]
        XFMR["Inverter transformers<br/>690 V → 34.5 kV"]
        FEED["34.5 kV COLLECTION<br/>6 feeders × 5–6 × SC4600 UP<br/>≈462 A < 600 A"]
        GSU["MAIN GSU<br/>34.5 / 100 kV"]
        BRK{"POI BREAKER<br/>main_breaker_status<br/>closed / open"}
        POI["POI METERING<br/>100 kV · 60 Hz<br/>150 MWac · ±49.5 MVAR"]
    end

    GRID["⚡ GRID<br/>100 kV"]

    SUN -->|solar fuel| PV
    PV -->|DC| BUS
    BESS <-->|charge − / discharge +| BUS
    BUS -->|DC| INV
    INV --> XFMR --> FEED --> GSU --> BRK --> POI --> GRID

    classDef dc fill:#1e3a5f,stroke:#4a90d9,color:#e6f0fa
    classDef ac fill:#5f4a1e,stroke:#d9a94a,color:#faf3e6
    classDef grid fill:#3a1e5f,stroke:#9a4ad9,color:#f0e6fa
    class PV,BUS,BESS dc
    class INV,XFMR,FEED,GSU,BRK,POI ac
    class SUN,GRID grid
```

## DC-coupled BESS dispatch (auto firming law)

```mermaid
flowchart TD
    G["Irradiance G"] --> PVDC["PV DC power<br/>(NOCT temp derate)"]
    PVDC --> BASE["Slow/firming baseline P_ref<br/>fast-attack, slow-decay"]
    PVDC --> NEED
    BASE --> NEED["target_export = min(P_ref, setpoint, live AC cap)<br/>P_dc_needed = target / η_inv"]
    NEED --> BATT{"P_batt = clamp(P_dc_needed − P_dc_pv, ±37.5)"}

    BATT -->|"PV > cap (clip)"| CHG["CHARGE (−)<br/>captures clipping energy<br/>SOC ↑, clipping_loss → 0"]
    BATT -->|"fast cloud, PV sags"| DIS["DISCHARGE (+)<br/>firms export<br/>SOC ↓"]
    BATT -->|"smooth ramp"| IDLE["IDLE ≈ 0<br/>SOC preserved"]

    CHG --> SOC["battery_soc_pct<br/>SOC ±= (P_batt/η_ow)·dt/(3600·E_cap)·100<br/>usable 10–90%"]
    DIS --> SOC
    IDLE --> SOC

    classDef pv fill:#1e3a5f,stroke:#4a90d9,color:#e6f0fa
    classDef batt fill:#1e5f3a,stroke:#4ad98a,color:#e6faf0
    class G,PVDC,BASE,NEED pv
    class BATT,CHG,DIS,IDLE,SOC batt
```

## Campus microgrid coupling

The solar+BESS plant feeds a campus bus; the district-heating substation's circulation pumps are
an electrical load on it. A pump trip drops campus load → plant export to the grid rises; at night
solar = 0 so the campus imports from the grid.

```mermaid
flowchart LR
    SOLAR["SOLAR + BESS PLANT<br/>150 MWac · Sunfield<br/>plant_active_power_mw"]
    BUS(("CAMPUS BUS"))
    GRID["⚡ UTILITY GRID<br/>grid_power_mw<br/>+ export / − import"]

    subgraph LOADS["Campus loads"]
        BASE["Base load<br/>10 MW night → 28 MW midday"]
        SUB["HEAT SUBSTATION<br/>circ pumps (P ∝ Hz³)<br/>~3 MW/pump + 1 MW makeup"]
    end

    SOLAR --> BUS
    GRID <-->|import / export| BUS
    BUS --> BASE
    BUS --> SUB

    classDef gen fill:#1e5f3a,stroke:#4ad98a,color:#e6faf0
    classDef load fill:#5f4a1e,stroke:#d9a94a,color:#faf3e6
    classDef grid fill:#3a1e5f,stroke:#9a4ad9,color:#f0e6fa
    class SOLAR gen
    class BASE,SUB load
    class GRID grid
    class BUS gen
```

## Day-in-the-life arc (24 h)

```mermaid
flowchart LR
    N1["NIGHT<br/>G=0 · inverters off<br/>trackers stowed · BESS idle<br/>campus imports"]
    SR["SUNRISE ~06:00<br/>trackers −60→0<br/>Plant MW climbs"]
    PK["PEAK ~solar noon<br/>DC > 150 · AC pins at 150<br/>BESS charges from clip<br/>SOC ↑ · clipping_loss ≈ 0"]
    CL["SHALLOW CLOUD ~13:00<br/>PV sags ~20 MW<br/>BESS discharges → Plant MW holds 150<br/>SOC dips"]
    EV["EVENING ~18:00<br/>trackers 0→+60→stow<br/>BESS covers shoulder"]
    N2["NIGHT<br/>STATCOM ±49.5 MVAR<br/>campus imports"]

    N1 --> SR --> PK --> CL --> EV --> N2 --> N1
```
