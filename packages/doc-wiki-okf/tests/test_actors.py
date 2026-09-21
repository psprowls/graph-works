"""Actor strings follow okf-io's convention (OKF v0.2 §7) and never raise."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from doc_wiki_okf import actors
from doc_wiki_okf.actors import human_actor, human_handle, producer_actor
from okf_io.models import parse_actor


def _git_answers(monkeypatch: pytest.MonkeyPatch, **config: str) -> None:
    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        value = config.get(command[-1].replace("user.", ""))
        return subprocess.CompletedProcess(command, 0 if value is not None else 1, stdout=(value or "") + "\n")

    monkeypatch.setattr(actors.subprocess, "run", fake_run)


def test_a_producer_actor_is_the_agent_form_okf_io_accepts() -> None:
    actor = parse_actor(producer_actor("doc-wiki-okf"))
    assert actor is not None and actor.kind == "agent"


def test_an_uninstalled_package_is_stamped_version_zero() -> None:
    assert producer_actor("no-such-distribution-xyz") == "no-such-distribution-xyz/0"


def test_the_handle_is_the_local_part_of_the_git_email(monkeypatch: pytest.MonkeyPatch) -> None:
    _git_answers(monkeypatch, email="psprowls@gmail.com", name="Patrick Sprowls")
    assert human_actor() == "human:psprowls"
    actor = parse_actor(human_actor())
    assert actor is not None and actor.kind == "human"


def test_a_github_noreply_email_yields_the_login(monkeypatch: pytest.MonkeyPatch) -> None:
    _git_answers(monkeypatch, email="12345+octocat@users.noreply.github.com")
    assert human_handle() == "octocat"


def test_a_plus_tag_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    _git_answers(monkeypatch, email="Pat+work@example.com")
    assert human_handle() == "pat"


def test_the_git_name_is_slugified_when_there_is_no_email(monkeypatch: pytest.MonkeyPatch) -> None:
    _git_answers(monkeypatch, name="Patrick Sprowls")
    assert human_handle() == "patrick-sprowls"


def test_the_login_name_is_the_last_resort_before_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _git_answers(monkeypatch)
    monkeypatch.setattr(actors.getpass, "getuser", lambda: "Some User")
    assert human_handle() == "some-user"

    def broken() -> str:
        raise OSError("no login")

    monkeypatch.setattr(actors.getpass, "getuser", broken)
    assert human_handle() == "unknown"


def test_a_missing_git_binary_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git")

    monkeypatch.setattr(actors.subprocess, "run", no_git)
    monkeypatch.setattr(actors.getpass, "getuser", lambda: "fallback")
    assert human_actor(Path()) == "human:fallback"
