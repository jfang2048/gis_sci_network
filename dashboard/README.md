# Dashboard

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

## Geographic comparison semantics

The geographic page defaults to proportional, coordinate-independent views rather than drawing a
dense web of institution links. For region `r`, the within-region collaboration share is:

```text
2 * W(r, r) / (2 * W(r, r) + sum(W(r, s) for s != r))
```

`W` is the selected full or fractional collaboration weight. An internal link contributes two
endpoints because both institutions are in `r`; a cross-region link contributes one endpoint to
each side. The same definition is used for the country choropleth, where it represents domestic
collaboration share. Absolute weights and denominators remain visible in details and hover text.

Institution-level geographic links are available only as an optional, tightly limited drilldown.
They use sourced coordinates, report coordinate coverage, and must not be interpreted as a complete
geographic census when coordinates are missing.
