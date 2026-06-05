from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib


@dataclass
class HostConfig:
    id: str
    label: str
    type: str = "local"
    ssh: str | None = None
    password: str | None = None
    root: str | None = None
    poll_interval_seconds: int = 10
    connect_timeout_seconds: int = 5
    command_timeout_seconds: int = 15
    enable_actions: bool = False


def load_hosts(path: Path | None = None) -> list[HostConfig]:
    config_path = Path("agent-console.toml") if path is None else path
    if not config_path.exists():
        return [_local_host()]

    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    rows = data.get("hosts")
    if not isinstance(rows, list) or not rows:
        return [_local_host()]

    hosts = [
        _host_from_row(row, index)
        for index, row in enumerate(rows)
        if isinstance(row, dict)
    ]
    return _with_default_local(hosts)


def save_host(host: HostConfig, path: Path | None = None) -> list[HostConfig]:
    config_path = Path("agent-console.toml") if path is None else path
    hosts = [host for host in load_hosts(config_path) if host.id != "local" or config_path.exists()]
    normalized = _normalize_host(host)
    updated: list[HostConfig] = []
    replaced_existing = False
    for existing in hosts:
        if existing.id == normalized.id:
            updated.append(normalized)
            replaced_existing = True
        else:
            updated.append(existing)
    if not replaced_existing:
        updated.append(normalized)
    _write_hosts(config_path, updated)
    return updated


def delete_host(host_id: str, path: Path | None = None) -> list[HostConfig]:
    config_path = Path("agent-console.toml") if path is None else path
    normalized_id = host_id.strip()
    if normalized_id == "local":
        raise ValueError("local host cannot be deleted")
    hosts = [host for host in load_hosts(config_path) if host.id != normalized_id]
    _write_hosts(config_path, hosts)
    return _with_default_local(hosts)


def discover_ssh_hosts(config_paths: list[Path] | None = None) -> list[dict[str, str]]:
    paths = config_paths or _default_ssh_config_paths()
    candidates: dict[str, dict[str, str]] = {}
    for path in paths:
        if not path.exists():
            continue
        for row in _parse_ssh_config(path):
            candidates.setdefault(row["id"], row)
    return sorted(candidates.values(), key=lambda item: item["id"].lower())


def _local_host() -> HostConfig:
    return HostConfig(id="local", label="Local", type="local")


def _with_default_local(hosts: list[HostConfig]) -> list[HostConfig]:
    if not hosts:
        return [_local_host()]
    if any(host.id == "local" for host in hosts):
        return hosts
    return [_local_host(), *hosts]


def _host_from_row(row: dict[str, Any], index: int) -> HostConfig:
    host_id = _required_text(row, index, "id")
    return HostConfig(
        id=host_id,
        label=str(row.get("label") or host_id),
        type=str(row.get("type") or "local"),
        ssh=_optional_text(row.get("ssh")),
        password=_optional_text(row.get("password")),
        root=_optional_text(row.get("root")),
        poll_interval_seconds=_positive_int(row, index, "poll_interval_seconds", 10),
        connect_timeout_seconds=_positive_int(row, index, "connect_timeout_seconds", 5),
        command_timeout_seconds=_positive_int(row, index, "command_timeout_seconds", 15),
        enable_actions=_boolean(row, index, "enable_actions", False),
    )


def _required_text(row: dict[str, Any], index: int, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{_field(index, field)} must be a non-empty string")
    return value


def _positive_int(row: dict[str, Any], index: int, field: str, default: int) -> int:
    value = row.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{_field(index, field)} must be a positive integer")
    return value


def _boolean(row: dict[str, Any], index: int, field: str, default: bool) -> bool:
    value = row.get(field, default)
    if not isinstance(value, bool):
        raise ValueError(f"{_field(index, field)} must be a boolean")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _field(index: int, field: str) -> str:
    return f"hosts[{index}].{field}"


def _normalize_host(host: HostConfig) -> HostConfig:
    if not isinstance(host.id, str) or not host.id.strip():
        raise ValueError("host.id must be a non-empty string")
    if host.type == "ssh" and not host.ssh:
        raise ValueError("ssh host requires an ssh target")
    return replace(
        host,
        id=host.id.strip(),
        label=(host.label or host.id).strip(),
        type=(host.type or "local").strip(),
        ssh=host.ssh.strip() if isinstance(host.ssh, str) else None,
        password=host.password if isinstance(host.password, str) and host.password else None,
        root=host.root.strip() if isinstance(host.root, str) and host.root.strip() else None,
    )


def _write_hosts(path: Path, hosts: list[HostConfig]) -> None:
    lines = [
        "# CliDeck host configuration.",
        "# Passwords are stored in local plaintext when provided.",
        "",
    ]
    for host in hosts:
        lines.append("[[hosts]]")
        lines.append(f'id = {_toml_string(host.id)}')
        lines.append(f'label = {_toml_string(host.label)}')
        lines.append(f'type = {_toml_string(host.type)}')
        if host.ssh:
            lines.append(f'ssh = {_toml_string(host.ssh)}')
        if host.password:
            lines.append(f'password = {_toml_string(host.password)}')
        if host.root:
            lines.append(f'root = {_toml_string(host.root)}')
        lines.append(f"poll_interval_seconds = {host.poll_interval_seconds}")
        lines.append(f"connect_timeout_seconds = {host.connect_timeout_seconds}")
        lines.append(f"command_timeout_seconds = {host.command_timeout_seconds}")
        lines.append(f"enable_actions = {str(host.enable_actions).lower()}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _toml_string(value: str) -> str:
    return json_escape(value)


def json_escape(value: str) -> str:
    # TOML basic strings use the same escaping for the characters we emit here.
    import json

    return json.dumps(value)


def _default_ssh_config_paths() -> list[Path]:
    home = Path.home()
    paths = [home / ".ssh" / "config"]
    appdata = Path.home() / "AppData" / "Roaming"
    paths.extend(
        [
            appdata / "Code" / "User" / "globalStorage" / "ms-vscode-remote.remote-ssh" / "config",
            appdata / "Cursor" / "User" / "globalStorage" / "ms-vscode-remote.remote-ssh" / "config",
            appdata / "Code - Insiders" / "User" / "globalStorage" / "ms-vscode-remote.remote-ssh" / "config",
        ]
    )
    return paths


def _parse_ssh_config(path: Path) -> list[dict[str, str]]:
    hosts: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\s+", line, maxsplit=1)
        if len(parts) != 2:
            continue
        key, value = parts[0].lower(), parts[1].strip()
        if key == "host":
            current = None
            for name in value.split():
                if "*" in name or "?" in name or name.lower() == "github.com":
                    continue
                current = {"id": _host_id_from_alias(name), "label": name, "ssh": name, "source": str(path)}
                hosts.append(current)
            continue
        if current is None:
            continue
        if key == "hostname":
            current["hostname"] = value
        elif key == "user":
            current["user"] = value
    for host in hosts:
        target = host.get("hostname") or host["label"]
        if host.get("user"):
            target = f"{host['user']}@{target}"
        host["ssh"] = target
    return hosts


def _host_id_from_alias(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._")
    return cleaned or "host"
