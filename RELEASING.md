# Releasing

reflens publishes to PyPI as a pure-Python package (zero required runtime deps;
the `semantic`/`code`/`tokens` extras are optional). The name **`reflens` is
available on PyPI** — no rename needed to publish.

## Verified build + publish steps

```bash
# 1. bump the version
$EDITOR reflens/_version.py        # e.g. 0.4.0 -> 0.5.0
# update CHANGELOG.md

# 2. build (artifacts land in dist/, which is gitignored)
python -m pip install -U build twine
python -m build                    # -> dist/reflens-<v>.tar.gz + .whl

# 3. validate metadata (must say PASSED)
twine check dist/*

# 4. smoke-test the wheel in a throwaway venv
python -m venv /tmp/rl-clean
/tmp/rl-clean/bin/pip install dist/*.whl
/tmp/rl-clean/bin/python -c "import reflens; print(reflens.__version__)"
/tmp/rl-clean/bin/reflens --help

# 5. publish (needs a PyPI API token in ~/.pypirc or TWINE_PASSWORD)
twine upload dist/*
# or test first against TestPyPI:
# twine upload --repository testpypi dist/*
```

Steps 2–4 are confirmed working for 0.4.0 (`twine check` PASSED; clean-venv
install + CLI verified, including on Python 3.14). Step 5 is the only one that
needs your PyPI account/token.

## Tag the release

```bash
git tag -a v<version> -m "reflens <version>"
git push origin v<version>
```

## CI note

`.github/workflows/ci.yml` exists on disk but is git-excluded because the gh
OAuth token lacks the `workflow` scope. To enable CI:

```bash
gh auth refresh -h github.com -s workflow
# then un-exclude and commit it:
git update-index --no-skip-worktree .github/workflows/ci.yml 2>/dev/null || true
git add .github/workflows/ci.yml && git commit -m "ci: add workflow" && git push
```
