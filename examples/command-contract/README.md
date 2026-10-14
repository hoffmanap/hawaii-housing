# Optional: command-contract examples

These are **optional** helpers. The challenge only requires that your README
documents working **setup**, **run**, and **test** commands — it does not
require any particular tool. Pick whichever of these fits your stack (or none),
copy it to the repository root, and fill in your commands.

The default CI workflow (`.github/workflows/ci.yml`) runs the **shell script
trio** (`scripts/setup.sh` and `scripts/test.sh`) if present, so that trio is
the lowest-friction way to get a green CI check in any language.

Contents:

- `scripts/` — a POSIX shell script trio (`setup.sh`, `run.sh`, `test.sh`).
- `Makefile.example` — the same contract as Make targets.

## Using the shell trio

```
mkdir -p scripts
cp examples/command-contract/scripts/*.sh scripts/
chmod +x scripts/*.sh
# edit each script to run your real commands
```

## Using the Makefile

```
cp examples/command-contract/Makefile.example Makefile
# edit the setup/run/test targets
```
