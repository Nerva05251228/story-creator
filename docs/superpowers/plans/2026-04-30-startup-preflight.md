# Startup Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make multi-worker Web startup fast and reliable by moving database bootstrap work out of `main.py` import time.

**Architecture:** Add a startup control surface: role-based poller enablement, a versioned migration state helper, and a preflight command that runs before Web/Poller processes. Web workers import `main:app` without DDL/DML and without starting pollers.

**Tech Stack:** Python, SQLAlchemy, FastAPI/Uvicorn, PowerShell/CMD startup scripts, unittest/pytest-compatible tests.

---

### Task 1: Startup Role Policy

**Files:**
- Create: `backend/startup_runtime.py`
- Test: `tests/test_startup_runtime.py`
- Modify: `backend/main.py`

- [ ] Add tests proving pollers are disabled by default, enabled only for explicit poller role or truthy override.
- [ ] Implement `startup_runtime.should_enable_background_pollers()`.
- [ ] Wire `main.py` to use the helper.

### Task 2: Migration State and Preflight

**Files:**
- Create: `backend/startup_migration_state.py`
- Create: `backend/preflight.py`
- Test: `tests/test_startup_migration_state.py`
- Modify: `backend/main.py`

- [ ] Add tests for migration version table behavior and source-level import-time side effects.
- [ ] Implement schema migration state helpers with checksum and advisory lock support.
- [ ] Implement `python -m preflight migrate/check`.
- [ ] Remove import-time `create_all()` and `run_startup_bootstrap()` from `main.py`.

### Task 3: Startup Scripts

**Files:**
- Modify: `start_web.ps1`
- Modify: `start_poller.ps1`
- Modify: `start_server.cmd`

- [ ] Run `preflight migrate` before Web startup.
- [ ] Run `preflight check` before Poller startup.
- [ ] Set Web role to disable pollers and Poller role to enable pollers.
- [ ] Keep multi-worker Web startup enabled.

### Task 4: Verification

**Files:**
- Test suite and import checks.

- [ ] Verify focused startup tests fail before implementation and pass after implementation.
- [ ] Verify `python -m preflight check` reports the current database state.
- [ ] Verify `python -c "import main"` no longer runs bootstrap.
