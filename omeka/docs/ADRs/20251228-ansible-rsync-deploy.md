# Deploy Omeka volume via Ansible rsync

Status: accepted

## Context
- Omeka is installed on a VPS with a managed root directory.
- Local changes to the `volume/` directory should be synced without clobbering uploads or server-specific config.

## Decision
- Use the Ansible playbook `ansible/deploy.yml` to rsync `volume/` to the VPS.
- Exclude `files/` and `config/` by default and keep deletes optional via `deploy_delete`.

## Consequences
- Production uploads/config remain on the server and are not managed here.
- Deploys require rsync on both local and remote hosts.
- Rollbacks are done by re-syncing a prior git state of `volume/`.
