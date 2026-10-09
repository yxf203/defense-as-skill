# 测试专用配置：复制为 config.test.sh 并填入真实 URL / Key（勿提交密钥）。
# 使用方式：Docker 里 **导出** SKILLGUARD_CONFIG=/work/run/config.test.sh
#（run_all_instances_mixed.sh、run_claude_code_task_{skill,benign}.sh 都会用它，
# 否则会回落到 run/config.sh，OUTPUT_BASE 仍会指向 /pinchbench-out）。
#
# victim agent：走 CC_*；judge：走 JUDGE（传给 benchmark --judge）。
# 二者共用同一网关时 CC_BASE_URL / CC_AUTH_TOKEN 相同，只要模型名不同即可。
#
# continuation（completion judge）默认已有 INFO 摘要日志（run.log 可见）。
# 需要完整 judge prompt/原文时：docker -e PINCHBENCH_COMPLETION_JUDGE_LOG=1 …

# Claude Code → Bedrock 等上游若报 invalid beta flag，可开启（按需）
export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1

# ---------- 与网关一致 ----------
CC_BASE_URL="https://YOUR-GATEWAY/v1"
CC_AUTH_TOKEN="<your-token>"
# NO_PROXY：填网关 hostname（无 scheme），与 CC_BASE_URL 主机名一致
CC_HOST="YOUR-GATEWAY-HOST"

# ---------- Victim（PinchBench agent：claude-code --model 别名 + 真实模型 id）----------
CC_ALIAS="haiku"
CC_REAL_MODEL="claude-haiku-4-5-20251001"

# ---------- Judge（ASR / benign comment：lib_grading 会规范成 claude:<name> 发到同一 ANTHROPIC_*）----------
JUDGE="gemini-3-flash-preview"
# 可选：让 judge 走独立网关（不填则复用 CC_BASE_URL / CC_AUTH_TOKEN）
JUDGE_BASE_URL=""
JUDGE_AUTH_TOKEN=""

# ---------- 输出目录（容器内路径；通常挂载 -v ...:/work/results）----------
OUTPUT_BASE="/work/results"
