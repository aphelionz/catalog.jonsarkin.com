# AGENTS.md

This folder is a workspace containing multiple repositories. It is not itself a repository.

## Operating rules (for humans + Codex/agents)

1) **Read docs first**
   - Before changing anything, locate and follow the documentation in each repo:
     - `*/docs/` (preferred), otherwise `README.md` / `RUNBOOK.md` / `SPEC.md`.
   - Treat those docs as the source of truth for how to run, deploy, test, and operate that repo.

2) **Keep docs and code aligned**
   - When you find any mismatch between documentation and actual behavior (config, endpoints, commands, paths, env vars, ports, deploy steps, etc.), do both:
     - **Update the docs** to match reality, or **update the code/config** to match the docs (choose the smaller, safer change).
     - **Prompt** with a short discrepancy report:
       - what the docs claim
       - what you observed in code/runtime
       - what you propose to change (docs vs code) and why

## Repository boundaries
- Do not add scripts or code at the workspace root.
- All executable code, configs, compose files, and automation live inside the individual repos.

