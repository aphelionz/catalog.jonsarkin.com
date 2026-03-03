# catalog-ops spec

## Purpose
- Provide Portainer-ready Docker Compose stacks for catalog infrastructure services.

## In scope
- Compose stacks under `stacks/` (traefik, qdrant + clip-api + ingest, metrics).
- Traefik configuration in `stacks/traefik/traefik.toml`.

## Out of scope
- Building application images (handled in other repos or registries).
- Application data migrations and backups.
- Host-level Docker/Swarm setup and secrets management.

## Dependencies
- Portainer (Docker Swarm or compatible stack deployer).
- External `traefik` network on the host.
- Node labels referenced by deploy constraints (for example `traefik-public` or `metrics`).
- External volumes referenced by stacks (for example `clip-qdrant-dev_qdrant_data`).
- Environment variables used by stacks (for example `OPENAI_API_KEY`, database credentials).

## Interfaces
- Stack definitions in `stacks/*/docker-compose.yml`.
- Traefik config at `stacks/traefik/traefik.toml`.
