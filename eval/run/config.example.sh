# 复制为 config.sh 然后改成你的真实值。
# run_*.sh 会在启动时 `source ./config.sh` 加载这些。
#
# cp run/config.example.sh run/config.sh && vim run/config.sh

# ============================================================================
# OpenClaw 后端（自定义中转）—— 见 run_openclaw.sh
# ============================================================================
# OPENCLAW_MODEL="glm-4.6"
# OPENCLAW_BASE_URL="http://34.13.73.248:3888/v1"
# OPENCLAW_API_KEY="<your-api-key>"
# # 推理类模型必须 "true"（GLM 系 / qwen3.6-plus 等）；OpenAI 兼容模型设 "false"
# OPENCLAW_NO_STREAM="true"
# # 推理类模型建议 5；OpenAI 兼容模型保持 1
# OPENCLAW_TIMEOUT_MULT="5"

# ============================================================================
# Claude Code 后端（官方模型）—— 见 run_claude_code.sh
# ============================================================================
# 当前 shell 必须已经 `claude login`，且 http_proxy/https_proxy 指向你的代理
CLAUDE_OFFICIAL_MODEL="sonnet"  # 可选: sonnet / opus / haiku
# 无头 claude -p 的权限模式（agent 与 ASR judge 共用）。默认 bypassPermissions（与 docker/claude.settings.json 一致）。
# 在宿主机以 root 跑若被拒，可设 acceptEdits 或 project（不写 --permission-mode，只靠 .claude/settings.json）。
# export PINCHBENCH_CLAUDE_PERMISSION_MODE="acceptEdits"
# Claude Code 采样等：改仓库里 docker/claude.settings.json（会复制到任务 workspace/.claude/settings.json）。

# ============================================================================
# Claude Code 后端（自部署模型，env 重定向）—— 见 run_claude_code_custom.sh
# ============================================================================
CC_BASE_URL="http://<your-internal-host>"
CC_AUTH_TOKEN="<your-token>"
# alias 选 haiku/sonnet/opus 之一，下面 3 个 env 选对应的一个填即可
CC_ALIAS="haiku"
CC_REAL_MODEL="glm5"
# 自部署端点的 host（NO_PROXY 用，让 node fetch 对这个 host 绕过 Clash）
CC_HOST="<your-internal-host>"

# ============================================================================
# 通用（PinchBench --judge，与 agent 的 --model 独立）
# ============================================================================
# 传给 benchmark --judge：OpenClaw 后端用 openrouter/...；Claude Code 后端用 haiku|sonnet|opus|claude|claude:…（claude -p）
JUDGE="haiku"
# automated 任务不设 JUDGE 则不做 ASR；llm_judge / hybrid 任务也需要 JUDGE

# ============================================================================
# task-skill 注入实验 —— 见 run_claude_code_task_skill.sh
# ============================================================================
# 与 attack-metadata/<TASKSKILL_INJECTED_PATH>/attack_metadata.json 对齐
TASKSKILL_INJECTED_PATH="system-execution/unapproved_command_execution/stock-research"
# 可选：仅当希望 ASR 判分模型与上面 JUDGE 不同时填写；留空则 task-skill 脚本也用 JUDGE
# TASKSKILL_JUDGE_MODEL="sonnet"

# ASR 注入矩阵断点续跑（run_all_injected_matrix.sh）：跳过 JSONL 里已成功的格子
# export ASR_MATRIX_RESUME=1

# 结果目录：在宿主机直接跑时，默认写到 /tmp（本机任意可写路径均可）。
# OUTPUT_BASE="/tmp/pinchbench-out"
# 若用本仓库的 docker-compose.yml（把宿主机 ./pinchbench-out-docker 挂到容器内
# /pinchbench-out），请改成下面这一行，与挂载点一致（二选一，不要两行同时生效）：
OUTPUT_BASE="/pinchbench-out"
