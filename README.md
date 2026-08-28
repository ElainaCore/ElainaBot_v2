# ElainaBot CI assets

This orphan branch stores test suites and Docker deployment assets outside the
application source branch.

- `tests/`: test suites used by the reusable CI workflow.
- `docker/`: Dockerfile, entrypoint, and Compose definitions.
- `.github/workflows/`: reusable workflows called by the thin workflow
  entrypoints kept on `main`.

The workflows always check out the exact source revision that triggered the
caller on `main`, then add the assets from this branch. Keep the branch name
`ci-assets` stable and protect it from accidental deletion.
