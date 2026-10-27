// node test_interactive.mjs : the note rule of the interactive pi gate (pi-agent/interactive-core.js)
import { notesNamed, newNoteState, noteBlock, noteSeen, evidenceProblems, claimTableProblems, replayEntries } from "../pi-agent/interactive-core.js"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
let fails = 0
const expect = (label, ok) => { if (!ok) fails++; console.log((ok ? "ok   " : "FAIL ") + label) }
const V = "/v"
const files = [`${V}/Project筆記/x/ESCape32_MSPM0G3507_eRPM_opencode_trial.md`, `${V}/Project筆記/x/ESCape32_MSPM0G3507_Pinmux與週邊分析.md`, `${V}/a/Other_note_one.md`, `${V}/a/Other_note_two.md`, `${V}/a/Other_note_three.md`, `${V}/a/Other_note_four.md`]
const P = "base on ESCape32_MSPM0G3507_eRPM_opencode_trial, what is the timebase on timer?"
expect("a note named in the prompt is found", notesNamed(P, files).found.join() === files[0])
expect("with .md and brackets", notesNamed("see [[ESCape32_MSPM0G3507_eRPM_opencode_trial.md]]", files).found.length === 1)
expect("plain English words match nothing", notesNamed("what is the timebase on timer and how to calculate", files).found.length === 0 && notesNamed("what is the timebase on timer", files).missing.length === 0)
expect("a name that is no note is reported missing", notesNamed("base on ESCape32_MSPM0G3507_no_such_note please", files).missing.length === 1)
expect("a vague part matching more than 3 notes is ignored", notesNamed("use Other_note", files).found.length === 0)
const st = { ...newNoteState(), required: [files[0]] }
const proj = JSON.stringify({ path: "/home/andre/workspace_ccstheia/drone/escape32_msp/src/main.c" })
expect("project is blocked until the named note is read", (noteBlock(st, proj) || "").startsWith("NOTE FIRST"))
expect("a vault search is not blocked", noteBlock(st, JSON.stringify({ command: "grep -rn x /home/andre/llm-wiki" })) === null)
noteSeen(st, "read", JSON.stringify({ path: files[0] }))
expect("after the note was read the project is open", noteBlock(st, proj) === null)
const st2 = { ...newNoteState(), missing: ["ESCape32_MSPM0G3507_no_such_note"] }
expect("a missing note is told once", (noteBlock(st2, proj) || "").startsWith("NOTE NOT FOUND") && noteBlock(st2, proj) === null)
const st3 = { ...newNoteState(), required: [files[0]] }
noteSeen(st3, "bash", JSON.stringify({ command: "ls " + files[0] }))
expect("ls of the note is not a read", noteBlock(st3, proj) !== null)

// evidence check
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ev-"))
const src = path.join(dir, "io.c")
fs.writeFileSync(src, ["int a;", "// Extended DShot Telemetry bit rate is 2.5 times DShot bit rate", "dshotarr2 = CLK_CNT(clk, 375000 << m) - 1; // Extended DShot Telemetry bit rate is 2.5 times DShot bit rate", "x = y;"].join("\n"))
const note = path.join(dir, "note.md"); fs.writeFileSync(note, "some note\nsecond line\n")
const seen = new Set([src, note])
expect("a comment-only citation is a problem", evidenceProblems("see io.c:2 for the rule", seen).some((x) => x.includes("is a comment")))
expect("a code line is fine", evidenceProblems("`io.c:4` sets x", seen).length === 0)
expect("repeating the trailing comment of a cited code line is a problem", evidenceProblems("`io.c:3` says the rate is Extended DShot Telemetry bit rate is 2.5 times DShot bit rate", seen).some((x) => x.includes("repeats the comment")))
expect("citing the code line without leaning on its comment is fine", evidenceProblems("`io.c:3` computes the reload value from 375000 << m", seen).length === 0)
expect("only notes cited is a problem", evidenceProblems("per note.md:1 it works", seen).some((x) => x.includes("note or a comment")))
expect("an unresolvable citation is ignored", evidenceProblems("see other.c:9", seen).length === 0)
expect("no citation at all is a problem only when citations are needed", evidenceProblems("it works", seen).length === 0 && evidenceProblems("it works", seen, { needCitations: true }).length === 1)
const sub = path.join(dir, "proj", "src"); fs.mkdirSync(sub, { recursive: true }); fs.writeFileSync(path.join(sub, "io2.c"), "// only a comment\nint z;\n")
expect("a bare file name is found under the directories the run worked in", evidenceProblems("see io2.c:1", new Set(), { roots: new Set([path.join(dir, "proj")]) }).some((x) => x.includes("is a comment")))
expect("an absolute path works", evidenceProblems(`${src}:2`, new Set()).some((x) => x.includes("is a comment")))

