// Interactive gate for pi: the opencode plugin's normal-mode rules (decide: karpathy and a vault search before the project or the SDK, the
// peripheral needs its TRM note and SDK header, no execute tool) plus: a note the user names in the prompt is read first.
// Off when GATE_FILE is set (work-run has its own gate) or PI_INTERACTIVE_GATE=off. Decisions go to ~/.local/state/opencode-gate/gate.jsonl.
// @ts-nocheck
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { execFile } from "node:child_process"
import { decide, newState } from "/home/andre/.config/opencode/plugins/research-gate.js"
import { CLAIM_TABLE_FORMAT, claimTableProblems, evidenceProblems, newNoteState, noteBlock, noteSeen, notesNamed, replayEntries, touched, VAULT } from "/home/andre/.agents/skills/work-run/pi-agent/interactive-core.js"

const TOOL = { bash: "bash", read: "read", write: "write", edit: "edit", grep: "grep", find: "glob", ls: "list" }
const SEMCHECK = "/home/andre/.agents/skills/work-run/scripts/semcheck.py"
const LOG = path.join(os.homedir(), ".local", "state", "opencode-gate", "gate.jsonl")
const audit = (rec) => { try { fs.mkdirSync(path.dirname(LOG), { recursive: true }); fs.appendFileSync(LOG, JSON.stringify({ ts: new Date().toISOString(), ...rec }) + "\n") } catch {} }

// Semantic check of the final answer's `verified` rows by a local model (semcheck.py: thinking-off pass, then a thinking pass for the rows it flagged). Report only.
const runSemcheck = (answer, files, sid) => new Promise((resolve) => {
  try {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "semcheck-"))
    const a = path.join(dir, "answer.md"), l = path.join(dir, "seen.txt")
    fs.writeFileSync(a, answer); fs.writeFileSync(l, [...files].join("\n"))
    // semcheck.py bounds its own thinking pass (SEMCHECK_BUDGET, default 420 s); this timeout is the outer limit. A failure is reported, never swallowed.
    execFile("python3", [SEMCHECK, "--answer", a, "--seen-file", l], { timeout: 720000, maxBuffer: 1 << 20 }, (err, out, errOut) => {
      try { fs.rmSync(dir, { recursive: true, force: true }) } catch {}
      if (err) resolve("[SEMCHECK] did not run: " + (err.killed ? "timed out after 12 min" : "exit " + (err.code ?? "?")) + (errOut ? " - " + String(errOut).trim().split("\n").slice(-2).join(" | ").slice(0, 300) : ""))
      else if (!String(out).trim()) resolve("[SEMCHECK] did not run: no output from semcheck.py")
      else resolve(String(out).trim())
    })
  } catch (e) { resolve("[SEMCHECK] did not run: " + String(e).slice(0, 200)) }
})

