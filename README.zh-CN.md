[English](README.md) | 中文

# CliDeck

CliDeck 是一个本机优先的 Web 控制台，用来同时监控和操作本机与 SSH 服务器上的
CLI coding agent。

它适合这种工作流：你在本机、VS Code Remote、远程 `screen` 里同时跑多个
Codex / Claude / 其他 CLI agent。CliDeck 把这些分散的终端会话放到同一个页面里：
看状态、看最近对话、继续发送 prompt、创建新的远程会话，不用在一堆终端窗口之间来回找。

## 功能

- 监控本机 Codex 和 Claude CLI transcript。
- 同时监控多个 SSH 服务器。
- 从 VS Code / OpenSSH config 导入服务器，也可以手动添加。
- 本机作为默认 host 永久保留。
- 按最近活动时间穿插显示本机和远程会话。
- 点开会话后进入聊天页面，支持 Markdown、表格和代码块。
- 在浏览器里接续本机 Codex 会话。
- 向远程 `screen` 里的 Codex 会话发送 prompt。
- 手动把会话关联到正确的远程 `screen`。
- 从浏览器创建新的远程 Codex `screen` 会话。
- 新建远程 Codex 时默认 full access，并自动发送 `你好` 触发第一轮对话。
- 支持任务完成后的桌面通知。
- 服务器配置保存在本地，不依赖云服务。

## 为什么需要它

CLI agent 很好用，但多个会话同时跑在本机和服务器上之后，状态会变得很散：

- 有的会话在等权限选择；
- 有的任务已经完成但你没看到；
- 远程 `screen` 里有回复，但网页还没刷新；
- 有价值的对话藏在 transcript 文件里；
- 继续发 prompt 又要切回对应终端。

CliDeck 的目标就是把这些分散的 CLI 进程变成一个统一的操作台。

## 快速开始

Windows 上可以直接双击：

```text
CliDeck.cmd
```

它会创建本地 `.venv`、安装依赖、启动服务，并自动打开浏览器。使用时保持启动窗口打开；
要停止服务，在窗口里按 `Ctrl+C`。

如果希望像桌面应用一样使用，不自己打开浏览器，可以双击：

```text
CliDeckDesktop.cmd
```

桌面启动器会安装可选的 `desktop` 依赖，启动同一个本地 CliDeck 服务，并在原生桌面窗口里打开控制台。

```bash
git clone https://github.com/YOUR_NAME/clideck.git
cd clideck
python -m pip install -e .
python -m uvicorn app:app --host 127.0.0.1 --port 7878
```

打开：

```text
http://127.0.0.1:7878/agent-console/
```

类 Unix 环境也可以：

```bash
bash run.sh
```

## 添加 SSH 服务器

如果你的 SSH 命令是：

```bash
ssh -p 2222 root@connect.example.com
```

填写时对应：

- host: `connect.example.com`
- user: `root`
- port: `2222`

如果希望 CliDeck 创建远程 `screen` 或向已有 `screen` 发送输入，需要开启 remote
actions。

## 远程 Codex 会话

CliDeck 创建新的远程 Codex 会话时，会运行：

```bash
codex --dangerously-bypass-approvals-and-sandbox
```

然后自动发送 `你好` 和回车，触发第一轮对话。这样远程 Codex 生成 transcript 后，
网页就可以直接识别和显示该会话。

这个模式只应该在你信任的服务器和工作目录里使用。

## 本地运行时文件

CliDeck 会在项目目录旁边写入本地运行状态：

- `agent-console.toml`：SSH host 配置和可选明文密码
- `agent-console-state.json`：UI 状态、通知设置和 screen 关联

这两个文件已被 `.gitignore` 忽略，不会被提交。

## 开发

```bash
python -m pip install -e .
python -m unittest discover -s tests/agent_console -v
python -m py_compile app.py agent_console/*.py agent_console/collectors/*.py
```

## License

MIT
