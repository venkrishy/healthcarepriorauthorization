# Lesson: CDK Python Lambda Packaging with PythonFunction

## The Problem

`aws_cdk.aws_lambda_python_alpha.PythonFunction` bundles your code using Docker. Getting
the `entry` / `index` / `handler` trio right is non-obvious and the errors are misleading.

---

## Rule 1: `entry` is the package root, not the handler directory

`PythonFunction` runs `pip install -r requirements.txt` relative to `entry`, then puts all
files under `entry` into the Lambda zip. `index` is the path to the handler module
**relative to `entry`**.

```
repo/
  authagent/           ← entry=this
    api/
      main.py          ← index="authagent/api/main.py"  WRONG if entry=authagent/
    requirements.txt   ← must exist here

# Correct:
repo/                  ← entry=this (parent of authagent/)
  authagent/
    api/
      main.py          ← index="authagent/api/main.py"
  requirements.txt     ← must exist here
```

If `entry=authagent/` and `index="authagent/api/main.py"`, CDK looks for
`authagent/authagent/api/main.py` — doubled path, file not found.

**Rule:** Set `entry` one level above your top-level package. Then
`from authagent.xxx import yyy` works in Lambda because `authagent/` is a proper
subdirectory of the bundle root.

---

## Rule 2: Use `requirements.txt`, not `pyproject.toml`

`PythonFunction` only recognises `requirements.txt` or `Pipfile`. A `pyproject.toml` using
hatchling/flit is silently ignored — Lambda starts but `import fastapi` fails at runtime.

Put a flat `requirements.txt` in the `entry` directory (not in a subdirectory).

---

## Rule 3: Set `output` outside the project directory

CDK writes synthesized assets to `cdk.out/` by default. If `cdk.out/` lives inside the
project and you run `cdk synth` again, the Docker bundler picks up the previous `cdk.out/`
and re-bundles it — `ENAMETOOLONG` / infinite recursion.

```json
// infra/cdk.json
{
  "app": "python3 app.py",
  "output": "/tmp/authagent-cdk-out"
}
```

Also add a `.dockerignore` next to `entry`:

```
infra/
.venv/
cdk.out/
__pycache__/
*.pyc
.git/
tests/
```

---

## Rule 4: Always set `asset_excludes` in bundling

```python
bundling={
    "asset_excludes": [
        "infra", ".venv", "__pycache__", "*.pyc",
        "tests", ".git", "docs",
    ]
}
```

Without this, CDK bundles your entire repo into the Lambda, slowing every deploy.

---

## Checklist Before First Deploy

- [ ] `entry` points to the **parent** of your top-level Python package
- [ ] `index` is relative to `entry`, e.g. `"authagent/api/main.py"`
- [ ] `requirements.txt` exists at `entry` root
- [ ] `cdk.json` has `"output": "/tmp/<name>-cdk-out"`
- [ ] `.dockerignore` exists next to `entry`
- [ ] `asset_excludes` set in `bundling`