export default function (pi) {
  if (process.env.GATE_FILE || (process.env.PI_INTERACTIVE_GATE || "on") === "off") return
  let st = newState()
  let notes = newNoteState()
  const sid = "pi-interactive-" + Date.now().toString(36) + "-" + process.pid // several panes can run at once; the pid keeps their audit lines apart
  // evidence check on the final answer: a comment or a note is a claim, not evidence (at most 3 reminders per prompt)
  let seen = new Set(), roots = new Set(), worked = false, evNudges = 0, semDone = false
  const reset = () => { st = newState(); notes = newNoteState(); seen = new Set(); roots = new Set(); worked = false; evNudges = 0; semDone = false }
  // A resumed or forked session keeps what it already did (karpathy, vault search, peripheral notes, opened files): replay its history instead of starting from zero.
  pi.on("session_start", async (event, ctx) => {
    reset()
    try {
      const r = replayEntries(ctx.sessionManager.getBranch(), { decide, newState, toolMap: TOOL })
      st = r.st; seen = r.seen; roots = r.roots
      if (r.calls) audit({ session: sid, agent: "pi-interactive", tool: "session-replay", blocked: false, input: JSON.stringify({ reason: event && event.reason, calls: r.calls, karpathy: st.karpathy, vault: st.vault, project: st.project, sdk: st.sdk }) })
    } catch {}
  })
  pi.on("input", async (event) => {
    const { found, missing } = notesNamed(event.text)
    notes = { ...newNoteState(), required: found, missing, read: notes.read }
    worked = false; evNudges = 0; semDone = false
    if (found.length || missing.length) audit({ session: sid, agent: "pi-interactive", tool: "prompt", blocked: false, input: JSON.stringify({ notes: found, missing }) })
    return undefined
  })
  pi.on("tool_call", async (event) => {
    const tool = TOOL[event.toolName] ?? event.toolName
    const s = JSON.stringify(event.input ?? {})
    let block = noteBlock(notes, s)
    let next = st
    if (!block) {
      const r = decide(st, tool, s)
      block = r.block
      next = r.next
    }
    audit({ session: sid, agent: "pi-interactive", tool, blocked: !!block, rule: block ? block.split(":")[0] : null, reason: block || undefined, input: s.slice(0, 300) })
    if (block) return { block: true, reason: block }
    noteSeen(notes, tool, s)
    const t = touched(tool, event.input)
    t.files.forEach((f) => seen.add(f)); t.dirs.forEach((d) => roots.add(d))
    if (tool !== "review_disposition") worked = true
    if (notes.required.length && notes.required.every((n) => notes.read.has(n))) next.vault = true // the named note counts as the vault look-up
    st = next
    return undefined
  })
  pi.on("agent_before_settle", async (event, ctx) => {
    if (event.outcome !== "completed" || !worked) return undefined
    let answer = ""
    try {
      const entries = ctx.sessionManager.getBranch()
      for (let i = entries.length - 1; i >= 0; i--) {
        const m = entries[i]?.message
        if (entries[i].type === "message" && m?.role === "assistant" && m.stopReason !== "toolUse") { answer = (m.content || []).filter((c) => c.type === "text").map((c) => c.text).join("\n"); break }
      }
    } catch {}
    if (!answer) return undefined
    if (evNudges < 3) {
      const problems = evidenceProblems(answer, seen, { needCitations: false, roots })
      const tableProblems = (process.env.PI_CLAIM_TABLE || "on") !== "off" && (st.project || st.sdk) ? claimTableProblems(answer, seen, { roots }) : []
      problems.push(...tableProblems)
      if (problems.length) {
        evNudges += 1
        audit({ session: sid, agent: "pi-interactive", tool: "final-answer", blocked: true, rule: "EVIDENCE", reason: problems.join("; "), input: "" })
        return {
          entries: [{ type: "custom_message", customType: "evidence-gate", display: true,
            content: "EVIDENCE CHECK (" + evNudges + "/3): " + problems.join("; ") + ". A code comment, a variable name or a note is a claim, not evidence. Re-derive the conclusion from executable code, the original specification text or a measurement. If you cannot, mark that conclusion inferred and say what would confirm it. " + (tableProblems.length ? CLAIM_TABLE_FORMAT + " " : "") + "Then give the answer again." }],
          continue: true,
        }
      }
    }
    // form and evidence are settled (accepted, or the three reminders are used up): the semantic check runs once on this answer, report only
    if ((process.env.PI_SEMCHECK || "off") === "on" && !semDone) {
      semDone = true
      const t0 = Date.now()
      const report = await runSemcheck(answer, seen, sid)
      audit({ session: sid, agent: "pi-interactive", tool: "semcheck", blocked: false, input: report.slice(0, 600), reason: Math.round((Date.now() - t0) / 1000) + "s" })
      if (report) return { entries: [{ type: "custom_message", customType: "semcheck", display: true, content: report + "\n(Local-model check of the quoted evidence. Report only: confirm these rows or mark them inferred; nothing was blocked.)" }] }
    }
    return undefined
  })
}
