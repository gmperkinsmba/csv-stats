# CSV Stats

First principle: Count each entity once per cohort and keep the event evidence that supports every result.

CSV Stats maps CSV event exports into versioned cohort measures. Its installed Mac app starts and stops its local server with the window. The included demo data is synthetic.

## Install and run the Mac app

This build targets macOS. Python 3.12 is used for the local build environment.

```sh
python3.12 -m venv .desktop-venv
.desktop-venv/bin/python -m pip install -r requirements-desktop.lock.txt
.desktop-venv/bin/python build_desktop.py
.desktop-venv/bin/python install_desktop.py
```

Open `~/Applications/CSV Stats.app` from Finder. Close the window or press Command-Q to stop the app and its local listener. To uninstall, move that app to Trash. The installer preserves a previous owned copy beside the replacement and refuses to replace an unrelated app.

PyInstaller applies an ad-hoc signature. The app has no Apple Developer ID signature and is not notarized. macOS may ask you to approve opening it.

The local database is stored at `~/.local/share/outcome-metrics/` with owner-only permissions. Imported event data remains on this device. The app has no external integrations, accounts, or API keys.

## Development and browser option

For source work, run the app backend with:

```sh
python3 server.py
```

The optional browser mode uses http://127.0.0.1:8324. The installed app uses an ephemeral loopback port and does not attach to an existing server. No third-party Python packages are needed for the browser backend. Desktop build dependencies are listed in `requirements-desktop.lock.txt`.

## Use and limits

Import a CSV and map its columns to event ID, entity ID, event type, and time. An optional source column separates event ID namespaces. IDs stay as text, including leading zeroes. In Settings, choose a denominator event, outcome event, and observation window. A definition change creates a new version. Each run saves an immutable snapshot.

The synthetic demo imports the same event file twice, then adds a late outcome. Earlier run snapshots remain available. Duplicate rows retain import provenance and do not add events twice.

Count an entity only if its first-ever denominator event falls in the selected period. Count its first outcome at or after that event only when it falls within the exact observation window. All event times use UTC. Date-only values mean midnight UTC. The as-of date covers the full UTC day. Pending entities stay out of both the numerator and mature denominator. A zero outcome count differs from missing data and pending observation.

The app does not export CSV. Browser storage is not used for imported data.

## Tests and build notices

```sh
python3 -m unittest discover -s tests -v
```

`build_desktop.py` writes readable notices for the Python runtime and installed build dependencies into the app. It uses the pinned dependency lock and verified fallback license texts under `third_party_licenses/`.
