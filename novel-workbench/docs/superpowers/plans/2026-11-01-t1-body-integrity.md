# T-1 Body Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent submission preflight and packaging from trusting stale word counts or producing a package without a complete, unchanged manuscript.

**Architecture:** Keep the existing SQLite schema and use its chapter rows as the submission word-count mirror. Validate disk chapter files against the DB paths and hashes, and validate committed chapters against the tracking ledger's `body_sha256`; compare committed counts with `state.chapters_committed`. Make packaging reject missing required files and verify `全文.md` is present in the manifest.

**Tech Stack:** Node.js >=24, built-in `node:test`, SQLite via `node:sqlite`, existing filesystem helpers.

**Spec:** `references/tdd-workbench-1.0.md` §2 T-1; `references/prd-workbench-1.0.md` §3.1 M1.

## Global Constraints

- TDD: every production behavior starts with a test observed failing for the intended reason.
- Keep the SQLite schema unchanged; the committed body digest is in `追踪/_tracking-state.json` as `wordcount_records[N].body_sha256`.
- Preserve current package confirmation and preflight ordering semantics unless a test demonstrates a required change.
- Follow TDD §2 T-1: the DB chapter-row count, valid on-disk body-file count, and `state.chapters_committed` must agree; an extra draft body blocks submission because the current package full text could include it.
- Use `manifest_incomplete` for a missing `全文.md`, matching the existing red test.
- The Novel tools project under `F:\小说\` is read-only.

## Review Focus

- A deleted committed chapter after API refresh must remain detectable even when sync removes its DB row — test by comparing state committed count against DB/disk counts.
- An extra draft chapter must block if it makes DB/disk counts diverge from the committed count — test an uncommitted disk/DB chapter against `state.chapters_committed`.
- A committed chapter missing `body_sha256` cannot be proven unchanged — test that preflight treats it as unverifiable and blocks.
- A missing non-manuscript required package file must fail with an actionable integrity error instead of silently shortening the manifest — test one required report file.
- A stale `books.words_total` must not affect the word threshold — test a manually inconsistent cache and chapter sum.

---

### Task 1: Preflight chapter and digest integrity

**Files:**
- Modify: `server/submissions.mjs`
- Modify: `server/tests/tdd-001-body-integrity.test.mjs`
- Modify: `server/tests/v2-story-submit.test.mjs` (make existing package fixtures internally consistent)
- Test: `server/tests/tdd-001-body-integrity.test.mjs`

**Interfaces:**
- Consumes: `chapters.file_path`, `chapters.words`, `chapters.committed`, `chapters.sha256`; `_pipeline/state.json`; `追踪/_tracking-state.json`.
- Produces: `preflightSubmission(db, book)` returns `words` from chapter rows and a `body_integrity` check whose detail lists missing/extra chapter numbers and digest mismatches; `ready` is false for any integrity failure.

- [x] **Step 1: Run the existing focused red tests**

Run: `node --test server/tests/tdd-001-body-integrity.test.mjs`
Expected: 4 failures; first asserts preflight uses cached `books.words_total`, subsequent failures identify missing chapter/hash guards and missing manifest validation.

- [x] **Step 2: Add preflight tests for post-sync deletion, extra draft chapter, and missing committed digest**

Extend the existing integration fixture to assert that committed-count drift after a file is deleted is reported by chapter number; an extra uncommitted draft that makes the DB/disk counts exceed `state.chapters_committed` blocks and identifies the chapter; and a committed row without a ledger digest blocks as unverifiable.

- [x] **Step 3: Run focused tests to verify new assertions fail**

Run: `node --test server/tests/tdd-001-body-integrity.test.mjs`
Expected: the new cases fail specifically because no disk/committed integrity check exists.

- [x] **Step 4: Implement chapter-derived word count and body integrity check**

The adversarial follow-up also binds each DB path to the unique canonical body file, validates tracking counts against visible characters, validates `全文.md` against the current chapter collection, and rejects invalid committed counts (0–500).

Add a small helper in `submissions.mjs` that loads the state and tracking JSON, gathers DB chapter rows, validates each relative path remains inside the book directory, checks current file existence and SHA against `chapters.sha256`, and checks committed rows against the ledger `body_sha256`. Compare `chapters_committed` with DB and disk chapter counts; block extra uncommitted drafts because they can enter the current full-text package. Return a failure detail with chapter numbers and a fix directing the author to restore or re-sync/re-commit.

- [x] **Step 5: Run focused T-1 and existing submission tests**

Run: `node --test server/tests/tdd-001-body-integrity.test.mjs server/tests/v2-story-submit.test.mjs`
Expected: all tests pass; existing fixtures represent real disk chapters and consistent committed counts.

### Task 2: Fail closed when required package outputs are missing

**Files:**
- Modify: `server/submissions.mjs`
- Modify: `server/tests/tdd-001-body-integrity.test.mjs`
- Modify: `server/tests/api.test.mjs` (verify the API exposes the explicit missing-file error)
- Modify: `server/api.mjs` only if error mapping needs an explicit client response

**Interfaces:**
- Consumes: required file list built by `packageSubmission` and `preflight.words`.
- Produces: `packageSubmission()` throws `manifest_incomplete` naming every missing required file; successful manifest always includes `全文.md`.

- [x] **Step 1: Add a failing test for a missing required non-manuscript file**

Delete `投稿包/体检报告.md`; with all confirmations true, assert `packageSubmission()` throws `manifest_incomplete` and names the missing file.

- [x] **Step 2: Run the focused test to verify it fails**

Run: `node --test server/tests/tdd-001-body-integrity.test.mjs`
Expected: package generation currently skips the absent file and returns a manifest without it.

- [x] **Step 3: Implement required-file collection and manifest validation**

Replace silent `continue` for required package files with an integrity error that includes missing paths. Before writing `MANIFEST.json`, assert it contains `全文.md` and every required output. Ensure failed packaging does not append a confirmation audit entry.

- [x] **Step 4: Run focused tests**

Run: `node --test server/tests/tdd-001-body-integrity.test.mjs server/tests/v2-story-submit.test.mjs`
Expected: all tests pass; missing required outputs fail closed and valid packages remain immutable and complete.

### Task 3: Full regression and review

**Files:**
- Review changes in `server/submissions.mjs`, `server/api.mjs`, and submission tests.

- [x] **Step 1: Run the full project test suite**

Run from `novel-workbench/`: `npm test`
Expected: every default test passes, including the now-implemented T-1 regression suite.

- [x] **Step 2: Review the final diff and whitespace**

Run: `git diff --check` and inspect `git diff`.
Expected: no whitespace errors; only T-1 implementation and necessary test fixture changes, plus pre-existing user document edits.
