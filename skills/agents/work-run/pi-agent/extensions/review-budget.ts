// Time and tool budget for the picky reviewer. Active only when REVIEW_BUDGET_CALLS or REVIEW_BUDGET_SECONDS is set.
// After the call budget the next tool call is refused ("write the challenge now"); after the time budget the run is aborted.
// @ts-nocheck
export default function (pi) {
  const maxCalls = Number(process.env.REVIEW_BUDGET_CALLS || 0)
  const maxSeconds = Number(process.env.REVIEW_BUDGET_SECONDS || 0)
  if (!maxCalls && !maxSeconds) return
  let calls = 0, timer = null
  pi.on("input", async () => { calls = 0; return undefined })
  pi.on("agent_start", async (_e, ctx) => {
    if (timer) clearTimeout(timer)
    if (maxSeconds) timer = setTimeout(() => { try { ctx.abort() } catch {} }, maxSeconds * 1000)
    return undefined
  })
  pi.on("agent_end", async () => { if (timer) clearTimeout(timer); timer = null; return undefined })
  pi.on("tool_call", async () => {
    calls += 1
    if (maxCalls && calls > maxCalls) return { block: true, reason: "REVIEW BUDGET USED (" + maxCalls + " tool calls). Write your challenge now from what you have read; do not read more." }
    return undefined
  })
}
