# Task prompt — HERO1 Two-BAT Windows Launcher + Start Workspace Dashboard Preview
Archived verbatim BEFORE implementation (2026-11-06), per the documentation protocol.

HERO1: Implement Two-BAT Windows Launcher + Start Workspace Dashboard Preview

Objective

I want the same simple Windows startup experience we had in the previous Evolutionary Trading Lab dashboard.

I do NOT want to manually use PowerShell, activate virtual environments, install dependencies, or execute multiple commands.

Implement exactly TWO Windows batch files in the Hero1 project root:

1. `PREREQUISITES.bat`
2. `START.bat`

The first installs and verifies everything required. The second starts the complete dashboard.

Additionally, after implementation, testing and GitHub publication, you MUST start Hero1's dashboard inside the current LMS workspace so I can immediately inspect the actual running application, including its theme, tabs, navigation and layout.

This is a narrow infrastructure and usability task. Do not modify Hero1's strategy logic, backend business logic, database schema, frontend design, API behavior, or existing research functionality.

1. PREREQUISITES.bat — one-time setup and dependency installer. It must:
- Work when double-clicked from Windows Explorer.
- Work when invoked from CMD or PowerShell.
- Explicitly execute batch commands through CMD-compatible logic.
- Determine the project root using `%~dp0`, not the current working directory.
- Use quoted absolute paths throughout.
- Detect Windows, Python, Node.js and npm.
- Verify Python 3.11+ and Node.js 20 LTS compatibility.
- Create the project's `.venv` if missing, otherwise reuse it.
- Upgrade pip inside that exact virtual environment.
- Install all required Hero1 Python packages using the existing project configuration and documented editable-package installation commands.
- Install frontend dependencies using the existing package-lock.json and `npm ci` where appropriate.
- Verify actual backend imports using `.venv\Scripts\python.exe`.
- Run the frontend production build and verify its exit code.
- Initialize the Hero1 database only when required, using the existing initialization script.
- Run the database integrity/check command.
- Configure `HERO1_DATA_ROOT` to `D:\Hero1\DATA` by default, while allowing a documented override.
- Keep all generated research data outside the Git source directory.
- Create required logging directories without deleting or resetting existing data.
- Detect an existing MT5 installation if available, but do not make MT5 mandatory for Phase 2.
- Treat Ollama and GPU checks as optional unless the existing project explicitly requires them.

Dependency verification — do not report success simply because pip or npm completed. Verify:
- Backend imports using the exact Hero1 virtual environment interpreter.
- Required package imports.
- Frontend TypeScript/Vite production build.
- Database initialization and integrity.
- Required project configuration and folders.
If any operation fails, identify the exact failed step and stop the success sequence.

Logging — append logs to `D:\Hero1\DATA\LOGS\prerequisite.log`. Display clear progress including current installation step, command being executed, success/failure, error details, and what the user should do next. At the end, display a clear setup-success message and instructions to double-click `START.bat`. On failure, keep the window open using `pause`. Never silently close.

2. START.bat — the everyday launcher. The user should only need to double-click this file.
Startup sequence:
1. Resolve project root using `%~dp0`.
2. Verify required files, `.venv`, frontend dependencies and configuration.
3. Verify the database is initialized.
4. Check whether the required ports are already occupied.
5. If an existing Hero1 instance is already running, detect it and avoid launching duplicate backend/frontend processes.
6. Launch the backend in a separate persistent CMD window.
7. Use the project's exact backend startup command and the interpreter from `.venv`.
8. Poll `http://127.0.0.1:8788/health` until the real backend health response confirms readiness.
9. If backend startup fails or times out, show the error and relevant log.
10. Only after backend readiness, launch the frontend in a separate persistent CMD window using the existing frontend configuration.
11. Verify frontend readiness before opening the browser.
12. Open the actual dashboard URL automatically.
13. Keep all service windows alive until the user deliberately stops the application or the process exits.

Persistent terminal windows — particularly important. Use explicit `cmd.exe` invocation and persistent `/k` windows for backend and frontend. The backend must not disappear when the launcher finishes. The frontend must not disappear when the launcher finishes. Do not use a detached process approach that hides errors or causes services to terminate when the parent batch file exits. The backend and frontend should each have visible terminal windows showing their live output. The user must be able to inspect errors directly.

Health checks and failure handling:
- Use real HTTP readiness checks, not arbitrary sleep delays.
- Use a sensible timeout.
- Do not open the browser before the backend and frontend are ready.
- If either service fails, display the exact issue and log location.
- If the frontend fails after the backend has started, safely handle the partially started instance.
- Do not kill unrelated Python, Node, or other user processes.
- Do not claim successful startup without actual readiness confirmation.

Logging — append logs to `D:\Hero1\DATA\LOGS\startup.log`, `D:\Hero1\DATA\LOGS\backend.log`, `D:\Hero1\DATA\LOGS\frontend.log`. Keep logs useful and timestamped.

