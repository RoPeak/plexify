# Plexify

Plexify is a small CLI that organises movie and TV files into a Plex-friendly
folder structure. It is safe by default and asks for confirmation when metadata
is uncertain.

## Requirements

- Python 3.10+
- Windows tested; macOS/Linux should work with the same commands

## Installation

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Quick start

Wizard (recommended):

```powershell
python -m plexify.cli
```

Organise (dry run):

```powershell
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --mode dry-run
```

Organise (apply, copy):

```powershell
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --mode apply --copy
```

Organise (apply, move):

```powershell
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --mode apply --move
```

Warning: move will remove files from the incoming folder. Use copy first.

## Troubleshooting

- Click/Typer compatibility: keep the pinned versions in `requirements.txt`.
- Wikidata blocked or 403: set `PLEXIFY_USER_AGENT` to include contact details.
- Offline/proxy networks: lookups may fail; confirm your proxy settings or run
  with cached results.
- Incoming and library overlap: Plexify exits with code 2; use separate folders to avoid re-scanning output.
- Reports are stored in `.plexify/reports` under the library folder.
