# AGENTS.md

This is the monorepo for the Jon Sarkin catalog raisonné (catalog.jonsarkin.com).

## Structure

- `omeka/` — Omeka S catalog backend (modules, themes, config, Ansible deploy)
- `sarkin-clip/` — CLIP embedding pipeline + FastAPI search/similarity API
- `ansible-hyphae/` — Ansible provisioning for the hyphae production host + production Docker stacks

## Local development

```
make doctor   # check prerequisites
make local    # start omeka + qdrant + clip-api
make ingest   # one-shot: index Omeka items into Qdrant (CPU)
make logs     # tail all service logs
make down     # stop everything
```

## Operating rules (for humans + agents)

1) **Read docs first**
   - Before changing anything, locate and follow the documentation in each directory:
     - `*/docs/` (preferred), otherwise `README.md` / `RUNBOOK.md` / `SPEC.md`.
   - Treat those docs as the source of truth for how to run, deploy, test, and operate.

2) **Keep docs and code aligned**
   - When you find any mismatch between documentation and actual behavior, do both:
     - **Update the docs** to match reality, or **update the code/config** to match the docs (choose the smaller, safer change).
     - **Report** with a short discrepancy note:
       - what the docs claim
       - what you observed
       - what you propose to change and why
