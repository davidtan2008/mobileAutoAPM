// APM hook 桥接：让 opencode 复用 Claude Code 的 hook 脚本。
//
// opencode 没有 settings hooks，只有插件 API。这里把 .claude/hooks/*.sh
// 当子进程调用，从而**同一份 hook 逻辑同时服务两个 harness**。
//
// 约定（与 Claude Code 对齐）：脚本读 stdin 的 JSON，exit 2 表示阻塞。

import type { Plugin } from "@opencode-ai/plugin"

export const ApmHookBridge: Plugin = async ({ $, directory }) => {
  const runHook = async (script: string, payload: unknown) => {
    const path = `${directory}/${script}`
    const proc = Bun.spawn(["bash", path], {
      stdin: "pipe", stdout: "pipe", stderr: "pipe",
    })
    proc.stdin.write(JSON.stringify(payload))
    proc.stdin.end()
    const code = await proc.exited
    const out = await new Response(proc.stdout).text()
    return { code, out }
  }

  return {
    // 会话开始：注入 APM 状态（与 Claude Code 的 SessionStart 等价）
    event: async ({ event }) => {
      if (event.type !== "session.created") return
      const { code, out } = await runHook(
        ".claude/hooks/apm-session-start.sh",
        { event: "SessionStart", cwd: directory },
      )
      if (code === 0 && out.trim()) {
        // 交给模型作为上下文
        return { context: out.trim() }
      }
    },
  }
}
