# Dashboard

> **Current scope:** this is the released complete-year annual analysis dashboard. The
> Geographic Flow Explorer now supports source-selected macro-region, subregion, and country
> relationships over inclusive complete-year windows. Institution-first School Finder, School
> Profile, Compare Schools, and rolling-window dashboard views remain planned under
> GISNET-133–138 even though their source data and comparison service are available on `main`.

Launch from the repository root:

```bash
uv run streamlit run dashboard/app.py
```

Then open <http://localhost:8501>.

The app reads only the compact processed snapshot in `dashboard/data/`. Rebuild that snapshot after
pipeline changes with:

```bash
uv run python -m gisnet.cli build-dashboard-data --resume
```

Ordinary dashboard viewing makes no OpenAlex or ROR requests. The checked-in snapshot contains only
public-source aggregate or thresholded scholarly-network data; it contains no API key or raw response
cache.

## Geographic Flow Explorer semantics

The explorer asks which selected geography collaborates with which partner geography. Its flow map
and selected-origin matrix row are generated from the same exact result frame at macro-region,
subregion, or country level. It supports complete-year windows from 2010 through 2025, Strict or
Broad corpus, organization or umbrella identity, and full or fractional counting.

Three metrics are available:

1. **Collaboration volume** is the selected full or fractional flow summed over the window.
2. **Partner share** divides a destination's endpoint weight by all endpoint weight attached to the
   selected source. For source `r`, the within-source share is:

```text
2 * W(r, r) / (2 * W(r, r) + sum(W(r, s) for s != r))
```

`W` is the selected full or fractional collaboration weight. An internal link contributes two
endpoints because both institutions are in `r`; a cross-geography link contributes one endpoint.
3. **Normalized intensity** is fractional geographic flow divided by the geometric mean of source
   and target full institutional Work-count denominators under the same corpus, hierarchy, and
   window. It is not geographic distance or research quality.

Every exact table retains full volume, fractional volume, partner share, normalized intensity, and
both Work-count denominators. Missing sparse rows mean no observed flow and are not imputed as zero.

Display controls do not alter those source values. The explorer first applies the selected minimum
collaboration weight and minimum partner share, then ranks cross-geography flows by the selected
metric and keeps Top N with target display label and stable geography ID as deterministic
tie-breakers. A qualifying internal flow remains in the source marker and companion table but does
not consume a Top N arc slot. The filtered map, matrix row, and exact displayed-flow table always
use the same result frame.

Arc widths are calibrated from each row's exact value, never from the maximum of the currently
visible subset. Consequently, an unchanged flow retains the same width when Top N or either
threshold changes. Width in display pixels is:

```text
volume:              min(8.0, 0.8 + 2.25 * log10(1 + selected_weight))
partner_share:       0.8 + 7.2 * sqrt(min(partner_share, 1.0))
normalized_intensity: 0.8 + 7.2 * sqrt(min(normalized_intensity, 1.0))
```

The 0.8–8.0 pixel bounds are display calibration, not a scientific normalization. Values above 1
for a bounded-width metric saturate visually at 8.0 pixels while their exact values remain in hover
and the companion table. Arcs use 32-point great-circle interpolation between sourced anchors.
Arc and partner-marker color encodes the target macro-region; the selected source has a distinct
diamond outline. Macro-region partner labels include the selected value directly on the map.

Geographic display anchors are unweighted spherical means of distinct source-provided OpenAlex
institution coordinates. They are versioned, checksum-linked, and recorded as CC0 in dashboard
metadata. They are research-institution display anchors, not geometric or political centroids; no
coordinate is invented.

Institution-level geographic links are available only as an optional, tightly limited drilldown.
They use sourced coordinates, report coordinate coverage, and must not be interpreted as a complete
geographic census when coordinates are missing.

## Reading the visuals

- Macro-regions keep stable colors across views: Europe is blue, Asia amber, the Americas teal,
  Africa magenta, Oceania sky blue, and unknown/other geography gray.
- Ordered shares use one Cividis sequential scale. A darker or lighter position on that scale is
  quantitative; missing matrix cells remain separately labelled and are never imputed as zero.
- Time-series charts add line dash to color so series remain distinguishable without color alone.
  The vertical dotted line marks the selected complete year.
- Fixed-layout network node size uses the metric named beside the chart. Its institutional edge
  width is constant; selected full or fractional weight controls inclusion. Geographic-flow arc
  widths instead use the fixed formulas documented above.
- Primary geographic flows use complete annual flow aggregates. The optional institution-link map
  and fixed-layout network remain thresholded display subsets; their absence does not prove that no
  collaboration exists in the full processed data.
- Every chart retains exact values in hover text, a companion table, or the Data quality page. The
  fixed network also provides a text description of the currently rendered filters and encodings.
