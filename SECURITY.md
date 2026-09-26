# Security policy

PlugWarden writes to live Minecraft servers, so security reports are taken seriously.

**Please don't open a public issue for vulnerabilities.** Report them privately through
[GitHub's private vulnerability reporting](https://github.com/mk7luke/plugwarden/security/advisories/new).
Include what you found, how to reproduce it, and the version or commit you tested.

You can expect an acknowledgement within a few days. Fixes are released on `main` and noted in the advisory.

In scope: the PlugWarden app and its container setup (authentication, path handling, deploy/undo engine,
update downloads, redaction). Out of scope: AMP itself, Cloudflare, and third-party plugins.
