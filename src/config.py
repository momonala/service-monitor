import tomllib
from pathlib import Path

import typer

_config_file = Path(__file__).parent.parent / "pyproject.toml"
with _config_file.open("rb") as f:
    _config = tomllib.load(f)

_project_config = _config["project"]
_tool_config = _config["tool"]["config"]

FLASK_PORT = _tool_config["flask_port"]


# fmt: off
def config_cli(
    show_all: bool = typer.Option(False, "--all", help="Show all configuration values"),
    project_name: bool = typer.Option(False, "--project-name", help=_project_config['name']),
    project_version: bool = typer.Option(False, "--project-version", help=_project_config['version']),
    flask_port: bool = typer.Option(False, "--flask-port", help=str(FLASK_PORT)),
) -> None:
# fmt: on
    """Get configuration values from pyproject.toml."""
    requested = [
        ("project_name", project_name, _project_config["name"]),
        ("project_version", project_version, _project_config["version"]),
        ("flask_port", flask_port, FLASK_PORT),
    ]

    if show_all:
        for key, _, value in requested:
            typer.echo(f"{key}={value}")
        return

    for _, is_set, value in requested:
        if is_set:
            typer.echo(value)
            return

    typer.secho(
        "Error: No config key specified. Use --help to see available options.",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(1)


def main() -> None:
    typer.run(config_cli)


if __name__ == "__main__":
    main()
