# Deploy catalog services via Portainer stacks and Traefik

Status: accepted

## Context
- Multiple services (Traefik, Qdrant, Lychee, metrics, display site) need consistent deployment and routing.
- The environment uses Portainer to manage Docker Compose stacks.

## Decision
- Store each service stack as a Compose file under `stacks/` and deploy them via Portainer.
- Use Traefik labels and the external `traefik` network for ingress routing.

## Consequences
- Hosts must provide the external `traefik` network, required volumes, and node labels referenced in stack constraints.
- Deploys and rollbacks are performed by redeploying stacks in Portainer.
