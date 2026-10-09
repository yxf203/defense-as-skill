# Dataset setup (required before running)

The evaluation framework resolves **all** dataset directories relative to this
`eval/` root (same layout convention as the original internal project):

```
eval/               ← "skill root" (auto-derived from scripts/.. )
├── run/                 ← run/config.sh, per-task runner scripts
├── subsets/             ← subset drivers (smoke / coverage / benign / combined)
├── scripts/             ← benchmark.py + lib_*.py evaluation core
├── safetySkill/         ← skill-sonar guard skill
├── tasks/               ← 📥 place dataset dirs here (see below)
├── injected-skills/     ← 📥
├── attack-metadata/     ← 📥
├── benign-skills/       ← 📥
└── split/               ← 📥
```

## One-time setup

Download the **SCOPE-R** dataset (hosted on Hugging Face) and copy its data
directories into this `eval/` root:

```bash
# option A: git clone (no HF account needed for public datasets)
git clone https://huggingface.co/datasets/<org>/SCOPE-R /tmp/scope-r
cp -r /tmp/scope-r/dataset/{tasks,injected-skills,attack-metadata,benign-skills,split} .

# option B: huggingface_hub
python3 -c "
from huggingface_hub import snapshot_download
p = snapshot_download(repo_id='<org>/SCOPE-R', repo_type='dataset')
print(p)
"
cp -r <printed-path>/dataset/{tasks,injected-skills,attack-metadata,benign-skills,split} .
```

**Note on `split/`:** two MCTS eval lists ship with this repo
(`cheap_eval_instances.jsonl`, `full_eval_instances.jsonl`) — do **not**
overwrite them when copying the dataset's `split/` in; simply add the
dataset's files alongside (they coexist in the same directory).

Then copy the run config template and fill in your endpoint credentials:

```bash
cp run/config.example.sh run/config.sh   # edit ANTHROPIC_BASE_URL / AUTH_TOKEN etc.
```

## Why relative layout

`benchmark.py` derives `skill_root = scripts/..`, and
`run/run_claude_code_task_skill.sh` derives `SKILL_DIR = run/..`. Both expect
`tasks/`, `injected-skills/`, `attack-metadata/`, `benign-skills/`, `split/`
to live directly under `eval/`. Keeping this layout means **zero path
configuration** — the whole pipeline (evolution loop → subset driver →
benchmark runner → judge) works out of the box.
