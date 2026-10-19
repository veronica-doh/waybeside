# Tests

Python tests:

    python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/python -m pytest tests -q

Browser tests (Playwright is a devDependency of this repo):

    npm ci && npx playwright install chromium
    node tests/browser-checks.cjs
    node tests/browser-menu-door.cjs
    node tests/browser-private-ui.cjs
    node tests/browser-course-config.cjs
    node tests/browser-onboarding.cjs      # first run: disclaimer -> 4-step wizard -> main; agent-share first-visit guide
    node tests/browser-assistant-mode.cjs  # first open: disclaimer -> choose how much the assistant may do; both paths, settings, marks; desktop + 375px
    node tests/browser-upgrade-notice.cjs  # one-time "can change in settings" note for upgraded libraries
    node tests/browser-user-journey.cjs    # regressions found by the documents-only user journey simulation
    node tests/browser-rules.cjs           # rules filtering UI (fictional rules, hidden groups, allergy reminder text)
    node tests/browser-generic.cjs         # --demo: fictional labs, free-text metric, user-chosen symptoms
    node tests/no-profile-view-unit.cjs    # no read-only profile view in source or DOM
    node tests/menu-view-unit.cjs && node tests/private-startup-unit.cjs

AGENTS.md is executed by tests/test_agents_md_example.py: every command in it runs for real (a live server and command-line subprocesses), once per assistant mode.

Hard-code check (must print 0 hits): `tools/check_hardcodes.sh`

Environment variables: `WAYBESIDE_PYTHON` (interpreter), `WAYBESIDE_TEST_ARTIFACTS`
(screenshot/report output dir, default: system temp dir), `PLAYWRIGHT_MODULE` (override Playwright path).

Demo data: `python run.py --demo` (fictional, separate `WayBeside-Demo` directory; set `WAYBESIDE_TODAY=YYYY-MM-DD` to fix the app's date in tests).
