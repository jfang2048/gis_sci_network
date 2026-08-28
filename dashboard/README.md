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
- Network node size uses the metric named beside the chart. Edge width is constant; the selected
  full or fractional weight controls inclusion rather than width.
- Primary geographic flows use complete annual flow aggregates. The optional institution-link map
  and fixed-layout network remain thresholded display subsets; their absence does not prove that no
  collaboration exists in the full processed data.
- Every chart retains exact values in hover text, a companion table, or the Data quality page. The
  fixed network also provides a text description of the currently rendered filters and encodings.
