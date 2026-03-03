# ansible-hyphae spec

## Purpose
- Manage baseline configuration and services for the hyphae host and signage Pi(s) via Ansible.

## In scope
- Base host configuration (packages, SSH, UFW, users, timezone, WireGuard).
- GPU/Docker runtime setup and Python runtime provisioning.
- Cockpit service enablement for fishcity hosts.
- Raspberry Pi signage kiosk provisioning and VNC access.
- Qdrant snapshot backup automation and GCS upload.
- Inventory and group_vars for host targeting.

## Out of scope
- Application code and data not managed by Ansible roles here.
- Omeka content and Qdrant data beyond snapshot backup mechanics.
- Manual server changes outside Ansible.

## Dependencies
- Ansible + SSH access to hosts in `inventory/hosts.ini`.
- Debian/Ubuntu hosts with systemd and apt.
- Google Cloud Storage bucket `jonsarkin-catalog-backups-us` and WIF credentials.
- Qdrant reachable at `qdrant_url` (default `http://10.10.0.1:6333`).

## Interfaces
- Playbooks: `playbooks/site.yml`, `playbooks/signage.yml`, `playbooks/pi_baseline.yml`.
- Makefile targets: `make hello`, `make run`, `make lint`.
- Inventory/vars: `inventory/hosts.ini`, `group_vars/*.yml`.
