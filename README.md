# Plexify

Plexify is an interactive CLI that reorganises and renames movie + TV files into Plex-compatible structure with human confirmation at each step.

Features:

- Safe by default (dry-run)
- Apply mode with move/copy
- Undo support (best-effort)
- Metadata from free, no-key sources (TVMaze, Wikidata)
- Interactive confirmation with candidate lists

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Quickstart

Dry run (default):

```bash
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" \
  --extensions ".mkv,.mp4,.avi,.m4v,.mov,.ts"
```

Print the planned destination tree:

```bash
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --print-tree
```

Run without prompts (uses cache or high-confidence matches only):

```bash
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --no-interactive --yes
```

Apply (move by default):

```bash
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --mode apply
```

Copy instead of move:

```bash
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --mode apply --copy
```

Undo the last run:

```bash
python -m plexify.cli undo --report "D:\Media\.plexify\reports\2026-01-21_11-45-33.json"
```

## How it works

- Scans incoming folders for video files
- Infers movie vs TV episode
- Searches TVMaze (TV) or Wikidata (movies)
- Prompts for confirmation
- Builds Plex-compatible destinations

Plex layout:

```
<LIBRARY>/Movies/<Movie Title> (<Year>)/<Movie Title> (<Year>).ext
<LIBRARY>/TV Shows/<Show Name> (<Year>)/Season <NN>/<Show Name> (<Year>) - sNNeMM - <Episode Title>.ext
```

## Troubleshooting

- If metadata is ambiguous, use `m` (manual) or `s` (new search) during prompts.
- If a year is missing for a movie, you will be prompted to enter it or accept "Unknown Year".
- Use `--no-interactive` for batch runs (still requires confidence or cache).

## Recommended structure

You can keep an `_Incoming` folder inside your library root:

```
<LIBRARY>/_Incoming
```

This is optional; any incoming folder works.
