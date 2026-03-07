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
python -m pip install -e .
```

After editable install, you can run the console script directly:

```powershell
plexify --help
```

## Quick start

Wizard (recommended):

```powershell
python -m plexify.cli
```

The wizard guides you through incoming/library folders, media type, dry-run vs apply, copy vs move,
auto-accept settings, confidence threshold, and cache usage. It prints the exact command it runs.
Move mode requires typing MOVE to confirm. During a dry run, it prints a loud warning that no files
will be moved or copied, and it offers to apply the plan immediately at the end.

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
When running in dry-run with interactive mode, Plexify offers to apply the plan at the end without
redoing the selections. It also prints the exact apply command so you can rerun later.

Optional cleanup (move only):

```powershell
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --mode apply --move --prune-empty-dirs
```

This removes empty folders under the incoming root after a successful move. In dry-run, it prints
what would be removed.

Filter by media type:

```powershell
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --media-type movie
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --media-type tv
```

Auto-accept and confidence threshold:

```powershell
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --yes --min-confidence 0.90
```

Strict safety preset:

```powershell
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --strict-safe
```

`--strict-safe` applies conservative defaults for that run: disables cache usage, disables auto-accept,
forces explicit risky-choice handling, and enforces a minimum confidence floor of `0.95`.

Interactive selection shortcuts:

- `Enter` accepts #1 (only when candidates exist)
- `1-9` chooses a candidate
- `s` searches again
- `m` manual entry
- `k` skip this file
- `q` quit the run
- `b` go back to the previous file
- Any other text is treated as a search query

Cache control:

```powershell
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --no-cache
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --clear-cache
```

Logging:

```powershell
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --log-level DEBUG
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --log-format json
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --log-file ".plexify\run.log"
```

JSON logs are line-delimited and include event metadata, for example:

```json
{"timestamp":"2026-02-08T12:00:00+00:00","level":"INFO","logger":"plexify.plexify.cli","message":"run_started","event":"run_started","command":"organise"}
```

Offline mode:

```powershell
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --offline
python -m plexify.cli music --source "D:\Rips" --library "D:\Media" --offline
```

Offline behaviour:

- No network lookups are performed.
- For video, cache hits are still used.
- For video with no cache hit: interactive mode still allows manual entry; non-interactive mode skips.
- For music, `--offline` disables MusicBrainz verification for that run.

Cache reuse:

- Movie cache keys are based on normalised title + year (e.g., `Superman II` caches separately from `Superman`).
- TV show cache keys include show title + year, and episode keys include season + episode when known.
- TV folder keys are stored as `tvfolder|<relative show folder>` so confirming one episode can reuse the show choice for sibling episodes in the same folder, even when inferred year is unknown.
- Reuse only applies to entries confirmed by the user (`confirmed_by_user: true`).
- TV cache lookup precedence is: episode key -> reusable show key -> folder key -> file-specific key.

Matching notes:

- Search queries preserve sequel markers (e.g., `II`, `2`) so sequels do not collapse to the base title.
- Auto-accept requires a meaningful confidence gap or a close year match; otherwise it asks for confirmation.
- Timing output labels `api=` for API calls and `total=` for end-to-end processing.

Conflict handling:

```powershell
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --on-conflict rename
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --on-conflict skip
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --on-conflict overwrite
```

Completions:

```powershell
python -m plexify.cli --install-completion
```

Run once to enable shell autocompletion for options and paths.
For in-wizard tab completion, install prompt_toolkit:

```powershell
python -m pip install prompt_toolkit
```

Music organisation (dry-run):

```powershell
python -m plexify.cli music --source "D:\Rips" --library "D:\Media" --dry-run
```

Music organisation (apply, move, skip verification):

```powershell
python -m plexify.cli music --source "D:\Rips" --library "D:\Media" --apply --no-verify
```

Verbose per-track plan output:

```powershell
python -m plexify.cli music --source "D:\Rips" --library "D:\Media" --dry-run --verbose-plan
```

Example CD layout:

```
Alanis Morissette - Jagged Little Pill\
  01 - Alanis Morissette - All I Really Want.flac
```

Output:

```
D:\Media\Music\Alanis Morissette\Jagged Little Pill\01 - All I Really Want.flac
```

## Troubleshooting

- Click/Typer compatibility: keep the pinned versions in `requirements.txt`.
- Wikidata blocked or 403: set `PLEXIFY_USER_AGENT` to include contact details.
- MusicBrainz throttling: set `PLEXIFY_USER_AGENT` to include contact details.
- Offline/proxy networks: lookups may fail; confirm your proxy settings or run
  with cached results.
- Incoming and library overlap: Plexify exits with code 2; use separate folders to avoid re-scanning output.
- Reports are stored in `.plexify/reports` under the library folder.
- Cache is stored at `.plexify/cache.json` under the library folder by default (override with `--cache`).
- Numbered episode folders (e.g., `Pride and Prejudice\1.mkv`, `2.mkv`) are treated as TV when the folder
  contains multiple video files. The show name is taken from the folder name and any year is used as a hint.

## Clean project export

To create a shareable archive without local runtime artifacts (`.git`, `.venv`, `.pytest_cache`, `.plexify`, temp folders):

```powershell
.\scripts\export-clean.ps1
```

This writes `exports\plexify-clean.zip` by default.

You can override the destination path:

```powershell
.\scripts\export-clean.ps1 -OutputPath "exports\plexify-YYYYMMDD.zip"
```