// claims table
const T = (rows) => "answer\n\n| claim | basis | how calculated | evidence | limit |\n|---|---|---|---|---|\n" + rows.join("\n") + "\n"
const sx = new Set([src])
expect("no table is a problem", claimTableProblems("just text", sx).length === 1)
expect("a verified row with a code quote that exists passes", claimTableProblems(T(["| x is set | verified | - | `dshotarr2 = CLK_CNT(clk, 375000 << m) - 1;` | none |"]), sx).length === 0)
expect("a verified row with a path only is a problem", claimTableProblems(T(["| x is set | verified | - | io.c:4 | none |"]), sx).some((x) => x.includes("backticked quote")))
expect("a verified quote that is in no opened file is a problem", claimTableProblems(T(["| x | verified | - | `zz = 99;` | none |"]), sx).some((x) => x.includes("none of the files")))
expect("a line number is not required", claimTableProblems(T(["| x | verified | - | `dshotarr2 = CLK_CNT(clk, 375000 << m) - 1;` (somewhere) | none |"]), sx).length === 0)
expect("an external row needs url and quote", claimTableProblems(T(["| r | external | - | https://example.org/a \"sent at 5/4 of the bitrate\" | web |"]), sx).length === 0 && claimTableProblems(T(["| r | external | - | the web | web |"]), sx).length === 1)
expect("an inferred row needs premises", claimTableProblems(T(["| m | inferred | - | because the old constant is 2x of the new | needs scope |"]), sx).length === 0 && claimTableProblems(T(["| m | inferred | - | - | |"]), sx).length === 1)
expect("a bad basis word is a problem", claimTableProblems(T(["| m | sure | - | x | |"]), sx).length === 1)
expect("a verified comment quote is not evidence", claimTableProblems(T(["| r | verified | - | `// Extended DShot Telemetry bit rate is 2.5 times DShot bit rate` | |"]), sx).some((x) => x.includes("comment")))

// resume: the gate state is rebuilt from the session history
const { decide, newState } = await import("/home/andre/.config/opencode/plugins/research-gate.js")
const TOOLMAP = { bash: "bash", read: "read", write: "write", edit: "edit", grep: "grep", find: "glob", ls: "list" }
const call = (id, name, args) => ({ type: "message", message: { role: "assistant", content: [{ type: "toolCall", id, name, arguments: args }] } })
const result = (id, text, isError = false) => ({ type: "message", message: { role: "toolResult", toolCallId: id, isError, content: [{ type: "text", text }] } })
const PRJ = "/home/andre/workspace_ccstheia/drone/escape32_msp/src/io.c"
const hist = [
  call("1", "read", { path: "/home/andre/.agents/skills/karpathy-guidelines/SKILL.md" }), result("1", "ok"),
  call("2", "bash", { command: "grep -rn dshot /home/andre/llm-wiki --include=*.md | head" }), result("2", "..."),
  call("3", "read", { path: PRJ }), result("3", "int a;"),
]
const rep = replayEntries(hist, { decide, newState, toolMap: TOOLMAP })
expect("replay: karpathy, vault search and the project are known after the history", rep.st.karpathy && rep.st.vault && rep.st.project)
expect("replay: the opened project file is in the seen set", rep.seen.has(PRJ))
expect("replay: a resumed session is not blocked by R1 for a project read", decide(rep.st, "read", JSON.stringify({ path: PRJ })).block === null)
expect("replay: a fresh session still is", String(decide(newState(), "read", JSON.stringify({ path: PRJ })).block).startsWith("GATE R1"))
const blocked = [call("1", "read", { path: PRJ }), result("1", "GATE R1: blocked. Done so far: karpathy=false", true)]
expect("replay: a call that was blocked establishes nothing", !replayEntries(blocked, { decide, newState, toolMap: TOOLMAP }).st.project)
const pre = [call("a", "read", { path: "/home/andre/.agents/skills/karpathy-guidelines/SKILL.md" }), result("a", "ok"), call("b", "bash", { command: "grep -rn x /home/andre/llm-wiki | head" }), result("b", "...")]
const okGate = [...pre, call("1", "read", { path: PRJ }), result("1", "GATE-style text in a successful file read")]
expect("replay: a successful result that happens to start with GATE is not a block", replayEntries(okGate, { decide, newState, toolMap: TOOLMAP }).st.project)
const noResult = [...pre, call("1", "read", { path: PRJ })]
expect("replay: a call without a result (never finished) is not counted", !replayEntries(noResult, { decide, newState, toolMap: TOOLMAP }).st.project)
const failedButAllowed = [...pre, call("1", "read", { path: PRJ }), result("1", "ENOENT: no such file", true)]
expect("replay: an allowed call that failed still counts (the gate saw it before it ran)", replayEntries(failedButAllowed, { decide, newState, toolMap: TOOLMAP }).st.project)
expect("replay: an empty or missing history gives the fresh state", replayEntries(undefined, { decide, newState }).st.karpathy === false)
const realSession = os.homedir() + "/.pi/agent/sessions/--home-andre-llm-wiki--/2026-10-22T14-42-31-702Z_01a16410-4a96-7201-94fc-ec205e4a8e94.jsonl"
if (fs.existsSync(realSession)) {
  const entries = fs.readFileSync(realSession, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l))
  const r = replayEntries(entries, { decide, newState, toolMap: TOOLMAP })
  expect("replay: the real e2e session gives karpathy, vault, project and SDK", r.st.karpathy && r.st.vault && r.st.project && r.st.sdk && r.calls > 20)
}
console.log(fails ? fails + " FAILED" : "all passed")
process.exit(fails ? 1 : 0)
