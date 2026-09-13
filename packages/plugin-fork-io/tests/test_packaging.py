from importlib.metadata import distribution

from plugin_fork_io.cli import app
from typer.testing import CliRunner


def test_distribution_declares_version_dependencies_and_command():
    package = distribution("plugin-fork-io")
    assert package.version == "0.1.0"
    assert {requirement.split(">=")[0] for requirement in package.requires or ()} == {
        "markdown-it-py",
        "ruamel-yaml",
        "typer",
    }
    assert any(
        entry.name == "plugin-fork" and entry.value == "plugin_fork_io.cli:main" for entry in package.entry_points
    )


def test_installed_cli_exposes_inspect_command():
    invocation = CliRunner().invoke(app, ["--help"])
    assert invocation.exit_code == 0
    assert "inspect" in invocation.stdout
