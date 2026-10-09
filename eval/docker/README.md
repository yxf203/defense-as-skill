# 用 Docker 跑 PinchBench（`run_claude_code_custom.sh`）

## 你需要准备什么

1. **Docker** 与 **docker compose**（或只用 `docker build` / `docker run`）。
2. **`run/config.sh`**（不要提交密钥）：`cp run/config.example.sh run/config.sh` 后改里面的值。
   - `CC_BASE_URL`、`CC_AUTH_TOKEN`、`CC_ALIAS`、`CC_REAL_MODEL`、`CC_HOST`
   - `JUDGE`（例如 `claude`）
   - **`OUTPUT_BASE`**：`config.example.sh` 里**已经有这一行**，默认是 `"/tmp/pinchbench-out"`（适合在宿主机跑）。  
     用下方 **docker compose** 时，请**手动改成** `OUTPUT_BASE="/pinchbench-out"`（或注释掉默认行、解开 example 里 Docker 注释行）——不必「新加」变量，只要和挂载目录一致即可。

## 构建镜像

在 **`skillGuard` 目录**（本仓库含 `Dockerfile` 的那一层）执行：

```bash
cd /path/to/safety-benchmark/skillGuard
docker build -t pinchbench-skillguard .
```

`Dockerfile` 里已按 `skill-inject/docker/Dockerfile` 的方式加了 **构建期代理**（`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`、`apt` 的 `95proxies`、`npm config set proxy`）。默认指向集群内 headless 代理；你不在该网络时请加空值覆盖，例如：

```bash
docker build --build-arg HTTP_PROXY= --build-arg HTTPS_PROXY= -t pinchbench-skillguard .
```

或改成自己的代理地址：`--build-arg HTTP_PROXY=http://...`。

## 方式 A：Compose（推荐）

确认 `run/config.sh` 里 **`OUTPUT_BASE="/pinchbench-out"`**（相对默认的 `/tmp/...` 改这一处），在 **`skillGuard` 目录**（含 `docker-compose.yml`）执行。

**Compose V2（插件，子命令是空格）：**

```bash
cd /path/to/safety-benchmark/skillGuard
docker compose run --rm pinchbench ./run/run_claude_code_custom.sh task_ad_banking_ut0_it0 --verbose
```

**Compose V1（独立可执行文件，中间是连字符）：**

```bash
cd /path/to/safety-benchmark/skillGuard
docker-compose run --rm pinchbench ./run/run_claude_code_custom.sh task_ad_banking_ut0_it0 --verbose
```

若本机没有装 Compose 插件，`docker compose ...` 会报错；请先执行 `docker compose version`，不行再试 `docker-compose version`。

**不要**写成顶层的 `docker --rm compose ...`（`--rm` 只能跟在 `docker run` 或 `docker compose run` 后面，不能紧跟在 `docker` 后面），否则会出现 `unknown flag: --rm` 且打印的是 **`docker` 总帮助**（而不是 compose 的帮助）。

若 `--rm` 仍不兼容你当前的 compose 版本，可先去掉（容器退出后需自行 `docker rm` 或忽略残留一次性容器）：

```bash
docker compose run pinchbench ./run/run_claude_code_custom.sh task_ad_banking_ut0_it0 --verbose
```

评测输出（`cc_custom_*` 子目录里的 JSON、transcripts 等）与 **`benchmark.log`** 都落在宿主机 **`skillGuard/pinchbench-out-docker/`** 下：前者由 `OUTPUT_BASE=/pinchbench-out` 决定，后者通过卷挂到同目录里的 **`benchmark.log`**。

在 **`skillGuard` 目录**先准备目录和日志文件（**文件必须先存在**，否则 Docker 会误挂成目录）：

```bash
mkdir -p ./pinchbench-out-docker
touch ./pinchbench-out-docker/benchmark.log
chmod 666 ./pinchbench-out-docker/benchmark.log
# 必须：容器内用户是 uid 1000，否则无法在挂载卷里创建 cc_custom_* 等子目录
sudo chown -R 1000:1000 ./pinchbench-out-docker
```

镜像内用户为 **uid 1000**（`pinchbench`）。若宿主机目录属 root，`PermissionError: ... /pinchbench-out/...` 时就是缺了上面的 **`chown -R 1000:1000`**。

**Compose**：`docker-compose.yml` 已同时挂载 `./pinchbench-out-docker:/pinchbench-out` 与 `./pinchbench-out-docker/benchmark.log:/work/benchmark.log`，并设置 **`ASR_MATRIX_ROOT=/pinchbench-out/matrix`**，这样 **`./run/run_all_injected_matrix.sh`** 的 JSONL / `batches/` / 矩阵日志会落在宿主机 **`pinchbench-out-docker/matrix/`**（不再只留在容器内 `/work/matrix-output`）。

**纯 `docker run`** 示例见下一节（同样使用 `pinchbench-out-docker/benchmark.log`）。

### 可选：不要单独的 `benchmark.log` 文件

在 **compose** 里注释掉 `benchmark.log` 那一行；**docker run** 里去掉对应 `-v`。终端里仍会打印一份日志（StreamHandler）。

