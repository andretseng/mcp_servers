// Research-rules gate for OpenCode 2 (Ornith). Enforces in the harness what AGENTS.md only asks:
//   R1  before touching project/SDK sources: read karpathy-guidelines AND search the vault
//   R2  peripheral gate: handling a peripheral (COMP, TIM, ADC...) in project calls or in a note about
//       project code requires that peripheral's TRM module note AND its SDK driverlib header read first
//   R8  the `execute` tool is not available.
//   R9  a shell command may not write or copy into the vault, the project or the SDK (use the write/edit tools; read files where they are).
//   (Removed 2026-10-21: G0-G6 adviser-request gates, R3-R7 findings/contract rules. Backup in ~/.agents/.bak.)
//   Gated-session mode (env GATE_FILE, written by ~/.agents/skills/work-run): the session may read only the roots minus the
//   excluded sources, write only its draft, and the harness checks the draft after the session. See cardDecide.
// State is per sessionID, in memory. OPENCODE_GATE=off disables it. Decisions go to
// ~/.local/state/opencode-gate/gate.jsonl. Only Edit THIS file via the install path.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { spawnSync } from "node:child_process"

const VAULT = "/home/andre/llm-wiki"
const RE_PROJECT = /\/home\/andre\/workspace_ccstheia(\/|["\s]|$)/
const RE_SDK = /\/home\/andre\/ti(\/|["\s]|$)/
const RE_NOTE_PATH = /\/home\/andre\/llm-wiki\/(Project筆記|專業領域)\/[^"]*\.md/
const RE_SDK_FILE = /\/home\/andre\/ti\/[^\s"'|;&<>\\]+\.(h|c|inc|syscfg|js|json|md|txt|pdf|xml)\b/g
const RE_TRM_NOTE_ONE = /\/home\/andre\/llm-wiki\/專業領域\//
const RE_TRM_NOTE = /\/home\/andre\/llm-wiki\/專業領域\/[^"]*\.md/g
// Peripheral gate: when the agent handles a peripheral it must have read that peripheral's TRM
// module note AND its SDK driverlib header. No counting: each engaged peripheral needs both.
// kw: how the peripheral shows up in tool inputs / note text; trm: TRM note basename prefix;
// sdk: driverlib header basename prefix.
const PERIPHS = {
  comp: { kw: /\bDL_COMP|\bCOMP[0-9_]|\bCOMP\b|comparator/i, trm: "COMP", sdk: "dl_comp" },
  timer: { kw: /\bDL_TIMER|\bTIM[ABG][0-9]|\bIFTIM|\bTIMER|\bTIMx\b/i, trm: "TIM", sdk: "dl_timer" },
  adc: { kw: /\bDL_ADC12|\bADC[0-9]*\b/i, trm: "ADC", sdk: "dl_adc12" },
  dac: { kw: /\bDL_DAC12|\bDAC[0-9]*\b/i, trm: "DAC", sdk: "dl_dac12" },
  opa: { kw: /\bDL_OPA|\bOPA[0-9]*\b/i, trm: "OPA", sdk: "dl_opa" },
  dma: { kw: /\bDL_DMA|\bDMA\b/i, trm: "DMA", sdk: "dl_dma" },
  uart: { kw: /\bDL_UART|\bUART[0-9]*\b/i, trm: "UART", sdk: "dl_uart" },
  spi: { kw: /\bDL_SPI|\bSPI[0-9]*\b/i, trm: "SPI", sdk: "dl_spi" },
  i2c: { kw: /\bDL_I2C|\bI2C[0-9]*\b/i, trm: "I2C", sdk: "dl_i2c" },
  gpio: { kw: /\bDL_GPIO|\bGPIO[A-C]?\b/i, trm: "GPIO", sdk: "dl_gpio" },
}
function engaged(s) {
  return Object.keys(PERIPHS).filter((k) => PERIPHS[k].kw.test(s))
}
function periphMissing(st, keys) {
  const miss = []
  for (const k of keys) {
    const P = PERIPHS[k]
    const sdkOk = st.sdkFiles.some((f) => new RegExp("/" + P.sdk + "[a-z0-9]*\\.h$").test(f))
    const trmOk = st.trmNotes.some((f) => new RegExp("/專業領域/[^/]+/" + P.trm + "[^/]*\\.md$").test(f))
    if (!trmOk) miss.push(k.toUpperCase() + ": read the TRM note 專業領域/**/" + P.trm + "*模組分析*.md with the read tool (find it with glob/grep under /home/andre/llm-wiki/專業領域)")
    if (!sdkOk) miss.push(k.toUpperCase() + ": read the SDK header " + P.sdk + "*.h under /home/andre/ti (use the DL_* API names from the TRM note; find it with glob)")
  }
  return miss
}
// R9 helper: shell writes into the vault, the project or the SDK. Returns what was found, or null. A text check cannot see an interpreter's own file writes (python -c ...);
// it covers the shell's: output redirection, tee, cp/mv/ln/rsync/install (the destination; for mv also the source, which it removes), dd of=, sed -i / perl -i.
// Relative paths count from the working directory at that command (`base`, moved by each `cd`). A destination built from a shell variable or a substitution cannot be resolved
// and is refused too: write the literal path. Copying FROM a protected path to somewhere else is reading, and is allowed.
const PROTECTED_ROOT = (p) => p === VAULT || p.startsWith(VAULT + "/") || RE_PROJECT.test(p + "/") || RE_SDK.test(p + "/")
const SHELL_WRITERS = new Set(["cp", "mv", "ln", "rsync", "install", "tee", "dd"])
const shellWords = (seg) => (seg.match(/"[^"]*"|'[^']*'|\S+/g) || []).map((x) => x.replace(/^["']|["']$/g, ""))
// Remove heredoc bodies (their text is data, and may contain > or cp); the line that starts the heredoc stays.
function stripHeredocs(c) {
  let out = String(c), pos = 0
  for (let guard = 0; guard < 20; guard++) {
    const m = /<<-?\s*(?:"([^"]+)"|'([^']+)'|(\w+))/.exec(out.slice(pos))
    if (!m) break
    const at = pos + m.index
    if (out[at + 2] === "<") { pos = at + 3; continue } // <<< is a here-string, not a heredoc
    const delim = m[1] || m[2] || m[3]
    const lineEnd = out.indexOf("\n", at)
    if (lineEnd < 0) break
    const rest = out.slice(lineEnd + 1)
    const d = new RegExp("(^|\\n)[ \\t]*" + delim.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "[ \\t]*(\\n|$)").exec(rest)
    if (!d) { out = out.slice(0, lineEnd); break } // not terminated: the rest is the body
    out = out.slice(0, lineEnd + 1) + rest.slice(d.index + d[0].length)
    pos = lineEnd + 1
  }
  return out
}
// Targets of output redirections (`>`, `>>`, `2>`) outside quotes; `>&` and `>(` are skipped.
function redirectTargets(c) {
  const out = []
  let q = null
  for (let i = 0; i < c.length; i++) {
    const ch = c[i]
    if (q) { if (ch === "\\" && q === '"') { i++; continue } if (ch === q) q = null; continue }
    if (ch === "'" || ch === '"') { q = ch; continue }
    if (ch === "\\") { i++; continue }
    if (ch === ">") {
      let j = i + 1
      if (c[j] === ">") j++
      if (c[j] === "&" || c[j] === "(") { i = j; continue }
      while (c[j] === " ") j++
      let t = ""
      if (c[j] === '"' || c[j] === "'") { const qq = c[j++]; while (j < c.length && c[j] !== qq) t += c[j++] }
      else while (j < c.length && !/[\s;&|)<>]/.test(c[j])) t += c[j++]
      if (t) out.push(t)
      i = j - 1
    }
  }
  return out
}
export function protectedShellWrite(cmd, base = process.cwd()) {
  const c = stripHeredocs(String(cmd || ""))
  let cwd = base // null once a `cd` target cannot be resolved
  const expand = (t) => String(t).replace(/^~(?=\/|$)/, os.homedir()).replace(/\$\{?HOME\}?/g, os.homedir())
  const dest = (t, what) => {
    const x = expand(t)
    if (/[$`]/.test(x) || (cwd === null && !x.startsWith("/"))) return what + " to a path that cannot be resolved (" + t + "): write the literal path"
    return PROTECTED_ROOT(path.resolve(cwd, x)) ? what + " into " + t : null
  }
  for (const seg of scanShell(c).segs) {
    const words = shellWords(seg)
    if (words[0] === "cd" && words[1]) { const x = expand(words[1]); cwd = /[$`]/.test(x) || cwd === null && !x.startsWith("/") ? null : path.resolve(cwd, x); continue }
    for (const t of redirectTargets(seg)) {
      if (t === "/dev/null" || !/^[~./\w$`-]/.test(t)) continue
      const r = dest(t, "a redirection"); if (r) return r
    }
    const w = words.filter((x) => !/^\w+=/.test(x))
    const name = (w[0] || "").split("/").pop()
    const args = w.slice(1).filter((x) => !x.startsWith("-"))
    if (SHELL_WRITERS.has(name)) {
      let targets = []
      if (name === "tee") targets = args
      else if (name === "dd") targets = args.filter((x) => x.startsWith("of=")).map((x) => x.slice(3))
      else {
        const ti = w.findIndex((x) => x === "-t" || x === "--target-directory")
        const eq = w.find((x) => x.startsWith("--target-directory="))
        if (eq) targets = [eq.slice(19)]
        else if (ti >= 0 && w[ti + 1]) targets = [w[ti + 1]]
        else if (args.length >= 2) targets = [args[args.length - 1]]
        if (name === "mv") targets = targets.concat(args.slice(0, -1)) // a mv removes its sources
      }
      for (const t of targets) { const r = dest(t, name); if (r) return r }
    }
    if ((name === "sed" || name === "perl") && w.slice(1).some((x) => /^-[a-zA-Z]*i/.test(x))) {
      for (const t of args.slice(1)) { const r = dest(t, name + " -i"); if (r) return r } // args[0] is the script
    }
  }
  return null
}
const SHELL = new Set(["shell", "bash"])
const WRITERS = new Set(["write", "edit", "patch", "apply_patch", "multiedit"])

export function newState() {
  return { karpathy: false, vault: false, project: false, sdk: false, sdkFiles: [], trmNotes: [], vaultNotes: [], projectFiles: [], blocks: 0, sid: "", t0: "", notePath: "" }
}

// Pure decision: returns {block: string|null, next: state}. `s` is the serialized tool input.
export function decide(state, tool, s) {
  const st = { ...state, sdkFiles: [...(state.sdkFiles || [])], trmNotes: [...(state.trmNotes || [])], vaultNotes: [...(state.vaultNotes || [])], projectFiles: [...(state.projectFiles || [])] }
  const isShell = SHELL.has(tool)
  const touchesProject = RE_PROJECT.test(s)
  const touchesSdk = RE_SDK.test(s)
  const isNoteWrite = WRITERS.has(tool) && RE_NOTE_PATH.test(s)
  if (tool === "execute") {
    return { block: "GATE R8: the execute tool is not available in this setup, because the harness cannot see what it runs. Use the shell, read, grep and write tools.", next: st }
  }
  if (isShell) {
    let cmd = ""
    try { cmd = JSON.parse(s).command || "" } catch {}
    const w = protectedShellWrite(cmd)
    if (w) return { block: "GATE R9: blocked. A shell command may not write or copy into the vault, the project or the SDK (found: " + w + "). Read files where they are; do not copy them into the vault to read them. To create or change a note or a source file use the write or edit tool.", next: st }
  }
  if (touchesProject || touchesSdk) {
    const missing = []
    if (!st.karpathy) missing.push("karpathy-guidelines (load it with the skill tool or read ~/.agents/skills/karpathy-guidelines/SKILL.md)")
    if (!st.vault) missing.push("vault search (a grep/glob/shell grep whose path or command contains /home/andre/llm-wiki; ls or reading one file does not count)")
    if (missing.length) {
      return { block: "GATE R1: blocked. Done so far: karpathy=" + st.karpathy + ", vault_search=" + st.vault + ". Still missing: " + missing.join("; ") + ". Do ONLY the missing step(s) now, each as its own tool call, then retry this call once. Do not retry before they are done.", next: st }
    }
  }
  // R2: peripheral gate. Engaged = named in a project-touching call, or in a vault note being written
  // about project code. Order is vault (project + TRM notes) -> project -> SDK.
  if ((touchesProject && !touchesSdk && !isNoteWrite) || (isNoteWrite && st.project)) {
    const miss = periphMissing(st, engaged(s))
    if (miss.length) {
      return { block: "GATE R2: blocked. This call handles a peripheral whose reference you have not read yet. Missing: " + miss.join(" | ") + ". Read those first, each as its own read call, then retry this call once. Do not describe registers, defaults or API behaviour from memory; label anything unread [INFERENCE].", next: st }
    }
  }

  // allowed: record what this call establishes
  if ((tool === "read" || tool === "skill") && /karpathy-guidelines/.test(s)) st.karpathy = true
  if (touchesProject) st.project = true
  if (tool === "read") {
    const pth = (s.match(/"(?:path|filePath)":"([^"]+)"/) || [])[1]
    if (pth && RE_PROJECT.test(pth) && !st.projectFiles.includes(pth)) st.projectFiles.push(pth)
    if (pth && pth.startsWith(VAULT + "/") && pth.endsWith(".md") && !RE_TRM_NOTE_ONE.test(pth) && !st.vaultNotes.includes(pth)) st.vaultNotes.push(pth)
  }
  if (tool === "read") for (const m of s.matchAll(RE_TRM_NOTE)) if (!st.trmNotes.includes(m[0])) st.trmNotes.push(m[0])
  if (touchesSdk) {
    st.sdk = true
    if (tool === "read" || isShell || tool === "grep") {
      for (const m of s.matchAll(RE_SDK_FILE)) if (!st.sdkFiles.includes(m[0])) st.sdkFiles.push(m[0])
    }
  }
  const vaultSearch =
    (isShell && s.includes(VAULT) && /\b(grep|rg|ugrep|find)\b/.test(s)) ||
    ((tool === "grep" || tool === "glob") && s.includes(VAULT))
  if (vaultSearch) st.vault = true
  return { block: null, next: st }
}

// Progress ledger injected into every model call so that context compaction cannot make the agent
// forget what it already read and start over (or skip the research steps again).
export function ledgerText(st) {
  const short = (f) => f.replace("/home/andre/workspace_ccstheia/", "proj:").replace("/home/andre/llm-wiki/", "vault:").replace("/home/andre/ti/", "sdk:")
  const list = (a) => (a.length ? a.map(short).join("; ") : "none")
  return [
    "HARNESS PROGRESS LEDGER (kept by the harness; it survives context compaction. Do not redo what is listed here; continue from it.)",
    "- karpathy-guidelines loaded: " + st.karpathy,
    "- vault searched: " + st.vault,
    "- vault notes read: " + list(st.vaultNotes),
    "- TRM module notes read: " + list(st.trmNotes),
    "- SDK files read: " + list(st.sdkFiles),
    "- project files read: " + list(st.projectFiles),
    "- gate blocks so far: " + (st.blocks || 0),
    "Rules still apply after compaction: handling a peripheral needs its TRM note and SDK header read first.",
  ].join("\n")
}


// ---- gated sessions (plan, work): read only the allowed roots, write one checked file ----
const CARD_TOOLS = new Set(["read", "grep", "glob", "list", "shell", "bash", "write", "todowrite", "todoread"])
const CARD_SHELL_OK = new Set(["ls", "cat", "head", "tail", "grep", "rg", "sed", "awk", "nl", "wc", "find", "sort", "uniq", "cut", "echo", "tr", "pwd", "true", "stat", "file", "diff", "basename", "dirname", "realpath"])
// Split a shell command into its commands the way the shell does: a | ; && || & inside quotes does not split. Also reports what a
// card must refuse anywhere: redirections outside quotes, command substitution (backticks, $( ) outside single quotes).
export function scanShell(cmd) {
  const segs = []
  let cur = "", q = null, bad = null
  for (let i = 0; i < cmd.length; i++) {
    const c = cmd[i], n = cmd[i + 1]
    if (q) {
      if (c === "\\" && q === '"' && n) { cur += c + n; i++; continue }
      if (c === q) { q = null; cur += c; continue }
      if (q === '"' && (c === "`" || (c === "$" && n === "("))) bad = bad || "command substitution"
      cur += c
      continue
    }
    if (c === "'" || c === '"') { q = c; cur += c; continue }
    if (c === "\\" && n) { cur += c + n; i++; continue }
    if (c === "`" || (c === "$" && n === "(")) bad = bad || "command substitution"
    if (c === "<" || c === ">") bad = bad || "redirection"
    if (c === ";" || c === "\n" || c === "|" || c === "&") {
      if (c === "&" && n !== "&") bad = bad || "background job"
      segs.push(cur); cur = ""
      if ((c === "|" && n === "|") || (c === "&" && n === "&")) i++
      continue
    }
    cur += c
  }
  segs.push(cur)
  return { segs, bad, open: q }
}
// Command substitutions outside single quotes: returns the command with each one replaced by the word `x`, and the commands that were inside.
export function splitSubstitutions(cmd) {
  let outer = "", q = null
  const inners = []
  for (let i = 0; i < cmd.length; i++) {
    const c = cmd[i]
    if (q === "'") { if (c === "'") q = null; outer += c; continue }
    if (c === "\\" && cmd[i + 1]) { outer += c + cmd[i + 1]; i++; continue }
    if (c === "'" && q === null) { q = "'"; outer += c; continue }
    if (c === '"') { q = q === '"' ? null : '"'; outer += c; continue }
    if (c === "$" && cmd[i + 1] === "(" && cmd[i + 2] !== "(") {
      let d = 1, j = i + 2
      for (; j < cmd.length && d > 0; j++) { if (cmd[j] === "(") d++; else if (cmd[j] === ")") d-- }
      if (d === 0) { inners.push(cmd.slice(i + 2, j - 1)); outer += "x"; i = j - 1; continue }
    }
    if (c === "`") {
      const j = cmd.indexOf("`", i + 1)
      if (j > i) { inners.push(cmd.slice(i + 1, j)); outer += "x"; i = j; continue }
    }
    outer += c
  }
  return { outer, inners }
}
// The read-only commands can still write or run something: sed `w`/`e`, awk `print >`/system(), find -fprint, sort -o, uniq IN OUT, rg --pre.
// Redirection is refused outside quotes only, and the roots are writable as far as the path check goes, so these are refused by option.
export function unsafeUse(w, seg) {
  const tk = seg.trim().split(/\s+/)
  const quoted = [...seg.matchAll(/'([^']*)'|"([^"]*)"/g)].map((m) => m[1] ?? m[2]).join("\n")
  if (w === "find" && /\s-(exec|execdir|delete|ok|okdir|fprint0?|fprintf|fls)\b/.test(seg)) return "find " + (seg.match(/\s(-\w+)/g) || []).find((x) => /exec|delete|ok|fprint|fls/.test(x))?.trim()
  if (w === "sed") {
    if (/\s(-[a-zA-Z]*i|--in-place|-f|--file|-[a-zA-Z]*f)(\s|=|$)/.test(seg)) return "sed -i / -f"
    if (/(^|[\s;{}0-9$,\/!])[wWe](\s|$)/.test(quoted) || /\/[gIimM0-9p]*[we][gIimM0-9pe]*\s*($|;|})/.test(quoted)) return "a sed w, W or e command or flag"
  }
  if (w === "awk") {
    if (/\s(-f|--file)(\s|=|$)/.test(seg)) return "awk -f"
    if (/system\s*\(|getline|\b(print|printf)\b[^;}]*[>|]/.test(quoted)) return "an awk program with system(), getline, or print/printf followed by > or |"
  }
  if (w === "sort" && /\s(-[a-zA-Z]*o|--output)(\s|=|$)/.test(seg)) return "sort -o"
  if (w === "uniq" && tk.slice(1).filter((x, i, a) => !x.startsWith("-") && !/^\d+$/.test(x) && !(i > 0 && /^-[fsw]$/.test(a[i - 1]))).length > 1) return "uniq with an output file"
  if ((w === "rg") && /\s--(pre|hostname-bin)(\s|=|$)/.test(seg)) return "rg --pre"
  if ((w === "tail") && /\s(-[a-zA-Z]*[fF]|--follow)(\s|=|$)/.test(seg)) return "tail -f"
  return null
}
const TOP_DIRS = (() => { try { return new Set(fs.readdirSync("/")) } catch { return new Set(["home", "tmp", "etc", "usr", "var", "opt", "mnt", "dev", "proc"]) } })()
const CARD_READERS = new Set(["cat", "head", "tail", "sed", "awk", "nl"])
export function loadCard() {
  const f = process.env.GATE_FILE || process.env.TASKCARD_FILE
  if (!f) return null
  try { return JSON.parse(fs.readFileSync(f, "utf8")) } catch { return null }
}
const cardAllowed = (card) => [...(card.seeds || []), ...(card.extra_reads || []), card.out].filter(Boolean).map((x) => path.resolve(x))
function pathOk(card, p) {
  const q = path.resolve(String(p).replace(/^~/, os.homedir()))
  if (q === "/dev/null") return true
  return cardAllowed(card).some((a) => q === a || q.startsWith(a.replace(/\/$/, "") + "/"))
}
// The vault notes of a module (a peripheral): 專業領域/<chip>/<prefix>…模組分析….md, every chip folder that has one.
export function trmNotesFor(prefix, vault = VAULT) {
  const out = []
  try {
    const base = path.join(vault, "專業領域")
    for (const d of fs.readdirSync(base)) {
      const dir = path.join(base, d)
      if (!fs.statSync(dir).isDirectory()) continue
      for (const n of fs.readdirSync(dir)) if (n.startsWith(prefix) && n.includes("模組分析") && n.endsWith(".md")) out.push(path.join(dir, n))
    }
  } catch {}
  return out.slice(0, 4)
}
export function cardDecide(card, st, tool, s) {
  // The gate blocks only on order (guidelines first, vault before project/SDK), scope (roots minus excluded sources, one draft file). Everything else is a reminder in the ledger and a line of advice in the log.
  const next = { ...st, cardReads: [...(st.cardReads || [])], mods: { ...(st.mods || {}) }, modRead: [...(st.modRead || [])], advice: [] }
  const blk = (m) => ({ block: "GATE: " + m, next })
  let j = {}; try { j = JSON.parse(s) } catch {}
  if (!CARD_TOOLS.has(tool) && !(tool === "edit" && card.mode === "work")) return blk("the " + tool + " tool is not available in this session. Use read, grep, glob, shell (read-only commands) and write.")
  for (const d of card.deny || []) if (s.includes(d)) return blk("the path matches the excluded source '" + d + "'. Do not use it.")
  const firstMiss = (card.first_reads || []).filter((r) => !next.cardReads.includes(path.resolve(r)))
  if (firstMiss.length) {
    const rp = tool === "read" ? j.path || j.filePath : null
    if (!(rp && firstMiss.some((r) => path.resolve(rp) === path.resolve(r)))) {
      return blk("first read the working guidelines with the read tool: " + firstMiss.join(", ") + ". Every other call is blocked until you have.")
    }
  }
  const callPaths = []
  const outside = (p) => blk(p + " is outside the allowed sources. Allowed: " + [...(card.seeds || []), ...(card.extra_reads || [])].join(", ") + ". Work only inside them.")
  if (tool === "read" || tool === "list") {
    const p = j.path || j.filePath
    if (!p) return blk("give the absolute path.")
    if (!pathOk(card, p)) return outside(p)
    callPaths.push(p)
    if (tool === "read" && !next.cardReads.includes(path.resolve(p))) next.cardReads.push(path.resolve(p))
  } else if (tool === "grep" || tool === "glob") {
    if (!j.path) return blk("give an absolute directory or file in the path argument of " + tool + ".")
    if (!pathOk(card, j.path)) return outside(j.path)
    callPaths.push(j.path)
  } else if (tool === "shell" || tool === "bash") {
    let cmd = j.command || ""
    // $( ) and `...` are allowed when the command inside passes the same checks; the outer command is then judged with a placeholder in their place
    const sub = splitSubstitutions(cmd)
    for (const inner of sub.inners) {
      const r = cardDecide(card, next, "shell", JSON.stringify({ command: inner }))
      if (r.block) return r
    }
    cmd = sub.outer
    const stripped = cmd.replace(/\d?>\s*\/dev\/null/g, "").replace(/2>&1/g, "")
    const scan = scanShell(stripped)
    if (scan.bad || scan.open || /\.\.\//.test(stripped)) return blk((scan.bad || (scan.open ? "an unclosed quote" : ".. paths")) + " is not allowed in this session. Use plain read-only commands (a | or && between commands is fine).")
    for (const seg of scan.segs) {
      const w = seg.trim().split(/\s+/)[0]
      if (!w) continue
      if (w === "cd") {
        const target = (seg.trim().split(/\s+/)[1] || "").replace(/^["']|["']$/g, "")
        if (!/^(~|\/)/.test(target)) return blk("cd needs an absolute path inside the allowed roots; or use absolute paths in the next command.")
        if (!pathOk(card, target)) return outside(target)
        callPaths.push(target)
        continue
      }
      if (w === "python3" && card.mode === "work" && card.script) {
        const tk = seg.trim().split(/\s+/)
        const fi = tk.indexOf("--file")
        if (tk[1] === card.script && tk[2] === "check-note" && tk.includes("--work") && fi > 0 && tk[fi + 1] && path.resolve(tk[fi + 1]) === path.resolve(card.out)) continue
        return blk("python3 is allowed only for the check command: python3 " + card.script + " check-note --work " + card.work_file + " --file " + card.out)
      }
      if (!CARD_SHELL_OK.has(w)) return blk("the command `" + w + "` is not allowed in this session. Allowed: " + [...CARD_SHELL_OK].join(", ") + ".")
      const unsafe = unsafeUse(w, seg)
      if (unsafe) return blk(unsafe + " is not allowed in this session: it can write a file or run a program. Use it only to read.")
      for (const m of seg.matchAll(/(?:^|[\s"'=:])((?:~|\/)[^\s"'|;&<>()]*)/g)) {
        // a pattern like '/^int scale/' or '/foo/p' is not a path: the first component must be a directory that exists at /
        if (!m[1].startsWith("~") && !TOP_DIRS.has(m[1].split("/")[1] || "")) continue
        if (!pathOk(card, m[1])) return outside(m[1])
        callPaths.push(m[1])
        if (CARD_READERS.has(w) && !next.cardReads.includes(path.resolve(m[1].replace(/^~/, os.homedir())))) next.cardReads.push(path.resolve(m[1].replace(/^~/, os.homedir())))
      }
    }
  } else if (tool === "write" || (tool === "edit" && card.mode === "work")) {
    // one draft file; nothing is checked while it is written (the harness checks and publishes it when the session stops)
    const p = j.path || j.filePath
    if (!p || path.resolve(p) !== path.resolve(card.out)) return blk((tool === "edit" ? "edit" : "write") + " only to " + card.out + " (the draft note).")
  }
  if (card.vault_first) {
    const inVault = (x) => path.resolve(x).startsWith(VAULT + "/") || path.resolve(x) === VAULT
    const inSrc = (x) => RE_PROJECT.test(path.resolve(x) + "/") || RE_SDK.test(path.resolve(x) + "/")
    if (callPaths.some(inVault)) next.vaultSeen = true
    else if (!next.vaultSeen && callPaths.some(inSrc)) return blk("search order is vault first: look in the vault (" + VAULT + ") before project or SDK files. Do a vault read, grep or glob now, then retry.")
  }
  // modules: the first call that names one finds its vault note; the ledger reminds the session until the note has been read or searched
  for (const k of engaged(s)) if (!(k in next.mods)) next.mods[k] = trmNotesFor(PERIPHS[k].trm)
  for (const notes of Object.values(next.mods)) for (const n of notes) if (s.includes(n) && !next.modRead.includes(n)) next.modRead.push(n)
  for (const [k, notes] of Object.entries(next.mods)) if (!(st.mods || {})[k] && notes.length && !notes.some((n) => next.modRead.includes(n))) next.advice.push("module-note:" + k)
  return { block: null, next }
}
export function cardLedger(card, st) {
  const pending = []
  for (const notes of Object.values(st.mods || {})) for (const n of notes) if (!(st.modRead || []).includes(n)) pending.push(n)
  return [
    "HARNESS PROGRESS LEDGER (gated session " + card.id + ", kept by the harness)",
    ...(card.question ? ["Request (word for word): " + card.question] : []),
    "Research path: the vault first, then the project, then the SDK Read the vault note of a module when you handle the module.",
    ...(pending.length ? ["Module notes your calls touched that you have not read yet (read or grep -n the part you need): " + [...new Set(pending)].slice(0, 6).join("; ")] : []),
    "Draft note (write and edit it as often as you like; nothing is checked while you write): " + card.out + ". When it is complete run: python3 " + card.script + " check-note --work " + card.work_file + " --file " + card.out,
    "Files read so far: " + ((st.cardReads || []).map((x) => x.split("/").pop()).join(", ") || "none"),
    "Allowed sources: " + [...(card.seeds || []), ...(card.extra_reads || [])].join(", "),
    "STATUS: the job ends when you stop; the harness then checks and publishes the draft.",
  ].join("\n")
}

const CKDIR = path.join(os.homedir(), ".local", "state", "agent-checkpoints")
function loadState(sid) {
  try { return { ...newState(), ...JSON.parse(fs.readFileSync(path.join(CKDIR, "auto-" + sid + ".json"), "utf8")) } } catch { return newState() }
}
function saveState(sid, st) {
  try {
    fs.mkdirSync(CKDIR, { recursive: true })
    fs.writeFileSync(path.join(CKDIR, "auto-" + sid + ".json"), JSON.stringify(st))
    fs.writeFileSync(path.join(CKDIR, "auto-" + sid + ".md"), "# Harness ledger (auto, " + new Date().toISOString() + ")\n\n" + ledgerText(st) + "\n")
  } catch {}
}
// Audit log input: writes keep the target path first (their content would push the path out of the cut).
function logInput(tool, input, s) {
  if (WRITERS.has(tool) && input && typeof input === "object") {
    const body = [input.content, input.newString, input.new_string].filter((x) => typeof x === "string").join("")
    return JSON.stringify({ path: input.path || input.filePath, bytes: body.length })
  }
  return s.slice(0, 400)
}
const LOGDIR = path.join(os.homedir(), ".local", "state", "opencode-gate")
function audit(rec) {
  try {
    fs.mkdirSync(LOGDIR, { recursive: true })
    fs.appendFileSync(path.join(LOGDIR, "gate.jsonl"), JSON.stringify({ ts: new Date().toISOString(), ...rec }) + "\n")
  } catch {}
}

export default {
  id: "research-gate",
  setup: async (ctx) => {
    if ((process.env.OPENCODE_GATE || "on").toLowerCase() === "off") return
    const card = loadCard()
    const sessions = new Map()
    const get = (sid) => { if (!sessions.has(sid)) sessions.set(sid, loadState(sid)); return sessions.get(sid) }
    await ctx.tool.hook("execute.before", async (a) => {
      const sid = a.sessionID || "?"
      const s = JSON.stringify(a.input ?? {})
      const cur = get(sid); cur.sid = sid; if (!cur.t0) cur.t0 = new Date().toISOString()
      const { block, next } = card ? cardDecide(card, cur, a.tool, s) : decide(cur, a.tool, s)
      audit({ session: sid, card: card ? card.id : undefined, advice: next && next.advice && next.advice.length ? next.advice : undefined, tool: a.tool, blocked: !!block, rule: block ? (card ? "CARD" : block.slice(5, 7)) : null, reason: block ? String(block).slice(0, 300) : undefined, input: logInput(a.tool, a.input, s) })
      if (block) { const n = { ...cur, blocks: (cur.blocks || 0) + 1 }; sessions.set(sid, n); saveState(sid, n); throw new Error(block) }
      sessions.set(sid, next)
      saveState(sid, next)
    })
    await ctx.session.hook("context", async (a) => {
      try {
        try {
          const f = path.join(LOGDIR, "context-shape.json")
          if (!fs.existsSync(f)) fs.writeFileSync(f, JSON.stringify({ keys: Object.keys(a), n: (a.messages || []).length, sample: (a.messages || []).slice(0, 2), last: (a.messages || []).slice(-1) }, null, 1).slice(0, 6000))
        } catch {}
        const st = a.sessionID ? (get(a.sessionID).sid = a.sessionID, get(a.sessionID)) : null
        if (!st || !Array.isArray(a.system)) return
        a.system = a.system.filter((x) => !(x && typeof x.text === "string" && x.text.startsWith("HARNESS PROGRESS LEDGER")))
        a.system.push({ type: "text", text: card ? cardLedger(card, st) : ledgerText(st) })
      } catch {}
    })
  },
}
