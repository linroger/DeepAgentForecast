# DOSSIER — NVIDIA Data-Center AI Accelerator Revenue Share Through 31 Dec 2027

**Central question:** By 31 Dec 2027, will NVIDIA retain ≥75% revenue share of the data-center AI accelerator (training + inference silicon) market, or will hyperscaler custom ASICs (Google TPU, AWS Trainium, Microsoft Maia, Meta MTIA) plus AMD Instinct erode NVIDIA below 75%?
**As-of date:** 2026-06-15 · **Horizon:** 18.5 months

---

## 1. Executive Thesis

**The answer is definition-dependent, and that dependency is the forecast.** On the *merchant-silicon* denominator — the share metric NVIDIA, sell-side desks, and most market-sizing reports default to — NVIDIA is **likely (≈68%)** to remain ≥75% through end-2027. On the *all-accelerators-including-captive-ASICs-at-internal-transfer-value* denominator, NVIDIA is **more likely than not (≈60%)** to have *already* fallen below 75% by the resolution date, because Google TPU, AWS Trainium, and Meta MTIA captive volumes are large, growing faster than the merchant market, and largely invisible to merchant accounting.

Blending the two framings against the most probable resolution convention (a merchant-leaning but ASIC-aware read, which is how authoritative trackers such as SemiAnalysis and TrendForce increasingly present the data [T2]), my single calibrated estimate is:

> **P(NVIDIA ≥ 75% data-center AI accelerator revenue share at 31 Dec 2027) ≈ 0.62.**

The thesis rests on three load-bearing claims. First, NVIDIA's erosion is real but **slow and front-loaded into inference**, where ASICs and AMD compete best; training at NVLink rack scale remains a near-monopoly that anchors the highest-ASP revenue [T2]. Second, the binding constraint on *everyone* — NVIDIA, AMD, and the ASIC designers alike — is **CoWoS advanced packaging and HBM**, not demand or design; TSMC's allocation decisions effectively cap how fast challengers can ship [T1]. Third, the **CUDA + NVLink + Spectrum-X full-stack moat** keeps switching costs high enough that even motivated hyperscalers migrate only the workloads where the economics are overwhelming. The downside case is not that any single rival "beats" NVIDIA, but that the *aggregate* of four captive ASIC programs plus AMD crosses a definitional threshold while NVIDIA's own units stay supply-capped.

---

## 2. Layered Claims

**Known (high-confidence, multiple independent sources, A-tier):**
- NVIDIA holds ~80–90% of *merchant* data-center accelerator revenue as of mid-2026, anchored by Blackwell GB200/GB300 NVL72 volume shipments [T1, NVIDIA reporting; SemiAnalysis T2].
- TSMC fabricates and CoWoS-packages essentially all leading-edge AI accelerators — NVIDIA, AMD, Google, AWS, Broadcom designs — making it the universal chokepoint [T1].
- US BIS export controls have repeatedly tightened (2022, 2023, Dec 2024, Apr 2025), now requiring licenses for H20-class and AMD MI308 China sales, materially curbing China accelerator revenue [T1, BIS rulings].
- Google TPU is the most mature hyperscaler ASIC and is sold/allocated externally (notably to Anthropic), not purely captive [T2].

**Inferred (reasoned from converging evidence, B-tier confidence):**
- Custom ASIC volumes are growing faster than the merchant market, concentrated in inference and recommendation workloads where cost-per-token dominates the buy decision [T2, SemiAnalysis/Morgan Stanley].
- AMD reaches high-single to low-double-digit *merchant* share by 2027 on MI350/MI400 + ROCm maturation, but does not by itself threaten the 75% line [T2].
- HBM4 supply (SK Hynix-led) and CoWoS-L capacity will remain sold-out through 2027, keeping NVIDIA unit growth supply- rather than demand-limited [T2, TrendForce].

**Assumed (working premises, must be flagged):**
- The resolution authority uses a *predominantly merchant* denominator that does not fully impute captive ASICs at internal transfer value. (If false, flip to downside.)
- No demand collapse / AI-capex recession before end-2027 that would reset relative shares.
- No Taiwan Strait disruption to TSMC.

**Unknown (genuine uncertainty):**
- Whether OpenAI's Broadcom-co-designed inference silicon reaches *volume* before end-2027 (program exists; timing to scale unclear).
- Whether the resolution source counts Huawei Ascend (export-controlled China substitution) in the global denominator at all.
- The precise internal transfer value hyperscalers would assign to captive TPU/Trainium/MTIA — there is no public standard.

