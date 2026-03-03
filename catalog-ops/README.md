# catalog-ops
Docker Compose stacks for catalog infrastructure, deployed via Portainer.

## Stacks
- `stacks/qdrant/` — Qdrant vector database + CLIP API + ingest worker
- `stacks/traefik/` — Reverse proxy and HTTPS routing
- `stacks/metrics/` — Prometheus, Grafana, node_exporter, cAdvisor