3. Project and DATA layout:
`D:\Hero1\app\` = Hero1 source repository.
`D:\Hero1\DATA\` = persistent research data, database, logs, backups and generated artifacts.
The two BAT files must live in the Hero1 source repository root:
`D:\Hero1\app\PREREQUISITES.bat`
`D:\Hero1\app\START.bat`
Configure: `HERO1_DATA_ROOT=D:\Hero1\DATA`
Never delete, overwrite, reset or recreate existing DATA merely to make startup work.
Never put research data inside the Git source checkout.

4. Reuse the previous dashboard's proven BAT conventions. Preserve:
- `%~dp0` project-root detection.
- Explicit CMD execution.
- Proper quoting for paths with spaces.
- Reuse valid `.venv` and `node_modules`.
- Check every important command's exit code.
- Persistent visible terminal windows.
- Actual backend health polling.
- Actual frontend readiness verification.
- Append-only diagnostic logs.
- Clear success and failure messages.
- Safe handling of repeated launches.
- No silent exits.
- No fake success messages.
- No destructive DATA operations.
The BAT files must work whether double-clicked from Explorer or invoked from CMD/PowerShell.

5. Testing and verification. Do not just create the files and report completion. Test the actual behavior. At minimum:
1. Test prerequisite detection.
2. Test dependency installation/reuse.
3. Test Python imports with the exact virtual environment interpreter.
4. Test frontend production build.
5. Test database initialization/check.
6. Run `PREREQUISITES.bat` and verify it reaches a genuine success state.
7. Run `START.bat` and verify the backend health endpoint.
8. Verify the frontend becomes available.
9. Verify the browser opens only after readiness.
10. Verify both service windows remain open after the launcher exits.
11. Verify repeated execution does not create duplicate instances.
12. Verify failures remain visible and diagnostic logs are produced.
If Windows execution is unavailable in your environment, explicitly state which tests could not be performed. Do not pretend that Linux testing proves Windows BAT compatibility.

6. GitHub publication. Only implement the two BAT files and the minimum supporting documentation necessary. Do not:
- Change application functionality.
- Change the database schema.
- Modify strategy validation/compiler logic.
- Change the UI.
- Modify existing research data.
- Modify legacy-archive.
- Add a third launcher or require the user to run manual commands.
Update the existing documentation to explain the two-file startup process.
Run relevant checks, inspect the Git diff, and ensure no secrets or credentials are included.
Commit and push the changes to the existing GitHub repository: `awais225/evolutionary-trading-lab`
Work on `main`, preserving all existing Hero1 and legacy history.
Report: exact files created/modified; exact commands and checks performed; actual test results; any Windows-specific tests not performed; commit SHA; push confirmation and remote main SHA.

7. FINAL REQUIRED STEP: RUN HERO1 IN THE LMS WORKSPACE. After implementation, testing and GitHub publication, do not stop at providing instructions. You MUST attempt to launch the actual Hero1 dashboard inside the current LMS workspace.
Workspace startup requirements:
1. Detect the actual workspace environment and available runtime.
2. Install only the dependencies required by the existing Hero1 application in that workspace.
3. Configure a workspace-specific DATA directory outside the Git source directory. Do not use or overwrite my Windows DATA directory.
4. Initialize a separate workspace database if necessary.
5. Start the Hero1 backend using the workspace-compatible command.
6. Verify its actual health endpoint.
7. Start the Hero1 frontend using the workspace-compatible command.
8. Configure the frontend to communicate with the workspace backend using the correct environment configuration or supported proxy.
9. Expose the frontend through the LMS workspace's supported preview, forwarded-port or application-preview mechanism.
10. Verify that the actual dashboard loads and that its frontend can communicate with the backend.
11. Keep both workspace services alive after the setup commands finish, using the platform's supported persistent process or preview mechanism.
Important workspace limitations:
- Do not assume the LMS workspace supports Windows BAT execution.
- Do not claim that the Windows BAT files were tested if they were only inspected or tested on Linux.
- Do not assume the workspace has a GPU, MT5 terminal or Windows environment.
- Do not substitute a mock dashboard or static HTML page.
- Do not create a separate fake UI to demonstrate appearance.
- Run the actual Hero1 frontend and backend.
- If the workspace does not support persistent services or public preview ports, identify the exact limitation and provide the supported alternative.
- Do not silently stop after a failed launch. Diagnose and report the reason.
What I need to inspect once running — give me the actual workspace preview URL or open the dashboard through the workspace preview feature. I want to inspect: dashboard theme; all eight main tabs; tab navigation; overall layout and responsiveness; Manual Nodes interface; WWW Research; Interactive WWW; DeepTesting; Fine Tuning; Live Testing; LOG; SETTINGS. Do not redesign or modify anything just because a tab is empty or a feature is not yet implemented. The purpose of this step is to inspect the current implementation.
Report which tabs actually load and which features are functional versus placeholders.

8. Final completion report. Provide one concise report with these sections:
**A. Windows launcher** — PREREQUISITES.bat status. START.bat status. Installation and dependency verification. Windows tests actually performed or not performed.
**B. GitHub** — Commit SHA. Push status. Remote main SHA.
**C. LMS workspace preview** — Backend status and health check. Frontend status. Actual preview URL. Whether backend/frontend communication works. Tabs inspected and their actual status.
**D. Remaining issues** — Exact unresolved problems, if any. Any environment limitations.

Acceptance criteria — the task is complete only when:
1. I can double-click `PREREQUISITES.bat` once on Windows.
2. I can double-click `START.bat` whenever I want to use Hero1.
3. Backend and frontend run in separate visible persistent terminal windows.
4. The browser opens automatically after readiness checks.
5. Research data remains outside the source repository.
6. The changes are committed and pushed to the existing GitHub repository.
7. The actual Hero1 dashboard is also started in the LMS workspace and made available through its supported preview mechanism so I can inspect it immediately.

**Two BAT files for Windows. One actual running dashboard preview in the LMS workspace. No unnecessary manual setup.**
