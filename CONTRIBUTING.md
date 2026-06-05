# Contributing

Thanks for your interest in CliDeck.

## Development Setup

```bash
git clone https://github.com/YOUR_NAME/clideck.git
cd clideck
python -m pip install -e .
python -m uvicorn app:app --host 127.0.0.1 --port 7878
```

Open:

```text
http://127.0.0.1:7878/agent-console/
```

## Tests

```bash
python -m unittest discover -s tests/agent_console -v
```

## Before Opening A PR

- Do not commit `agent-console.toml` or `agent-console-state.json`.
- Do not commit real hostnames, private IPs, passwords, API keys, or internal paths.
- Keep the CliDeck UI dependency-light and easy to run locally.
- Add focused tests for collector, SSH, server route, and transcript parsing changes.

## Security

Remote actions can send input to SSH `screen` sessions. Treat changes in this
area carefully and keep unsafe behavior explicit in the UI and docs.
