// Rules for the interactive pi gate that the opencode plugin's decide() does not have: a note the user names in the prompt is read before the
// project or the SDK is touched. Pure functions; the extension (~/.pi/agent/extensions/interactive-gate.ts) wires them to pi.
import fs from "node:fs"
import path from "node:path"

export const VAULT = "/home/andre/llm-wiki"
export const RE_PROJECT = /\/home\/andre\/workspace_ccstheia(\/|["\s]|$)/
export const RE_SDK = /\/home\/andre\/ti(\/|["\s]|$)/

let cache = null
export function vaultFiles(root = VAULT) {
  if (cache && cache.root === root && Date.now() - cache.t < 60000) return cache.files
  let files = []
  try { files = fs.readdirSync(root, { recursive: true }).map(String).filter((f) => f.endsWith(".md") && !f.split("/").some((p) => p.startsWith(".") || p === "node_modules")).map((f) => path.join(root, f)) } catch {}
  cache = { root, t: Date.now(), files }
  return files
}

// Tokens of the prompt that name a vault note: the file name without .md, or a long unique part of it. found: absolute paths. missing: tokens
// that look like a note name (underscores or .md) but match no file.
export function notesNamed(text, files = vaultFiles()) {
  const found = [], missing = []
  const toks = new Set(String(text || "").match(/[\p{L}\p{N}_\-.（）()·+]{10,}/gu) || [])
  for (let tok of toks) {
    tok = tok.replace(/\.md$/i, "").replace(/^[.\-]+|[.\-]+$/g, "")
    if (tok.length < 10 || !(tok.includes("_") || /[一-鿿]/.test(tok))) continue
    const low = tok.toLowerCase()
    const base = (f) => path.basename(f, ".md").toLowerCase()
    let hit = files.filter((f) => base(f) === low)
    if (!hit.length && low.length >= 12) hit = files.filter((f) => base(f).includes(low))
    if (hit.length > 3) continue // too vague to be a name
    if (hit.length) hit.forEach((f) => { if (!found.includes(f)) found.push(f) })
    else if ((tok.match(/_/g) || []).length >= 2 && tok.length >= 15) missing.push(tok)
  }
  return { found, missing }
}

export function newNoteState() { return { required: [], read: new Set(), missing: [], missingTold: false } }

export function noteBlock(state, s) {
  if (!state.required.length && !state.missing.length) return null
  const touches = RE_PROJECT.test(s) || RE_SDK.test(s)
  if (!touches) return null
  if (state.missing.length && !state.missingTold) {
    state.missingTold = true
    return "NOTE NOT FOUND: the prompt names " + state.missing.map((m) => "`" + m + "`").join(", ") + " but no note in the vault " + VAULT + " has that name. Tell the user this in your answer, search the vault by title keywords, and say which note (if any) you used instead. Then retry."
  }
  const pending = state.required.filter((n) => !state.read.has(n))
  if (pending.length) return "NOTE FIRST: the user told you to base the answer on " + pending.join(", ") + ". Read it (the read tool) before the project or the SDK, then retry."
  return null
}

export function noteSeen(state, tool, s) {
  for (const n of state.required) if (s.includes(n) && (tool === "read" || /\b(cat|sed|head|tail|nl|awk)\b/.test(s))) state.read.add(n)
}

// ---- evidence check: a code comment or a note is a claim, not evidence --------------------------------------------------------------------
const CODE_EXT = /\.(c|h|cc|cpp|hpp|inc|s|asm|ld|cmd|syscfg|py|sh|js|ts|json|ya?ml|toml|txt)$/i
const HASH_COMMENT = /\.(py|sh|ya?ml|toml)$/i
const CITE = /((?:~|\/)?[\w.\-\/一-鿿()+]*?[\w\-一-鿿]\.(?:c|h|cc|cpp|hpp|inc|s|asm|ld|cmd|syscfg|py|sh|js|ts|json|ya?ml|toml|txt|md)):(\d+)(?:\s*[-–]\s*(\d+))?/g
const norm = (x) => String(x).toLowerCase().replace(/\s+/g, " ").trim()

export function commentOf(line, file) {
  const t = line.trim()
  if (t.startsWith("//") || t.startsWith("/*") || t.startsWith("*") || (HASH_COMMENT.test(file) && t.startsWith("#"))) return { only: true, text: t.replace(/^(\/\/+|\/\*+|\*+|#+)\s*/, "").replace(/\*\/\s*$/, "") }
  const m = t.match(/\/\/(.*)$|\/\*(.*?)\*\//)
  return { only: false, text: m ? (m[1] ?? m[2] ?? "").trim() : "" }
}

// seen: absolute paths the run has opened or named. Returns the problems found in the final answer; [] when the evidence is acceptable.
export function evidenceProblems(answer, seen, { needCitations = false, roots = [] } = {}) {
  const text = String(answer || "")
  const problems = []
  let codeCites = 0, cites = 0
  const answerN = norm(text)
  const resolve = (p) => {
    p = p.trim().replace(/^~/, process.env.HOME || "")
    if (p.startsWith("/")) return fs.existsSync(p) ? p : null
    const hits = [...seen].filter((s) => s.endsWith("/" + p) || path.basename(s) === p)
    if (hits.length === 1) return hits[0]
    const joined = [...roots].map((r) => path.join(r, p)).filter((f) => fs.existsSync(f))
    if (joined.length === 1) return joined[0]
    const found = new Set()  // a bare file name (io.c:212): look for it under the directories the run worked in
    const walk = (d, depth) => { if (depth < 0 || found.size > 1) return; let es = []; try { es = fs.readdirSync(d, { withFileTypes: true }) } catch { return } for (const e of es) { if (e.name.startsWith(".")) continue; const f = path.join(d, e.name); if (e.isDirectory()) walk(f, depth - 1); else if (e.name === path.basename(p) && f.endsWith("/" + p)) found.add(f) } }
    for (const r of roots) walk(r, 4)
    return found.size === 1 ? [...found][0] : null
  }
  for (const m of text.matchAll(CITE)) {
    const file = resolve(m[1]); if (!file) continue
    let lines; try { lines = fs.readFileSync(file, "utf8").split("\n") } catch { continue }
    const a = Number(m[2]), b = Math.min(Number(m[3] || m[2]), lines.length)
    if (a < 1 || a > lines.length) continue
    cites++
    const slice = lines.slice(a - 1, b)
    const base = path.basename(file)
    if (!CODE_EXT.test(file)) continue // a note (.md) is an index, not evidence: it never counts as a code citation
    const info = slice.map((l) => commentOf(l, file))
    if (slice.every((l, i) => !l.trim() || info[i].only)) problems.push(`${base}:${a}${b > a ? "-" + b : ""} is a comment, not code`)
    else codeCites++
    for (const c of info) if (c.text.length >= 10 && answerN.includes(norm(c.text))) problems.push(`${base}:${a}${b > a ? "-" + b : ""}: the answer repeats the comment "${c.text.slice(0, 60)}" as if it were evidence`)
  }
  if (!problems.length && cites > 0 && codeCites === 0) problems.push("every citation is a note or a comment; none is executable code or a specification")
  if (!problems.length && needCitations && cites === 0) problems.push("the answer cites no file:line at all")
  return [...new Set(problems)]
}

// What a tool call opens or names: absolute file paths, and the directories a `cd` moved into (relative citations are resolved against them).
export function touched(tool, input) {
  const files = new Set(), dirs = new Set()
  const j = input || {}
  for (const k of ["path", "filePath"]) if (typeof j[k] === "string" && j[k].startsWith("/")) (CODE_EXT.test(j[k]) || /\.md$/.test(j[k]) ? files : dirs).add(j[k])
  const cmd = typeof j.command === "string" ? j.command : ""
  for (const m of cmd.matchAll(/(?:^|[\s"'=:])(\/[^\s"'|;&<>()]+)/g)) (CODE_EXT.test(m[1]) || /\.md$/.test(m[1]) ? files : dirs).add(m[1].replace(/\/$/, ""))
  for (const m of cmd.matchAll(/\bcd\s+("[^"]+"|'[^']+'|\S+)/g)) { const d = m[1].replace(/^["']|["']$/g, ""); if (d.startsWith("/")) dirs.add(d.replace(/\/$/, "")) }
  return { files, dirs }
}

// ---- claims table: the answer ends with a table that states, per claim, what it rests on (so a reviewer can audit evidence, not re-solve) --------
export const CLAIM_TABLE_FORMAT = "Add a markdown table `| claim | basis | how calculated | evidence | limit |` with one row per claim the answer depends on. basis = verified (executable code: put a backticked quote of the code in evidence, a line number is welcome but not required), external (a specification or web page: put the URL and a quote), or inferred (say the premises in evidence). A comment, a variable name or a note is never `verified`. 'how calculated' gives inputs, formula and result for every number. 'limit' says what stays unconfirmed."

export function claimTableProblems(answer, seen, { roots = [] } = {}) {
  const lines = String(answer || "").split("\n")
  const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim())
  let h = -1
  for (let i = 0; i < lines.length - 1; i++) {
    if (!lines[i].includes("|") || !/^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) continue
    const names = cells(lines[i]).map((c) => c.toLowerCase())
    if (names.some((n) => n.startsWith("claim")) && names.some((n) => n.startsWith("basis")) && names.some((n) => n.startsWith("evidence"))) { h = i; break }
  }
  if (h < 0) return ["the answer has no claims table (| claim | basis | how calculated | evidence | limit |)"]
  const names = cells(lines[h]).map((c) => c.toLowerCase())
  const ci = names.findIndex((n) => n.startsWith("claim")), bi = names.findIndex((n) => n.startsWith("basis")), ei = names.findIndex((n) => n.startsWith("evidence"))
  const files = [...seen].filter((f) => { try { return fs.statSync(f).isFile() } catch { return false } })
  const corpus = () => files.map((f) => { try { return norm(fs.readFileSync(f, "utf8")) } catch { return "" } }).join("\n")
  let text = null
  const problems = []
  let rows = 0
  for (let i = h + 2; i < lines.length && lines[i].includes("|"); i++) {
    const c = cells(lines[i]); rows++
    const claim = (c[ci] || "").slice(0, 50), basis = (c[bi] || "").toLowerCase().replace(/[`*]/g, ""), ev = c[ei] || ""
    const quotes = [...ev.matchAll(/`([^`]{8,})`|"([^"]{12,})"|“([^”]{12,})”/g)].map((m) => m[1] || m[2] || m[3])
    if (basis.startsWith("verified")) {
      const codeQuotes = quotes.filter((q) => !/^[\w./~-]+(:\d+(-\d+)?)?$/.test(q))
      if (!codeQuotes.length) { problems.push(`row "${claim}": verified needs a backticked quote of the code in evidence`); continue }
      text ??= corpus()
      const found = codeQuotes.some((q) => text.includes(norm(q).replace(/\s*\.\.\.\s*/g, "")))
      if (!found) { problems.push(`row "${claim}": the quoted code is in none of the files this run opened`); continue }
      const onlyComment = codeQuotes.every((q) => /^\s*(\/\/|\/\*|\*|#)/.test(q))
      if (onlyComment) problems.push(`row "${claim}": a comment is not verified evidence`)
    } else if (basis.startsWith("external")) {
      if (!/https?:\/\//.test(ev) || !quotes.length) problems.push(`row "${claim}": external needs a URL and a quote`)
    } else if (basis.startsWith("inferred")) {
      if (ev.replace(/[`*\s]/g, "").length < 10) problems.push(`row "${claim}": inferred needs its premises in evidence`)
    } else if (!basis.startsWith("unverified")) problems.push(`row "${claim}": basis must be verified, external, inferred or unverified`)
  }
  if (!rows) problems.push("the claims table has no rows")
  return problems
}

// ---- resume: rebuild what the gate knew from a session's history --------------------------------------------------------------------------
// The gate state lives in memory, so a resumed or forked session would start from zero and be blocked for things it already did. The history holds every tool call and its
// result: replay the calls that were allowed. A call is skipped when it has no result (it never finished, so what it established is unknown: the gate asks again, the safe direction)
// or when its result is an error that starts with GATE or NOTE (the gate blocked it). A successful result is never read as a block, whatever its text says.
export function replayEntries(entries, { decide, newState, toolMap = {}, touchedFn = touched }) {
  let st = newState()
  const seen = new Set(), roots = new Set()
  const results = new Map()
  for (const e of entries || []) {
    const m = e && e.type === "message" ? e.message : null
    if (m && m.role === "toolResult") results.set(m.toolCallId, { text: (Array.isArray(m.content) ? m.content : []).map((c) => (c && c.text) || "").join(""), isError: m.isError === true })
  }
  let calls = 0
  for (const e of entries || []) {
    const m = e && e.type === "message" ? e.message : null
    if (!m || m.role !== "assistant") continue
    for (const c of m.content || []) {
      if (!c || c.type !== "toolCall") continue
      const res = results.get(c.id)
      if (!res || (res.isError && /^\s*(GATE|NOTE )/.test(res.text))) continue
      const tool = toolMap[c.name] ?? c.name
      const r = decide(st, tool, JSON.stringify(c.arguments ?? {}))
      if (r.block) continue
      st = r.next
      const t = touchedFn(tool, c.arguments)
      t.files.forEach((f) => seen.add(f)); t.dirs.forEach((d) => roots.add(d))
      calls++
    }
  }
  return { st, seen, roots, calls }
}
