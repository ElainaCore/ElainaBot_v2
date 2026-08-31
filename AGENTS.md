# Repository Instructions

## Test assets

- `main` and ordinary feature branches contain runtime framework code only. Do not add a `tests/` directory or any test-only asset to those branches.
- All automated tests and supporting assets belong to the dedicated `ci-assets` branch. This includes unit, integration, end-to-end, and stress tests; fixtures, mocks, snapshots, and test data; and plugins created only to exercise or validate the framework.
- Put test-only plugins under `tests/` on `ci-assets` (for example, `tests/fixtures/plugins/`). Never put a test plugin in the production `plugins/` directory.
- When a framework change needs tests, commit the source change on its normal branch and commit the related tests separately on `ci-assets`. Prefer a separate Git worktree so files from the two branches cannot be staged together.
- CI checks out the requested source revision, then overlays `ci-assets/tests` before running pytest. Keep the `ci-assets` branch and its `tests/` tree intact.
- Before every commit, check `git branch --show-current` and `git status --short`. If the current branch is not `ci-assets`, no test file or test-only plugin may be staged.
