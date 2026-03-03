# omeka runbook

## Deploy
- Local dev:
  - `docker compose up` (Omeka on http://localhost:8080).
- VPS volume sync:
  - Copy inventory if needed: `cp ansible/inventory.ini.example ansible/inventory.ini`.
  - Update `ansible/inventory.ini` with host/user.
  - Run: `ansible-playbook -i ansible/inventory.ini ansible/deploy.yml`.
  - Optional: `-e deploy_delete=true` to remove extraneous files, `-e omeka_root=/path` to change destination.

## Rollback
- Check out a previous git commit (especially `volume/`) and re-run the deploy playbook.
- For local, stop containers (`docker compose down`), roll back changes, then start again.

## Kill switches
- Local: `docker compose stop` or `docker compose down`.
- Production web shutdown is managed on the VPS (outside this repo); stop the Omeka web service there if needed.

## Smoke tests
- Local: load http://localhost:8080 and confirm Omeka responds.
- VPS: load the public site and check the web server logs for errors.

## Monitoring / alerts
- Not defined in this repo; rely on VPS/infra monitoring.

## Notes
- The deploy playbook excludes `files/` and `config/` by default to avoid clobbering uploads and server settings.
