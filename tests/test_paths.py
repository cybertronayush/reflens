from __future__ import annotations

import pytest

from reflens import paths


def test_slugify_basic():
    assert paths.slugify_name("My Repo!") == "my-repo"
    assert paths.slugify_name("Foo/Bar Baz") == "foo-bar-baz"
    assert paths.slugify_name("keeps.dots_and-dashes") == "keeps.dots_and-dashes"


def test_slugify_neutralizes_traversal():
    # Malicious input is sanitized to a safe single-segment slug (no separators),
    # never an escaping path.
    slug = paths.slugify_name("../../etc")
    assert "/" not in slug and ".." not in slug
    assert not slug.startswith((".", "-"))


def test_slugify_rejects_empty_after_strip():
    for bad in ("..", "...", "/", "----", "   "):
        with pytest.raises(paths.InvalidRepoName):
            paths.slugify_name(bad)


def test_repo_dir_contained():
    base = paths.repos_dir().resolve()
    # Even a hostile slug stays under repos_dir.
    d = paths.repo_dir(paths.slugify_name("../../etc"))
    assert base == d.parent or base in d.parents
