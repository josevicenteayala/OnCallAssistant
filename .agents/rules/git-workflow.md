# Git Release & Tagging Rules

## Best Practices & Invariants

1. **Always Sync Before Tagging**:
   - Run `git fetch origin` and integrate tracking branch changes (`git rebase origin/<branch>` or `git pull --rebase origin <branch>`) *before* creating release tags or pushing changes.
   - Never push tags before verifying that the target commit is integrated into the remote tracking branch.

2. **Headless & Non-Interactive Rebase Handling**:
   - In automated or non-interactive subshell environments where `EDITOR` is unset, pass `GIT_EDITOR=true` or use `--no-edit` when running `git rebase --continue`.

3. **Atomic Tagging & Push**:
   - Create annotated tags with semantic versioning matching project metadata (e.g. `v0.1.0` matching `pyproject.toml`).
   - Push branch commits and tags cleanly to origin:
     ```bash
     git tag -a vX.Y.Z -m "Release vX.Y.Z"
     git push origin <branch> --tags
     ```
