# Contributing

Thanks for helping make PlugWarden better for AMP network owners.

- **Bugs and ideas:** open an issue. For bugs, include your AMP version, server platforms (Paper/Purpur/Velocity…) and what you expected.
- **Security issues:** see [SECURITY.md](SECURITY.md); please report privately.
- **Pull requests:** keep them focused, and make sure `pytest` passes. CI runs the test suite and a dependency audit on every PR.

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
LGT_SANDBOX=/path/to/sandbox dev/serve.sh     # dev server on 127.0.0.1:18095
```

Always develop against a **copy** of a datastore (`sandbox/base/<Instance>/Minecraft/plugins`, `sandbox/state/`), never a live one.
The frontend has no build step: edit files in `app/static/` and reload.
