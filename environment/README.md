# AgentForge Python 开发环境

准备日期：2026-11-09。使用现有 Python 3.13.12、现有 PostgreSQL 容器和 agentforge 数据库。本目录负责开发依赖与环境验证脚本。项目根目录已增加可启动的最小 API/CLI，使用方式见根 README；任务业务、Runner 和生产部署仍待实现。

## 开始使用

在 PowerShell 中进入 `D:/AgentForge`：

```powershell
.\environment\dev.ps1 run python --version
.\environment\dev.ps1 run pytest --version
.\environment\dev.ps1 run rg --version
.\environment\dev.ps1 sync --locked
```

`dev.ps1` 自动定位 uv、项目 `.venv` 和独立 rg，禁用 Python 下载。首次创建 `.venv` 使用已有 `D:/developTools/miniconda3/envs/langchain1.2/python.exe`；依赖缓存位于 `D:/AgentForge-Tools/uv-cache`，依赖以 `uv.lock` 固定。没有改动 Conda base 或 langchain1.2 的已安装包。

uv/rg 的全局 PATH 调整未执行，统一使用上述项目入口。已有 PowerShell 进程不会自动获得其他安装程序更新的 PATH。

## 验证环境

基础检查（包导入、FastAPI 请求校验、OpenAPI、Markdown、PPTX 生成和备注）：

```powershell
.\environment\dev.ps1 run python environment/verify.py
```

数据库和完整渲染检查：

```powershell
.\environment\dev.ps1 run python environment/verify.py --database-container agentforge-postgres-1 --renderer-image agentforge-renderer:dev-20261109
```

结果写到 `environment/results/verification.json`，渲染样本写到 `environment/results/artifacts/`。完整检查临时在现有数据库中创建随机 `af_envcheck_*` schema，用于真实队列任务与 checkpoint 重连测试，随后清理；不创建数据库、不迁移 app/queue/graph 业务 schema。检查通过 Docker 内存读取当前容器的连接信息，使用既有管理员连接，仅服务于环境验证；不会把密码打印或写入项目。这不是未来应用的连接方式，应用仍需使用现有分权角色及明确授权。

当前 Windows 队列测试关闭信号处理，采用单并发，只验证开发兼容性；生产 Linux Worker、故障恢复、权限边界与真实任务验收仍须单独完成。

```powershell
.\environment\dev.ps1 pip check
.\environment\dev.ps1 run ruff check environment/verify.py
.\environment\dev.ps1 run ruff format --check environment/verify.py
.\environment\dev.ps1 run mypy --follow-imports=silent --ignore-missing-imports environment/verify.py
```

## 渲染与受限执行

镜像 `agentforge-renderer:dev-20261109` 包含 LibreOffice Impress、Poppler、Noto CJK 字体、Git、ripgrep。镜像默认非 root，验证时禁用网络、只读根文件系统、移除 capabilities，并限制内存/CPU/PID。仅挂载验证输出目录。

```powershell
docker build --pull=false -f environment/Dockerfile.renderer -t agentforge-renderer:dev-20261109 environment
```

Docker Hub 的 Debian 基础镜像拉取失败后，开发镜像复用了本机已有的 Debian 系列 PostgreSQL 镜像，改用渲染入口，没有启动数据库；基础镜像含额外数据库文件和继承 volume，正式发行前应改用专门的最小渲染基底并重新验收。基础镜像 digest 已固定，本次构建标识记录于 `environment/images.json`；apt 软件源会变化，重建不等于逐字节复现原镜像。没有替换或重启现有数据库容器。

该镜像验证了受限 shell/Git/检索/渲染工具，完整多语言代码 executor 和 Runner 服务仍属于项目实现任务。

## 本地模型

已启动本机已有 Ollama，只监听 `127.0.0.1:11434`，单并发、最多加载一个模型、keep_alive=0。复用已有 `deepseek-r1:1.5b`，未下载新模型。真实推理完成，但一次 128 token 限额的合成工具测试未得到预期调用，不能认定适合自动 Agent 工具执行；也不能据此断言该模型所有配置都不支持工具。结果见 `environment/results/ollama.json`。

远程提供方密钥、具体模型与真实任务质量尚未配置/验收。没有创建系统自启服务；本次启动的 Ollama 会在本机后台提供 API，重启电脑后按需启动。

## 文件与已有状态

新建 pyproject.toml、uv.lock、.python-version、environment/ 和项目 .venv；本地排除规则追加到 `.git/info/exclude`，防止虚拟环境、凭据和测试输出进入版本管理。原受管文件删除状态保留，没有恢复旧应用代码或被删除的部署配置。

用户要求复用现有环境后，Python 3.12 主机下载进程已停止。此前中断时已拉取的 `python:3.12-slim-bookworm` 镜像不参与本项目；自动审批拒绝了包含清理镜像/修改用户 PATH 的组合命令，因此未清理该镜像，也未修改全局 PATH。
