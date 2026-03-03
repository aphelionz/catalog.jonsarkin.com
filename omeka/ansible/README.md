# Omeka deploy (Ansible)

This playbook rsyncs the local `./volume/` directory into the Omeka root on your VPS.

## Quick start

1) Copy the inventory:

```sh
cp ansible/inventory.ini.example ansible/inventory.ini
```

2) Edit `ansible/inventory.ini` with your VPS host/user.

3) Run deploy:

```sh
ansible-playbook -i ansible/inventory.ini ansible/deploy.yml
```

## Options

- Delete extraneous files on the server (default `false`):

```sh
ansible-playbook -i ansible/inventory.ini ansible/deploy.yml -e deploy_delete=true
```

- Change Omeka root path (default `/var/www/omeka-s`):

```sh
ansible-playbook -i ansible/inventory.ini ansible/deploy.yml -e omeka_root=/var/www/omeka-s
```

## Notes

- Requires `rsync` installed on both local and remote machines.
- By default, permissions/owners are not modified to avoid permission issues.
- The `files/` and `config/` directories are excluded by default to avoid clobbering uploads and server-specific settings.
