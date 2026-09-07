# PR #4 combined verification — 2026-09-07

Merged main `9fb75ef` into the messaging repair branch at `d8dc05c` before merging
PR #4. Resolved CLI imports by retaining both implementations, combined both
decision histories, and consolidated build state. The maintained README and
project integration are preserved. No research repository or other worktree was
accessed. No live Telegram operation or deployment occurred.

Secret-scanning alert #1 was resolved as `used_in_tests` with user authorization.
The flagged fixture was introduced for disclosure scanning and scripted HTTP;
the introducing commits record no real bot/credential acquisition. GitHub's
validity was `unknown`; no provider validity request was made.

## Results

| Check | Result | Exit |
|---|---|---|
| Combined core/state-machine/adversarial suite | 486 passed, 2 skipped, 18 deselected | 0 |
| Final CLI import normalization: messaging audit and integration regressions | 52 passed, 6 deselected | 0 |
| Combined actual Docker Linux VM isolation | 17 passed, 489 deselected | 0 |
| Fresh offline wheel build | Passed | 0 |
| External clean install, installed demo and actual synthetic Docker integration workflows | 1 passed | 0 |
| Ruff / original pack integrity / whitespace | Passed; 20 files / 46 requirements | 0 |
| Release accounting | No integrity issues; existing four blockers remain | 1 |

A28 and the live Telegram test remain skipped. A30/A31/A43, target-host messaging
identity/egress gates, and deployment authorization remain unchanged. Actual Docker
runner/experiment measurements do not certify the messaging service deployment.

The first install attempt failed because the audit command supplied a bare image
digest where the project contract requires a fully qualified image reference.
The corrected invocation used the same existing pinned image; runtime code and
requirements were not changed. The failed evidence is retained as
`install-invalid-image-input.*`. The first sandboxed build could not access the
existing uv cache; the authorized offline rerun succeeded.

## Reproduction

Evidence is under `/private/tmp/kestrel-pr4-merge/`, subject to system cleanup.
Source and evidence hashes are retained in `docs/audits/pr4-merge/`. JUnit uses
`uncommitted` because verification preceded the merge commit. Import sorting was
normalized during verification and the combined CLI regressions reran afterward.

```sh
.venv/bin/ruff check src tests tools/release_gates.py tools/prepare_wheelhouse.py tools/validate.py
python3 tools/validate.py
KESTREL_TEST_SCOPE=core .venv/bin/python -m pytest -m 'not isolation and not install' -q --basetemp=/private/tmp/kestrel-pr4-core --junitxml=/private/tmp/kestrel-pr4-merge/core.xml
.venv/bin/python -m pytest tests/test_messaging_audit_regressions.py tests/test_integration.py -m 'not isolation' -q --basetemp=/private/tmp/kestrel-pr4-cli --junitxml=/private/tmp/kestrel-pr4-merge/combined-cli.xml
uv build --offline
KESTREL_TEST_SCOPE=isolation KESTREL_TEST_PROFILE=isolated-local KESTREL_TEST_RUNTIME=linux-docker-vm KESTREL_RUN_ISOLATION=1 KESTREL_DOCKER_IMAGE=sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca .venv/bin/python -m pytest -m isolation -q --basetemp=/private/tmp/kestrel-pr4-isolation --junitxml=/private/tmp/kestrel-pr4-merge/isolation.xml
KESTREL_TEST_SCOPE=install KESTREL_WHEEL=/Users/iamsikun/research/kestrel/dist/kestrel_research_runtime-0.1.0-py3-none-any.whl KESTREL_WHEELHOUSE=/private/tmp/kestrel-wheelhouse KESTREL_INSTALL_EVIDENCE=/private/tmp/kestrel-pr4-merge/install-evidence.json KESTREL_INTEGRATION_IMAGE=ghcr.io/astral-sh/uv@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca .venv/bin/python -m pytest tests/test_install.py -q --basetemp=/private/tmp/kestrel-pr4-install-corrected --junitxml=/private/tmp/kestrel-pr4-merge/install.xml
.venv/bin/python tools/release_gates.py --junit /private/tmp/kestrel-pr4-merge/core.xml --junit /private/tmp/kestrel-pr4-merge/install.xml --junit /private/tmp/kestrel-pr4-merge/isolation.xml --source-revision uncommitted --output /private/tmp/kestrel-pr4-merge/release-gates.json --blocker 'A28=Actual sanitized provider captures unavailable' --blocker 'A30=Live provider test unauthorized and untested' --blocker 'A31=Credential-boundary integration unauthorized and untested' --blocker 'A43=Target GPU untested'
git diff --check
```
