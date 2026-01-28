# Plexify

Plexify is a small CLI that organises movie and TV files into a Plex-friendly
folder structure. It performs a dry run by default and asks for confirmation
when metadata is uncertain.

## Features

- Dry-run by default
- Move or copy on apply
- TVMaze and Wikidata lookups (no API keys)
- Undo from the last report
- Interactive selection with candidate lists

## Requirements

- Python 3.10+
- Windows tested; macOS/Linux should work with the same commands

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Usage

Help:

```powershell
python -m plexify.cli --help
python -m plexify.cli organise --help
```

Dry run:

```powershell
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --mode dry-run
```

Apply with copy:

```powershell
python -m plexify.cli organise --incoming "D:\Media\_Incoming" --library "D:\Media" --mode apply --copy
```

Set a Wikimedia user agent (recommended):

```powershell
$env:PLEXIFY_USER_AGENT="plexify/0.1 (contact: you@example.com)"
```

## Safety

Run in dry-run mode first. If you need to apply changes, copy before moving.

## Notes

Click is pinned to 8.1.7 for Typer compatibility.