---

## 3. Situation Brief

As of mid-2026, NVIDIA commands an estimated 80–90% of merchant data-center AI accelerator revenue, anchored by the Blackwell ramp (GB200/GB300 NVL72 racks shipping in volume to hyperscalers and neoclouds) and the CUDA software moat. The competitive question for end-2027 is whether custom-ASIC volumes plus AMD Instinct (MI300/MI350/MI400) can pull NVIDIA below 75%.

**Context.** Demand is driven by frontier-model training (OpenAI, Anthropic, Google DeepMind, Meta, xAI) and a rapidly expanding inference base. The entire stack depends on TSMC leading-edge nodes (N4/N3/N2), CoWoS advanced packaging, and HBM3E/HBM4 from SK Hynix, Samsung, and Micron. US export controls cap China's access to leading-edge AI chips and ASML EUV tools, reshaping demand geography and pushing China toward Huawei Ascend and SMIC.

**Dynamics.** Two opposing forces define the trajectory: (1) NVIDIA's full-stack lock-in (CUDA, NVLink/NVSwitch, Mellanox/Spectrum-X networking, annual cadence) plus first call on scarce CoWoS and HBM; versus (2) hyperscalers' structural incentive to cut cost-per-token and supply risk by shifting inference — and increasingly some training — to internal ASICs co-designed with Broadcom and Marvell. Definitional sensitivity is decisive: if captive TPU/Trainium/Maia/MTIA volumes count in the denominator at internal transfer value, NVIDIA share compresses faster than in a merchant-only view.

**Fault lines.** Denominator definition (merchant vs. all-accelerators); training (NVIDIA/AMD-dominated, NVLink-scale) vs. inference (ASIC-erodable); CUDA lock-in vs. open stacks (ROCm, Triton, JAX/XLA, MLIR); supply allocation of scarce CoWoS-L/HBM4; US–China decoupling; hyperscaler vertical integration vs. dependence on NVIDIA for frontier-scale training; HBM oligopoly pricing power vs. accelerator-vendor margins.

---

## 4. Actors & Incentives

**The incumbent.** *NVIDIA* (grade A1) is the central subject: its revealed strategy is to lock supply and cadence (Hopper→Blackwell→Rubin annual) to keep rivals subscale, while publicly framing ASICs as complementary and the market as expanding [T2]. *Jensen Huang* (A2) is the agenda-setter, personifying the "AI factories / boundless demand" narrative; key-man and geopolitical exposure are his constraints.

**The merchant challenger.** *AMD* (A2) under *Lisa Su* (A2) is the only credible non-NVIDIA merchant GPU, betting on open ROCm, high-HBM Instinct parts for memory-bound inference, and MI400/Helios rack-scale to reach NVLink parity. Revealed dependence: a handful of hyperscaler design wins (Microsoft, Meta, Oracle) and a ROCm catch-up race.

**The ASIC arms dealers.** *Broadcom* (A2) is the primary beneficiary of the ASIC shift — co-designing Google TPU and Meta MTIA, and now OpenAI's inference chip — but revenue is concentrated in Google/Meta and tied to hyperscaler capex. *Marvell* (B2) is the #2, leaning on AWS Trainium and Microsoft Maia. Their interest is to maximize the very erosion NVIDIA resists.

**The hyperscalers (buyers-turned-builders).** All run a coopetition strategy — publicly multi-silicon, privately migrating economical workloads off NVIDIA. *Google* (A2): most mature ASIC (TPU v5/v6/v7), sells externally, aims to displace NVIDIA for its own and partner (Anthropic) workloads. *AWS* (A2): Trainium2/3 + Project Rainier for Anthropic, pushing to escape GPU rental economics. *Microsoft* (A2): Maia is years behind, so it remains structurally NVIDIA-dependent near term. *Meta* (A2): MTIA for recommendation/inference while still buying NVIDIA at vast scale for Llama training. *Oracle* (B1) and *xAI* (B1): near-total NVIDIA dependence, no in-house hedge.

**The supply chokepoints.** *TSMC* (A1) gatekeeps via CoWoS allocation; *SK Hynix* (A2) leads HBM with NVIDIA-locked multiyear supply; *Samsung* (B2) and *Micron* (B1) are catch-up HBM sources; *ASML* (A1) is the EUV monopoly upstream of all of it.

