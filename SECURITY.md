# Security

## Report a vulnerability

Use GitHub's [private vulnerability reporting](https://github.com/MartinWa/growbook/security/advisories/new).
Do not disclose vulnerabilities in public issues or pull requests before review.

Include the affected commit/version, impact, and a minimal reproduction using
synthetic data. Never attach real photos, databases, backups, credentials or private
host/proxy configuration. Test only installations you own or have permission to test.

Security fixes target the current `main` branch and latest release, when available.
Older versions are not maintained. This small project has no guaranteed response
or resolution time.

## Deployment boundary

Growbook is intended for private LAN/VPN access through HTTPS, not public Internet
exposure. Use non-root containers, host-local SQLite/photo storage, and independently
protected backups. See [README.md](README.md) and
[backup and recovery](docs/backup-and-recovery.md) for the supported setup.
