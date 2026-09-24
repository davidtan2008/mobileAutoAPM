#!/usr/bin/env bash
# APM SessionStart hook
#
# 设计原则：**只在已初始化 APM 的工程里说话**（存在 .apm/ 目录）。
# 其他工程直接静默退出，零开销、零干扰。
#
# 输出会被加入会话上下文，所以必须**极简**：只给状态，不给教程。

set -euo pipefail

APM_DIR="${PWD}/.apm"

# 未初始化 APM 的工程 → 静默退出
[ -d "$APM_DIR" ] || exit 0

echo "── mobile-apm ─────────────────────────────"

# 1) 当前阶段与在办 issue
if [ -f "$APM_DIR/state.json" ]; then
  python3 - "$APM_DIR/state.json" <<'PY' 2>/dev/null || true
import json, sys
try:
    s = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
parts = []
if s.get("phase"):
    parts.append(f"阶段={s['phase']}")
if s.get("current_issue"):
    parts.append(f"在办={s['current_issue']}")
if s.get("updated_at"):
    parts.append(f"更新于={s['updated_at']}")
if parts:
    print("状态: " + "  ".join(parts))
PY
fi

# 2) 已有基线
if [ -d "$APM_DIR/baseline" ]; then
  bases=$(find "$APM_DIR/baseline" -name '*.json' ! -name 'env.json' 2>/dev/null \
          | xargs -n1 basename 2>/dev/null | sed 's/\.json$//' | paste -sd' ' - || true)
  [ -n "${bases:-}" ] && echo "基线: ${bases}"
fi

# 3) 未关闭的 issue 数量
if [ -d "$APM_DIR/issues" ]; then
  open_n=$(grep -l '"status": *"open"' "$APM_DIR"/issues/*.json 2>/dev/null | wc -l | tr -d ' ')
  [ "${open_n:-0}" -gt 0 ] && echo "未关闭问题: ${open_n} 个（见 .apm/issues/）"
fi

# 4) 能力就绪度（读缓存，不重跑 doctor，保证 hook 快速）
if [ -f "$APM_DIR/baseline/env.json" ]; then
  python3 - "$APM_DIR/baseline/env.json" <<'PY' 2>/dev/null || true
import json, sys
try:
    env = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
caps = env.get("capabilities", {})
ready = [k for k, v in caps.items() if v.get("ready")]
limited = [k for k, v in caps.items() if not v.get("ready")]
if ready:
    print(f"可用能力({len(ready)}): " + "、".join(ready[:6]) + ("…" if len(ready) > 6 else ""))
if limited:
    print("受限能力: " + "、".join(limited) + "（缺工具，跑 apm-doctor 查看安装方式）")
PY
fi

# 5) 提醒（仅在有过基线时提示纪律，避免对新手工程说教）
if [ -d "$APM_DIR/baseline" ] && [ -d "$APM_DIR/runs" ]; then
  echo "提醒: 改代码后须按**同口径**复测并用 apm_baseline.py 对比；无实测不得报提升。"
fi

echo "───────────────────────────────────────────"
exit 0