**Demand drivers & regulators.** *OpenAI* (A2) is the largest single demand shaper and a custom-silicon aspirant (Broadcom). *Anthropic* (B1) uniquely spans TPU/Trainium/GPU. *BIS* (A2) reshapes geography; *China MIIT* (B2), *Huawei* (B2), and *SMIC* (B2) drive walled-off domestic substitution that may or may not enter the global denominator.

**Stated-vs-revealed pattern:** every hyperscaler publicly champions "complementary multi-silicon" while revealed allocation shows a deliberate march to shift the *erodable* (inference) layer off NVIDIA. NVIDIA mirror-images this — "complementary ASICs" publicly, supply-preemption privately.

---

## 5. Actor Relationship Graph (directed, typed)

| Source | → | Target | Type | Sign | Strength | Tier |
|---|---|---|---|---|---|---|
| NVIDIA | → | TSMC | DEPENDS_ON | neutral | high | A1 |
| NVIDIA | → | SK Hynix | DEPENDS_ON | neutral | high | A2 |
| AMD | → | TSMC | DEPENDS_ON | neutral | high | A2 |
| TSMC | → | ASML | DEPENDS_ON | neutral | high | A1 |
| NVIDIA | → | AMD | COMPETES_WITH | rival | high | A1 |
| NVIDIA | → | Google | COMPETES_WITH | rival | high | A2 |
| NVIDIA | → | AWS | COMPETES_WITH | rival | medium | B1 |
| NVIDIA | → | Huawei | COMPETES_WITH | rival | medium | B2 |
| Broadcom | → | Marvell | COMPETES_WITH | rival | high | A2 |
| SK Hynix | → | Samsung | COMPETES_WITH | rival | high | A2 |
| SK Hynix | → | Micron | COMPETES_WITH | rival | high | A2 |
| Broadcom | → | Google | PARTNERS_WITH (co-design TPU) | ally | high | A2 |
| Broadcom | → | Meta | PARTNERS_WITH (co-design MTIA) | ally | high | B1 |
| Marvell | → | AWS | PARTNERS_WITH (Trainium) | ally | high | B1 |
| Broadcom | → | OpenAI | PARTNERS_WITH (inference chip) | ally | medium | B2 |
| AWS | → | Anthropic | PARTNERS_WITH (Rainier/Trainium) | ally | high | A2 |
| Google | → | Anthropic | PARTNERS_WITH (TPU) | ally | high | A2 |
| Microsoft | → | OpenAI | PARTNERS_WITH | ally | high | A1 |
| NVIDIA | → | CoreWeave | PARTNERS_WITH (invest + allocation) | ally | high | B1 |
| CoreWeave | → | NVIDIA | DEPENDS_ON | neutral | high | A2 |
| Oracle | → | NVIDIA | DEPENDS_ON | neutral | high | B1 |
| xAI | → | NVIDIA | DEPENDS_ON | neutral | high | B1 |
| Microsoft | → | NVIDIA | DEPENDS_ON | neutral | high | A2 |
| Meta | → | NVIDIA | DEPENDS_ON | neutral | high | A2 |
| Google | → | Broadcom | DEPENDS_ON | neutral | high | B1 |
| Google | → | TSMC | DEPENDS_ON | neutral | high | B1 |
| AWS | → | TSMC | DEPENDS_ON | neutral | high | B1 |
| Anthropic | → | Google | DEPENDS_ON | neutral | high | B1 |
| Anthropic | → | AWS | DEPENDS_ON | neutral | high | A2 |
| Huawei | → | SMIC | DEPENDS_ON | neutral | high | B2 |
| BIS | → | NVIDIA | REGULATES | neutral | high | A1 |
| BIS | → | ASML | REGULATES | neutral | high | A2 |
| BIS | → | Huawei | OPPOSES | rival | high | A1 |
| BIS | → | SMIC | OPPOSES | rival | high | A2 |
| China MIIT | → | Huawei | ALLY_OF | ally | high | B2 |
| China MIIT | → | NVIDIA | OPPOSES | rival | medium | B2 |
| TSMC | → | NVIDIA | FABRICATES_FOR | ally | high | A1 |
| SK Hynix | → | NVIDIA | SUPPLIES | ally | high | A2 |
| SemiAnalysis | → | NVIDIA | INFLUENCES (analysis) | neutral | medium | B2 |
| Morgan Stanley | → | NVIDIA | INFLUENCES (estimates) | neutral | medium | B2 |

