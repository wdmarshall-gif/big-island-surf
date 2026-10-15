# Big Island Surf

A one-page surf forecast for Hawaiʻi Island only: a map with each coastline colored by forecast surf height, a shore-by-shore table (west and south shores first), an extended swell table for the five days after the NWS forecast, the NWS swell outlook, and Kona-side and Hilo-side weather, wind and tides.

**Live page:** https://wdmarshall-gif.github.io/big-island-surf/

## How it updates

A GitHub Actions workflow (`.github/workflows/update.yml`) runs twice a day, at about 4:47 AM and 4:47 PM HST, shortly after each NWS forecast comes out. It runs `scripts/update.py`. It pulls the NWS Honolulu Surf Zone Forecast (SRFHFO), keeping only the discussion and the two Big Island zones, plus 7-day offshore swell height off each shore from Open-Meteo's marine model, then commits the fresh `data.json`. GitHub Pages serves this `gh-pages` branch and redeploys on every commit. If a source is unreachable, its previous data stays up.

To refresh right away: **Actions → Update surf data → Run workflow**.

GitHub pauses scheduled workflows in public repos after 60 days without activity. If that happens, open the Actions tab and re-enable the workflow.

## Sources

- [NWS Honolulu surf forecast](https://www.weather.gov/hfo/SRF)
- [Open-Meteo](https://open-meteo.com/) marine API (free for non-commercial use)

Surf heights are wave faces, trough to crest, in feet. Conditions vary beach to beach; check [hawaiibeachsafety.com](https://hawaiibeachsafety.com) before going in.
