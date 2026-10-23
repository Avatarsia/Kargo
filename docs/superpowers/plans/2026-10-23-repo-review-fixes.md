# Repository Review Fixes Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. No commits are authorized.

**Goal:** Resolve all 14 reviewed findings with reproducible regression tests and independent review.
**Architecture:** Preserve Python Moonraker component, SQLite metadata and Linux shell installers; enforce safe file-descriptor operations and async completion before print start.
**Tech Stack:** Python 3.7-compatible production syntax, POSIX, asyncio, SQLite, Bash; pytest/Jinja2 tests under WSL.
**Spec:** `.superpowers/sdd/repo-review-fixes/spec.txt` (original 14-finding report, copied unchanged).

## Global Constraints

Shared constraints: Linux/POSIX target, existing Python architecture and public fetch/offload positional parameters preserved. Python 3.7-compatible production syntax; no new runtime dependency. No commit/push/merge, no nested agents, no installer execution against real system files. UTF-8, preserve existing encoding, Linux shell scripts LF. Only owned files may be edited. Work in shared current T3 worktree; other workers are editing disjoint files. Token-Audit was explicitly declined: do not run it and do not ask about it. Follow TDD: write meaningful failing regression tests, capture RED, implement, capture GREEN. Run scoped tests; full suite after integration. Use real temporary Linux filesystem for file safety. No NOT-TO-DO.md exists. Report full evidence in designated .superpowers/sdd/repo-review-fixes/task-N-report.txt, final reply short DONE/concerns and test counts. No broad refactoring.

## Interfaces

Binding interfaces: MetadataCache.get_files_metadata(rel_paths: List[str]) -> Dict[str, Dict[str, Any]] uses one DB connection, chunks SQL if necessary. get_pending_offloads(limit: int=10, after_id: int=0) returns id>after_id ordered id ASC; deduplicate queue by rel_path, preserve stable id/retry history. Existing method signatures otherwise retained. offload gains optional final nas_mount_point: Optional[Path]=None; component passes configured mount point explicitly; direct utility tests can pass temp dir with mocked mount check. remote_fetch becomes async def remote_fetch(filename: str, start_print: bool=False), ordinary NAS_FETCH only fetches; SDCARD_PRINT_FILE macro calls remote method with start_print=True and NEVER invokes original itself. Component invokes _SDCARD_PRINT_FILE_ORIGINAL FILENAME="escaped filename" via await klippy_apis.run_gcode only AFTER successful await fetch_file; reject CR/LF/NUL filename, properly escape quotes/backslashes. Fetch failure never starts. No recursive macro call. lock_dir default under local_gcodes_dir.parent / 'systemd' / 'nas-offload-locks' (expanduser); installers explicitly configure $PRINTER_DATA_DIR/systemd/nas-offload-locks, local_gcodes_dir and cache_db for detected data root. List uses get_files_metadata for page; preserve files response with live nas_available, optional offset(default0)/limit(default100,max1000) and pagination metadata. All blocking filesystem/DB I/O from async handlers, health, retries must run executor. Background tasks tracked/cancelled at shutdown.

## Review Focus

- Malicious NAS symlinks and parent swaps never overwrite/read host files.
- Retention never deletes the current archive or a different model.
- A failed/slow fetch never starts a print; filenames stay correctly quoted.
- Repeated print cycles, partial status updates and >10 pending entries make progress.
- Unprivileged fresh install compiles macros and matches actual printer_data paths.

### Task 1: File safety, retention and recovery

Requirements: `.superpowers/sdd/repo-review-fixes/task-1-brief.txt`.

- [x] Write regression tests and capture expected RED.
- [x] Implement owned files against binding interfaces.
- [x] Run covering tests and capture GREEN in task report.
- [x] Independent task review for spec compliance and quality; fix findings.

### Task 2: Metadata batching and stable retry queue

Requirements: `.superpowers/sdd/repo-review-fixes/task-2-brief.txt`.

- [x] Write regression tests and capture expected RED.
- [x] Implement owned files against binding interfaces.
- [x] Run covering tests and capture GREEN in task report.
- [x] Independent task review for spec compliance and quality; fix findings.

### Task 3: Moonraker async lifecycle and print handshake

Requirements: `.superpowers/sdd/repo-review-fixes/task-3-brief.txt`.

- [x] Write regression tests and capture expected RED.
- [x] Implement owned files against binding interfaces.
- [x] Run covering tests and capture GREEN in task report.
- [x] Independent task review for spec compliance and quality; fix findings.

### Task 4: Installer, macros and relevant documentation

Requirements: `.superpowers/sdd/repo-review-fixes/task-4-brief.txt`.

- [x] Write regression tests and capture expected RED.
- [x] Implement owned files against binding interfaces.
- [x] Run covering tests and capture GREEN in task report.
- [x] Independent task review for spec compliance and quality; fix findings.

### Final integration

- [x] Run complete Linux test suite, Bash syntax, exact Jinja compilation, diff hygiene.
- [x] Independent whole-change Security/integration review and scoped fix verification.
- [x] Report evidence, limitations and branch; leave uncommitted for user review.