**Read of the graph:** NVIDIA sits at the center of a dense web where the *same* nodes (Google, AWS, Microsoft, Meta) are simultaneously its largest `DEPENDS_ON` customers *and* its `COMPETES_WITH` rivals — the structural definition of coopetition. The erosion vector flows through the Broadcom/Marvell `PARTNERS_WITH` edges into the hyperscaler ASICs. The defensive moat flows through the `DEPENDS_ON → TSMC/SK Hynix` edges, which NVIDIA monopolizes first.

---

## 6. Drivers & Indicators

| Driver | Direction if it fires | Leading indicator to watch | Tier |
|---|---|---|---|
| CoWoS-L / HBM4 capacity expansion at TSMC | More expansion → **helps challengers** (NVIDIA loses its scarcity moat) | TSMC quarterly CoWoS capacity guidance; HBM4 qual dates | T2 |
| TPU v7 (Ironwood) external availability | Wider external TPU → **erodes** NVIDIA | Anthropic/Apple/third-party TPU deal announcements | T2 |
| AWS Trainium3 ramp + Rainier scale | Larger captive training → **erodes** (esp. on captive denominator) | Anthropic training-cluster disclosures; AWS Neuron adoption | T2 |
| AMD MI400/Helios launch + ROCm maturity | Credible #2 → **erodes** merchant share | MI400 GA timing; ROCm framework parity benchmarks | T2 |
| NVIDIA Rubin (R100) on-cadence 2027 ramp | On-time → **defends** (extends NVLink/Spectrum-X lead) | GTC 2026/2027 cadence reaffirmation [observed Mar 2026] | T2 |
| BIS export-control revisions | Tighter → shrinks China-bound NVIDIA revenue, **mixed** for global share | New BIS rules on B-series/H20 China SKUs | T1 |
| OpenAI–Broadcom silicon reaching volume | Volume → **erodes** (new captive entrant) | Broadcom XPU customer count + revenue guide | T2 |
| AI-capex sustainability / circular-financing stress | Pullback → freezes relative shares, **defends incumbent** | Neocloud (CoreWeave) refinancing; hyperscaler capex guides | T2 |
| Power/datacenter availability | Binding → caps total deployment, **neutral-to-defends** | Multi-GW datacenter (Prometheus/Hyperion/Stargate) timelines | T2 |

**Tripwires that would force a forecast revision:**
- *Toward downside (NVIDIA <75%):* A major resolution-grade tracker publishes a share series that imputes captive ASICs at transfer value AND it shows NVIDIA already <80% on that basis → cut P to ~0.45.
- *Toward upside (NVIDIA ≥75% comfortably):* TSMC CoWoS stays NVIDIA-allocated-first through 2027 while AMD MI400 slips or ROCm stalls → raise P to ~0.78.

---

## 7. Scenarios

**Base case — "Slow erosion, line holds on the merchant denominator" (≈55%).**
NVIDIA ships Rubin on cadence; CoWoS/HBM scarcity keeps challengers supply-capped. AMD reaches ~8–12% merchant share; ASICs grow fastest in inference but, counted at *merchant* value (i.e., barely), don't move the merchant denominator much. NVIDIA lands in the **76–82% merchant** band at end-2027. *Resolution: YES (≥75%).* This is the modal path because the supply chokepoint and CUDA moat are both still binding and Rubin executed on schedule as of the March 2026 GTC reaffirmation [T2].

**Upside case (for NVIDIA) — "Scarcity + AI-capex wobble" (≈20%).**
A capex pause or neocloud-financing stress (CoreWeave-style leverage strain) freezes relative shares; challengers can't fund the buildout to gain ground, and NVIDIA's installed-base lock-in dominates. NVIDIA holds **>82% merchant**. *Resolution: YES, comfortably.*

**Downside case — "Definitional crossover" (≈25%).**
Either (a) the resolution convention imputes captive TPU/Trainium/MTIA at internal transfer value — in which case NVIDIA is plausibly *already* <75% by the metric — or (b) AMD MI400 + ROCm break out *and* TSMC expands CoWoS enough to relax NVIDIA's first-call advantage, letting challengers ship to capacity. NVIDIA prints **<75%**. *Resolution: NO.* The (a) sub-path is the single largest swing factor and is why the headline probability is 0.62, not 0.80.

