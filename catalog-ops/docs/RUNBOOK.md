# catalog-ops runbook

## Deploy
- Update the desired stack files under `stacks/<stack>/`.
- Pull the repo on the Portainer host and redeploy the stack in Portainer.
- If Watchtower is in use, allow it to refresh images after redeploy.

## Rollback
- Check out a previous git commit on the host and redeploy the stack in Portainer.
- If the rollback requires an older image tag, update the compose file to the prior tag and redeploy.

## Kill switches
- Stop the stack in Portainer (immediate service shutdown).
- To disable public routing, remove the Traefik labels or detach the service from the `traefik` network and redeploy.

## Smoke tests
- Verify stacks show as running in Portainer.
- Confirm Traefik routes respond for:
  - `similar.jonsarkin.com`
- For metrics, confirm the `grafana` and `prometheus` hostnames defined in the metrics stack resolve.

## Monitoring / alerts
- The `metrics` stack provides Prometheus, Grafana, node_exporter, and cAdvisor.

## Notes
- Several stacks rely on external networks/volumes and node label constraints; confirm these exist before deploy.
