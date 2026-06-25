"""Minor-gap fixes: git-URL detection, .git name derivation, empty-glob note."""

from __future__ import annotations

from reflens.engine import Repo
from reflens.ingest import base
from reflens.ingest import ingest_source


def test_looks_like_git_url():
    assert base._looks_like_git_url("https://github.com/x/y")
    assert base._looks_like_git_url("http://host/x/y.git")
    assert base._looks_like_git_url("git@github.com:x/y.git")
    assert base._looks_like_git_url("ssh://git@host/x.git")
    assert not base._looks_like_git_url("/local/path/to/repo")
    assert not base._looks_like_git_url("./repomix-output.md")
    assert not base._looks_like_git_url("repomix.md")


def test_derive_name_strips_git_suffix():
    assert base.derive_name("https://github.com/chopratejas/headroom.git") == "headroom"
    assert base.derive_name("https://github.com/x/My-Repo") == "my-repo"
    assert base.derive_name("git@github.com:org/Cool.Tool.git") == "cool.tool"
    assert base.derive_name("/path/to/repomix-output-foo.md") == "foo"


def test_empty_path_glob_emits_note(sample_repo):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        text, stats = r.map(level=1, path_glob="does_not_exist/**")
    assert stats["files_total"] == 0
    assert "No files match path_glob" in text
    assert "reflens_modules" in text


def test_normal_glob_still_scopes(sample_repo):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        text, stats = r.map(level=2, path_glob="pkg/**")
    assert stats["files_total"] >= 1
    assert "No files match path_glob" not in text
    assert "pkg/core.py" in text
