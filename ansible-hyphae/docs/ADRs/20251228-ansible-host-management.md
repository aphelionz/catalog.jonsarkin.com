# Manage hyphae hosts with Ansible roles

Status: accepted

## Context
- The hyphae host and signage Pi(s) need consistent baseline configuration and service setup.
- Changes should be repeatable, reviewable, and scoped by host group.

## Decision
- Use Ansible playbooks and roles (`playbooks/site.yml`, `playbooks/signage.yml`, `playbooks/pi_baseline.yml`) with `group_vars/` and `inventory/` to manage host state.
- Represent long-running services as systemd units managed by Ansible roles.

## Consequences
- All operational changes should flow through Ansible to avoid drift.
- Manual edits on hosts may be overwritten by subsequent playbook runs.
- Operators must have Ansible and SSH access to target hosts.
- Rollbacks are difficult because removals are not automatically handled; manual cleanup may be required.
