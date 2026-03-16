# Remaining Improvements and GitHub Actions

## Scope

- Finish the current orchestration cleanup by separating the video item dispatcher and moving music verification policy out of `cli.py`.
- Consolidate shared selection/cache policy helpers used by CLI and UI code.
- Add GitHub Actions CI plus release-artifact packaging.
- Update README to match the implemented behavior and new workflow.

## Assumptions

- `python -m pytest -q` is the only enforced quality gate in this repo today.
- GitHub Actions should build artifacts only, not publish them.
- Windows and Linux are the supported CI platforms for this pass.

## Milestones

1. Refactor `video_item_service.py` into a dispatcher plus explicit TV/movie entrypoints.
2. Extract the music album verification/decision branch into a dedicated service.
3. Centralize folder-cache trust and skip-reason naming in `selection_policy.py`.
4. Add `.github/workflows/ci.yml` and `.github/workflows/release-artifacts.yml`.
5. Update README with CI, risky-enter behavior, folder-cache reuse, and clean export guidance.

## Verification

- `python -m pytest -q`
- `python -m build`

## Risks

- Service extraction is mechanical and should preserve behavior; recursive media-type switching and cached music decisions are the highest-risk paths.
- GitHub Actions should mirror local commands exactly to avoid a second quality bar.