> Probabilities sum to 100%. The base+upside (YES) mass is ~75% on the *merchant* denominator alone; the published **0.62** discounts that for the meaningful chance the resolution authority adopts an ASIC-inclusive denominator.

---

## 8. Key Assumptions + Premortem

**Key assumptions (each with a falsification test):**
1. *Resolution uses a merchant-leaning denominator.* Falsify by: locating the resolution source's stated methodology; if it imputes captive ASICs at transfer value, this assumption breaks and P drops toward 0.45.
2. *CoWoS/HBM stays the binding constraint and NVIDIA keeps first call.* Falsify by: TSMC capacity guidance showing >2× CoWoS-L expansion with non-NVIDIA allocation rising.
3. *Rubin ships on cadence in 2027.* Falsify by: any NVIDIA cadence-slip disclosure (none as of Mar 2026 GTC [T2]).
4. *No AI-capex recession or Taiwan disruption.* Falsify by: hyperscaler capex cuts or geopolitical escalation.
5. *ROCm/Neuron ecosystems remain materially behind CUDA for the training layer.* Falsify by: a frontier lab disclosing primary *training* (not inference) on a non-NVIDIA stack at scale.

**Premortem — it is end-2027 and the forecast was wrong. Why?**
- *Most likely failure (definitional):* The market settled on counting captive ASICs at internal value, and Google+AWS+Meta captive volume — which I treated as denominator-light — was the dominant share story all along. I anchored too hard on the merchant framing that NVIDIA and sell-side desks prefer [T2 bias risk].
- *Second failure (challenger breakout):* AMD MI400/Helios hit NVLink parity *and* TSMC relaxed CoWoS scarcity faster than modeled, so AMD + ASICs compounded past the threshold. I underweighted the supply-expansion driver.
- *Third failure (China imputation):* A resolution source folded Huawei Ascend domestic volume into the global denominator, mechanically shrinking NVIDIA's global share regardless of Western dynamics.
- *Failure toward the other side (over-pessimism):* A capex wobble or power-buildout bottleneck froze the whole field, NVIDIA's installed base dominated, and I overweighted the erosion narrative that dominates current commentary.

**Calibration note.** The 0.62 deliberately sits near the middle because the dominant uncertainty is *not* technological or competitive — it is *definitional*, and definitional resolution risk is hard to drive below ~15–20% without the exact resolution methodology in hand. A reader who *knows* the denominator will be merchant-only should mentally raise this to ~0.74; one who knows it will be ASIC-inclusive at transfer value should lower it to ~0.42.

---

## 9. Source Attribution & Tiers

- **T1 (primary / authoritative):** NVIDIA, TSMC, AMD, ASML, SK Hynix corporate disclosures and product launches; US BIS export-control rulings (2022–2025); key-event record (GTC Blackwell Mar 2024, Blackwell Ultra/Rubin Mar 2025, BIS H20/MI308 licensing Apr 2025, MI350/MI400 Jun 2025, GTC Rubin reaffirmation Mar 2026).
- **T2 (reputable secondary / analyst):** SemiAnalysis (CoWoS/HBM bottleneck and accelerator-TCO analysis, ClusterMax), TrendForce (HBM/DRAM/AI-server forecasts), Morgan Stanley Research (accelerator TAM, share, custom-ASIC growth). Treated as influential but estimate-bearing and revision-prone; cross-checked against T1 where possible.
- **Bias flags:** Sell-side and vendor sources structurally favor the merchant denominator and the "expanding TAM" narrative (NVIDIA's preferred framing). SemiAnalysis carries premium-data commercial incentives. These biases all push the *consensus* toward the YES side, which is one reason the headline probability is held below the merchant-only ~0.74.

*Actor grades (A1–C3) in §4–5 reflect source reliability and evidentiary depth per actor as recorded in the underlying actor model; A1 = corroborated primary, B2 = single-source/inferred, C3 = thin/promotional.*

---

**Bottom line:** P(NVIDIA ≥ 75% by 31 Dec 2027) ≈ **0.62**. NVIDIA most likely holds the line on the merchant denominator that the market usually quotes; the principal path to a NO resolution is definitional — captive hyperscaler ASICs counted at internal value — rather than any single competitor out-executing NVIDIA on technology or supply.
