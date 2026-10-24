## What and why

<!-- What changes, and why. Link the issue if there is one. -->

## Checklist

- [ ] The five checks in [CONTRIBUTING.md](https://github.com/riskand/racklint/blob/main/CONTRIBUTING.md#before-you-push) pass:
  - [ ] `ruff format --check`
  - [ ] `ruff check`
  - [ ] `mypy`
  - [ ] `pytest --cov`
  - [ ] `pytest examples/no-hardware`
- [ ] Tests are added or updated.
- [ ] `CHANGELOG.md` is updated under `## Unreleased` for a user-facing change.
- [ ] Recordings hold no real serial numbers, MAC addresses, IP addresses, hostnames or credentials, and
      `tests/recordings/README.md` states their source and licence.
- [ ] No breaking change to the command line, the JSON report or `racklint.__all__` without discussion.
