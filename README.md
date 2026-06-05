# CliDeck

CliDeck is a local-first web console for monitoring and controlling CLI coding
agents across your computer and SSH servers.

It is built for people who keep Codex, Claude, or other agent CLIs running in
multiple terminals, VS Code remote hosts, and remote `screen` sessions. CliDeck
gives those sessions one shared control surface: see what is active, open recent
conversation history, continue a session, and start new remote sessions without
jumping between terminal windows.

## What It Does

- Monitors local Codex and Claude CLI transcripts.
- Monitors multiple SSH servers at the same time.
- Imports SSH targets from VS Code / OpenSSH config, or lets you add servers manually.
- Keeps the local machine as a permanent default host.
- Interleaves local and remote sessions by activity time.
- Opens each session in a chat-style page with Markdown, tables, and code blocks.
- Sends prompts to local Codex sessions from the browser.
- Sends prompts to remote Codex sessions running inside `screen`.
- Lets you manually associate a session with the correct remote `screen`.
- Creates new remote Codex `screen` sessions from the browser.
- Starts new remote Codex sessions in full-access mode and sends an initial `你好`
  prompt so the transcript appears quickly.
- Supports optional desktop notifications when sessions complete.
- Stores runtime server configuration locally, not in a cloud service.

## Why

Agent CLIs are powerful, but the moment you run several of them across local and
remote machines, the workflow becomes scattered:

- one session is waiting on a permission prompt,
- another finished ten minutes ago,
- a remote `screen` has the answer but your browser view is stale,
- a useful conversation is hidden in a transcript file,
- and the next prompt requires switching terminals again.

CliDeck turns those loose CLI processes into a single operator console.

## Quick Start

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

On Unix-like systems you can also use:

```bash
bash run.sh
```

## Adding SSH Servers

If your SSH command is:

```bash
ssh -p 2222 root@connect.example.com
```

fill the server form as:

- host: `connect.example.com`
- user: `root`
- port: `2222`

Enable remote actions if you want CliDeck to create remote `screen` sessions or
send input to an existing remote `screen`.

## Remote Codex Sessions

When CliDeck creates a new remote Codex session, it runs:

```bash
codex --dangerously-bypass-approvals-and-sandbox
```

Then it sends `你好` and Enter to trigger the first turn. This is intentional:
remote `screen` sessions do not expose the same structured app-server approval
API as local Codex, so starting with full access keeps the web workflow smooth.

Use this only on servers and working directories you trust.

## Local Runtime Files

CliDeck writes local runtime state next to the app:

- `agent-console.toml`: SSH host configuration and optional plaintext passwords
- `agent-console-state.json`: local UI state, notification settings, and screen assignments

Both files are ignored by Git.

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests/agent_console -v
python -m py_compile app.py agent_console/*.py agent_console/collectors/*.py
```

## License

MIT
