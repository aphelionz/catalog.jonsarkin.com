# omeka spec

## Purpose
- Maintain the Omeka S deployment assets and a local Docker-based dev stack, and sync the `volume/` directory to the VPS.

## In scope
- Local `docker-compose.yml` for Omeka + MariaDB.
- Application `volume/` contents managed in this repo.
- Ansible deploy playbook that rsyncs `volume/` to the VPS (`ansible/deploy.yml`).

## Out of scope
- VPS web server configuration (nginx/apache) and OS management.
- Omeka database backups and operational monitoring.
- Uploaded files and server-specific config excluded by rsync (`files/`, `config/`).

## Dependencies
- Ansible and rsync on local and remote hosts.
- VPS reachable via `ansible/inventory.ini` with an Omeka root (default `/var/www/omeka-s`).
- Docker + Docker Compose for local development.

## Interfaces
- Local dev: `docker compose up` from repo root.
- Deploy: `ansible-playbook -i ansible/inventory.ini ansible/deploy.yml` with optional `deploy_delete` and `omeka_root` overrides.