## 方式 B：纯 `docker run`

下面示例里 **三条 `-v` 都要带上**（若你希望结果和 `benchmark.log` 都落在 `pinchbench-out-docker/`）。你如果只挂了 `config.sh` 和 `pinchbench-out`，**没有**第三条，宿主机就**不会出现** `benchmark.log`。

```bash
cd /path/to/safety-benchmark/skillGuard
mkdir -p ./pinchbench-out-docker
touch ./pinchbench-out-docker/benchmark.log
chmod 666 ./pinchbench-out-docker/benchmark.log
sudo chown -R 1000:1000 ./pinchbench-out-docker

docker run --rm -it \
  -v "$PWD/run/config.sh:/work/run/config.sh:ro" \
  -v "$PWD/pinchbench-out-docker:/pinchbench-out" \
  -v "$PWD/pinchbench-out-docker/benchmark.log:/work/benchmark.log" \
  pinchbench-skillguard \
  ./run/run_claude_code_custom.sh task_ad_banking_ut0_it0 --verbose
```

镜像内已默认 **`ASR_MATRIX_ROOT=/pinchbench-out/matrix`**（`Dockerfile`），跑 **`./run/run_all_injected_matrix.sh`** 时一般**不必**再写 `-e ASR_MATRIX_ROOT=...`（改 Dockerfile 后需 **`docker build`** 重建镜像才生效）。

同样要求 **`OUTPUT_BASE=/pinchbench-out`**（见上文）。可在跑之前执行 `grep OUTPUT_BASE run/config.sh` 确认不是 `/tmp/...`。

## 镜像里已经做了什么

- Python 3.11、`pip install -e .`、`pip install agentdojo`（满足自动评分）。
- 安装 **Claude Code CLI**（`claude`）。
- 默认用户 **`pinchbench`（uid 1000，非 root）**：Claude Code 在 **root** 下会拒绝 `bypassPermissions`（报错 *`--dangerously-skip-permissions cannot be used with root/sudo`*）。
- 将 **`docker/claude.settings.json`** 复制到 **`/home/pinchbench/.claude/settings.json`**，默认 **`bypassPermissions`**，减少无交互时 Bash 被「需要批准」拦截。
- 每次 **`claude-code`** 任务的临时 workspace 内也会写入同模板的 **`workspace/.claude/settings.json`**（若任务未自带该文件），与项目级 Claude Code 约定一致。评测代码里 `claude -p` 仍传 **`acceptEdits`**；实际权限由用户级 / 项目级 settings 与 CLI 的优先级共同决定（见官方文档）。

若你的 Claude Code 版本不认该字段，请对照 [Permission modes](https://code.claude.com/docs/en/permission-modes) 自行改 JSON。

## 常见问题

- **`PermissionError: ... /pinchbench-out/cc_custom_...`**：挂载目录在宿主机上属主不是 **uid 1000**。执行：`sudo chown -R 1000:1000 ./pinchbench-out-docker`，再重跑容器。
- **`pinchbench-out-docker/` 一直是空的**：说明结果**没有写到这个挂载点**。常见原因：
  1. **`run/config.sh` 里仍是** `OUTPUT_BASE="/tmp/pinchbench-out"`（example 默认值）。用 Docker 挂载时**必须改成** `OUTPUT_BASE="/pinchbench-out"`，否则 benchmark 会写到容器内 `/tmp/...`，宿主机这个目录就收不到。
  2. **没在容器里跑**：在宿主机 `conda` 里直接跑 `./run/run_claude_code_custom.sh` 时，日志里会出现 `Saved results to /tmp/pinchbench-out/...`，文件只在**本机 `/tmp`**，不会进 `pinchbench-out-docker/`。
  3. **`docker compose` 不在 `skillGuard` 目录执行**：`volumes` 里的 `./pinchbench-out-docker` 是相对当前目录的，换目录挂载会指到别处。
  4. 跑之前记得 `mkdir -p pinchbench-out-docker` 并创建可写的 `benchmark.log`（见上文）。
  5. **`docker run` 少挂了第三条卷**：缺 `-v "$PWD/pinchbench-out-docker/benchmark.log:/work/benchmark.log"` 时，`benchmark.py` 仍往容器内 `/work/benchmark.log` 写，但**没有映射到宿主机**，容器被 `--rm` 删掉后你在宿主机上看不到该文件；宿主机的 `pinchbench-out-docker/` 里也会**没有** `benchmark.log`。
- **`unknown flag: --rm` 且只有 `docker` 总帮助**：多半是命令写成了 `docker --rm ...`，或环境里没有 **Compose V2 插件**（没有 `docker compose`）。请改用 **`docker-compose run --rm ...`**（连字符），或直接用下面的 **方式 B：`docker run --rm`**。
- **连不上自部署网关**：检查容器能否访问 `CC_BASE_URL`；Kubernetes 内网服务名在默认 bridge 网络下可能解析不到，可换 `network_mode: host` 或正确配置 DNS/`extra_hosts`。
- **改代码后要重建镜像**：`docker compose build --no-cache` / `docker-compose build --no-cache` 或 `docker build --no-cache`。
