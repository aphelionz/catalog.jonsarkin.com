# ansible-hyphae runbook

## Deploy
- Confirm `inventory/hosts.ini` and SSH access to targets.
- Full apply (all roles): `ansible-playbook -i inventory/hosts.ini playbooks/site.yml` (or `make hello`).
- Signage-only apply: `ansible-playbook -i inventory/hosts.ini playbooks/signage.yml`.
- Pi baseline (static IP + unattended upgrades): `ansible-playbook -i inventory/hosts.ini playbooks/pi_baseline.yml`.
- If using virtual X11/VNC, set `raspi_signage_virtual_vnc_password` in `group_vars/signage.yml` and re-run the signage playbook.

## Rollback
- Check out a prior git commit/tag and re-run the same playbook to restore previous state.
- For a single service, disable or restart the relevant systemd unit on the host.

## Kill switches
- Disable signage (on the Pi):
  - `sudo systemctl disable --now raspi-signage.service`
  - `sudo systemctl disable --now raspi-signage-virtual.service`
  - `sudo systemctl disable --now raspi-signage-virtual-vnc.service`
  - `sudo systemctl disable --now raspi-signage-watchdog.timer`
- Stop Qdrant backups (on hyphae):
  - `sudo systemctl disable --now qdrant-backup.timer`
- Disable Cockpit (if enabled):
  - `sudo systemctl disable --now cockpit.socket`

## Smoke tests
- `systemctl status raspi-signage* --no-pager` on signage hosts.
- `systemctl status qdrant-backup.timer --no-pager` on hyphae.
- Follow `X11.md` to confirm the VNC tunnel and virtual display.

## Monitoring / alerts
- No dedicated monitoring defined here; use systemd status and `journalctl -u <unit>`.

## Notes
- Host behavior is driven by `group_vars/*.yml`. Update vars before reapplying playbooks.

