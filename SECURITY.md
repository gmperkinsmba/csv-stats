# Security

CSV Stats stores event rows and immutable metric run snapshots in a local database. The loopback server checks Host and Origin, requires a random token for writes, and limits request sizes. Runtime data is kept outside the app folder with owner-only permissions. CSV imports have byte and row limits, unique-header checks, and constrained mappings. SHA-256 fingerprints identify repeated imports; they do not conceal event data or prove who supplied it.

Local database files are plaintext protected by account permissions. Use your Mac’s disk encryption to protect saved files. The native app has an ad-hoc code signature. This lets the installer check whether its files changed. It does not prove who made the app. The build is not notarized.

Report security issues through [GitHub private vulnerability reporting](https://github.com/gmperkinsmba/csv-stats/security/advisories/new).
