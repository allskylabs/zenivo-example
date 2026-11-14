# Contributing to Zenivo

Thank you for helping. Small, focused pull requests are easiest to review.

## Development setup

Python 3.10 or newer, on Linux (see [docs/INSTALL.md](docs/INSTALL.md) for the full guide).

```bash
git clone https://github.com/hoanb1/zenivo.git && cd zenivo
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python run_allsky.py --camera simulator --port 8080
```

The simulator needs no camera. Open `http://127.0.0.1:8080/`; the first-run admin setup code is in
`data/admin_setup_code.txt` (see [docs/SECURITY.md](docs/SECURITY.md)).

## Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/sync_nav.py --check   # navigation, brand block and page head of the HTML pages
```

- Follow the test pyramid: many fast unit tests for rules and pure logic, a few integration tests against
  real dependencies, very few API or end-to-end tests for wiring and the public contract.
- Test each behaviour once, in the layer that owns it; one representative case per rule, not one per input.
  Do not test library behaviour.
- A bug fix comes with a test that fails without it. Coverage is reported, never gated.

## Commits and pull requests

Use conventional commit subjects, as in `git log`: `feat(scope): ...`, `fix(scope): ...`, `test: ...`,
`docs: ...`, `security: ...`. Keep the subject short and say why in the body when it is not obvious.

## Privacy rules for changes

- **No new outbound connection without a row in [docs/TRANSPARENCY.md](docs/TRANSPARENCY.md)** (destination,
  what is sent, when, default, how to turn it off, source file). The same goes for new third-party loads in the browser.
- **Defaults must be private**: a new feature that contacts another host is opt-in and off by default, and it
  must not send the exact station position.
- Never commit real coordinates, host names, keys or passwords; use `example.org` style placeholders in docs and tests.

## Licence

By contributing you agree that your contribution is licensed under the [MIT licence](LICENSE).
