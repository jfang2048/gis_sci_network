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
