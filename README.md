# Big Island Surf

A one-page surf forecast for Hawaiʻi Island only: a map with each coastline colored by forecast surf height, a shore-by-shore table (west and south shores first), the NWS swell outlook, Kona-side and Hilo-side weather, wind and tides, and the latest Hilo buoy reading.

**Live page:** https://wdmarshall-gif.github.io/big-island-surf/

## How it updates

A GitHub Actions workflow (`.github/workflows/update.yml`) runs every 3 hours. It runs `scripts/update.py`, which:

- pulls the NWS Honolulu Surf Zone Forecast (SRFHFO) and keeps only the discussion and the two Big Island zones,
- reads the latest swell and water temperature from NOAA buoy 51206 (Hilo),

then commits the fresh `data.json`. GitHub Pages serves this `gh-pages` branch and redeploys on every commit. If one source is down, its previous data is kept.

To refresh right away: **Actions → Update surf data → Run workflow**.

GitHub pauses scheduled workflows in public repos after 60 days without activity. If that happens, open the Actions tab and re-enable the workflow.

## Sources

- [NWS Honolulu surf forecast](https://www.weather.gov/hfo/SRF)
- [NOAA NDBC buoy 51206](https://www.ndbc.noaa.gov/station_page.php?station=51206)

Surf heights are wave faces, trough to crest, in feet. Conditions vary beach to beach; check [hawaiibeachsafety.com](https://hawaiibeachsafety.com) before going in.
