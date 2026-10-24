# Contributing to racklint

Thank you for helping. A pull request is welcome for a fix, a check or a platform. Open an issue first for
anything large, so that the design is agreed before the code is written.

## Set up

racklint needs Python 3.12 or later. Its dependencies are in `requirements.txt`, and its tools and the
optional gNMI client in `requirements-dev.txt` and `requirements-network.txt`. Those files are the source of
truth: `pyproject.toml` reads them, so change a dependency there and nowhere else.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,network]"
```

If you change the entry points in `pyproject.toml`, run `pip install -e ".[dev,network]"` again, so that the
installed metadata has them.

## Before you push

All of these must pass:

```bash
ruff format --check
ruff check
mypy
pytest --cov
pytest examples/no-hardware
```

The integration tests need Docker and run in CI, not here. If you have Docker,
`examples/no-hardware/README.md` shows how to run the stack.

## Code

- Every source file starts with a comment that names it: `# racklint/src/racklint/runner.py`. JSON, lock
  files, Markdown and `py.typed` do not.
- Design in this order: SOLID, then KISS, then DRY. Code must read well to a reviewer, with the style and
  comment density of the code around it. Read the code and tests near your change before you write new ones.
- A change adds or updates its tests. `tests/` never imports from `examples/`.
- Do not break users of the command line or the library without discussing it. A new field has a default, and
  a function that is part of the public API (`racklint.__all__`) keeps its signature.
- Wrap Markdown prose at 110 characters.

## Commits

A commit has a subject in the imperative, without a final full stop, such as `Wait for the fabric's switches`.
Add a body where the reason is not obvious: say why, and what you did differently from the obvious approach.
Do not add trailers. Keep one task to one commit.

## Adding a platform

A [platform](README.md#platforms) is one family of devices: how to read them and which checks judge them. Read
[Writing a platform](README.md#writing-a-platform) first for the contract. A platform goes in one of two
places:

- **In racklint**, if many people need it: `src/racklint/platforms/<name>.py`, with an entry in
  `pyproject.toml`'s `racklint.platforms` entry points. racklint registers its own platforms the way a package
  does.
- **As a package**, `racklint-<name>`, with its own repository and releases. It needs no change to racklint.

Either way, a platform comes with:

1. **A conformance test.** It reads recorded responses through the platform with `racklint.testing` and
   compares what the platform reports with what it should report. See `tests/test_testing.py`.
2. **Recordings.** Public, or anonymised. See below.
3. **A README section** on what the platform reads and what it cannot.

### Recordings

A recording is what a device returned. It makes the test of a platform real, and it is published with the
platform, so it must be safe to publish:

- A recording holds no real serial number, MAC address, IP address, hostname or credential. Use the
  documentation addresses `192.0.2.0/24` and `198.51.100.0/24`, invented serial numbers and locally
  administered MAC addresses (the second hexadecimal digit of the first octet is 2, 6, A or E). Check names,
  descriptions and any OEM section too.
- State where each recording comes from and its licence in `tests/recordings/README.md`: a public mockup or
  sample with its licence, a source whose shape you followed, or a recording you wrote, under racklint's
  licence.
- Never record a network that you may not publish. If you are not sure, you may not.

When you anonymise a capture, keep its structure and change only the values. Replace a value that the platform
reads, such as a serial number, with an invented one of the same form.
