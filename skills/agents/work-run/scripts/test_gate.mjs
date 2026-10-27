// Tests for the gated-session modes (work, plan) and the shared rules of the opencode gate. Run: node test_gate.mjs
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
const T = fs.mkdtempSync(path.join(os.tmpdir(), "cardgate-"))
process.env.HOME = T
fs.mkdirSync(T + "/.local/state/agent-checkpoints", { recursive: true })
const m = await import(os.homedir().replace(T, "/home/andre") + "/.config/opencode/plugins/research-gate.js")
const src = T + "/src"; fs.mkdirSync(src)
fs.writeFileSync(src + "/cap.c", "int a;\nvoid isr(void) {\n  ival = t >> 1;\n  ertm = sum >> 1;\n}\n")
fs.writeFileSync(src + "/ref.h", "#define REF 1\n")
fs.writeFileSync(T + "/other.c", "secret\n")
const wd = T + "/wd"; fs.mkdirSync(wd + "/facts", { recursive: true })
fs.writeFileSync(wd + "/scope.md", `# Job
## Question
Analyze how the captured period becomes the output value in the demo project.
## Start
capture interrupt
## End
output value
## Out of scope
- telemetry
## Deliverable
note: ${T}/note.md
max_chars: 6000
## Cards
### C1 — capture
task: Find how the capture interrupt computes the period.
seeds:
  - ${src}
required_reads:
  - ${src}/ref.h
in: unit=none; form=e; cond=c
out: unit=ticks; form=i; cond=c
max_claims: 3
`)
const card = { mode: "facts", id: "C1", task: "t", scope_file: wd + "/scope.md", out: wd + "/facts/C1.facts.md", seeds: [src], required_reads: [src + "/ref.h"], extra_reads: [wd + "/scope.md"], max_claims: 3, script: "/home/andre/.agents/skills/work-run/scripts/workrun.py", deny: ["old-answer"] }
let st = {}
let fails = 0
const run = (tool, input) => { const r = m.cardDecide(card, st, tool, JSON.stringify(input)); if (!r.block) st = r.next; return r.block }
const expect = (label, got, want) => { const ok = want === null ? got === null : (got || "").includes(want); if (!ok) fails++; console.log((ok ? "ok   " : "FAIL ") + label + (ok ? "" : "  got: " + String(got).slice(0, 160))) }
expect("read seed file", run("read", { path: src + "/cap.c" }), null)
expect("read outside seeds blocked", run("read", { path: T + "/other.c" }), "outside the allowed")
expect("read via .. is outside", run("read", { path: src + "/../other.c" }), "outside the allowed")
expect("grep without path blocked", run("grep", { pattern: "x" }), "give an absolute")
expect("grep in seeds ok", run("grep", { pattern: "ertm", path: src }), null)
expect("grep outside blocked", run("grep", { pattern: "x", path: T }), "outside the allowed")
expect("denied source blocked", run("read", { path: src + "/old-answer.c" }), "excluded source")
expect("shell grep in seeds ok", run("shell", { command: "grep -n ertm " + src + "/cap.c | head -5" }), null)
expect("shell cat outside blocked", run("shell", { command: "cat " + T + "/other.c" }), "outside the allowed")
expect("shell redirect blocked", run("shell", { command: "echo x > " + src + "/cap.c" }), "redirection")
expect("shell rm blocked", run("shell", { command: "rm " + src + "/cap.c" }), "not allowed")
expect("shell git blocked", run("shell", { command: "git log" }), "not allowed")
expect("shell sed -i blocked", run("shell", { command: "sed -i s/a/b/ " + src + "/cap.c" }), "sed -i")
const F = src + "/cap.c"
for (const [label, cmd] of [
  ["awk print > file", `awk '{print > "${src}/new.txt"}' ${F}`],
  ["awk system()", `awk 'BEGIN{system("touch ${src}/x")}'`],
  ["awk print | cmd", `awk '{print $1 | "sort"}' ${F}`],
  ["awk getline", `awk 'BEGIN{while ((getline l < "${F}") > 0) n++}'`],
  ["awk -f", `awk -f ${src}/p.awk ${F}`],
  ["sed w file", `sed -n 'w ${src}/new.txt' ${F}`],
  ["sed e command", `sed '1e touch ${src}/x' ${F}`],
  ["sed s///w", `sed 's/a/b/w ${src}/new.txt' ${F}`],
  ["sed s///e", `sed 's/a/b/e' ${F}`],
  ["sed --in-place", `sed --in-place s/a/b/ ${F}`],
  ["sed -f", `sed -f ${src}/p.sed ${F}`],
  ["find -fprint", `find ${src} -name '*.c' -fprint ${src}/list.txt`],
  ["sort -o", `sort -o ${F} ${F}`],
  ["sort --output", `sort --output=${F} ${F}`],
  ["uniq in out", `uniq ${F} ${src}/out.txt`],
  ["rg --pre", `rg --pre ${src}/x foo ${src}`],
  ["tail -f", `tail -f ${F}`],
]) expect("shell " + label + " blocked", run("shell", { command: cmd }), "is not allowed in this session")
for (const [label, cmd] of [
  ["awk filter and count", `awk '/^## Provenance/{exit} /\\[S[0-9]+\\]/ {n++} END{print n}' ${F}`],
  ["awk -F with a pipe", `awk -F'|' '{print $2}' ${F}`],
  ["awk comparison", `awk 'NR>5 && NR<9 {print $1}' ${F}`],
  ["sed range print", `sed -n '10,20p' ${F}`],
  ["sed pattern print", `sed -n '/error/p' ${F}`],
  ["sed substitute", `sed 's/a/b/g' ${F}`],
  ["sort | uniq -c", `sort ${F} | uniq -c`],
  ["uniq -f 1 one file", `uniq -f 1 ${F}`],
  ["sort -k reverse", `sort -rn -k2 ${F}`],
  ["rg", `rg -n ertm ${src}`],
  ["find by name", `find ${src} -name '*.c'`],
  ["tail -n", `tail -n 20 ${F}`],
]) expect("shell " + label + " allowed", run("shell", { command: cmd }), null)
expect("shell /dev/null redirect allowed", run("shell", { command: "grep -n ertm " + src + "/cap.c 2>/dev/null" }), null)
expect("edit tool unavailable", run("edit", { path: card.out }), "not available")
expect("execute tool unavailable", run("execute", { code: "1" }), "not available")
expect("write elsewhere blocked", run("write", { path: T + "/x.md", content: "x" }), "write only to")
// wiring: the plugin's own hook must call cardDecide with the right argument order
{
  const cf = T + "/wiring.card.json"
  fs.writeFileSync(cf, JSON.stringify(card))
  process.env.GATE_FILE = cf
  const hooks = {}
  await m.default.setup({ tool: { hook: async (n, f) => { hooks[n] = f } }, session: { hook: async (n, f) => { hooks[n] = f } } })
  let err = null
  try { await hooks["execute.before"]({ sessionID: "wire1", tool: "read", input: { path: src + "/cap.c" } }) } catch (e) { err = e.message }
  expect("wiring: allowed read passes through the real hook", err, null)
  err = null
  try { await hooks["execute.before"]({ sessionID: "wire1", tool: "read", input: { path: T + "/other.c" } }) } catch (e) { err = e.message }
  expect("wiring: outside read blocked by the real hook", err, "outside the allowed")
  const ctx = { sessionID: "wire1", system: [] }
  await hooks["context"](ctx)
  expect("wiring: context hook injects the card ledger", ctx.system[0] && ctx.system[0].text, "gated session C1")
  delete process.env.GATE_FILE
}

// order rules: guidelines first, vault before project/SDK
{
  const vault = "/home/andre/llm-wiki"
  const vc = { ...card, seeds: ["/home/andre/workspace_ccstheia/zz", vault + "/Project筆記"], extra_reads: [wd + "/scope.md", T + "/guide.md"], first_reads: [T + "/guide.md"], required_reads: [], vault_first: true }
  fs.writeFileSync(T + "/guide.md", "guidelines")
  let vs = {}
  const vrun = (tool, input) => { const r = m.cardDecide(vc, vs, tool, JSON.stringify(input)); if (!r.block) vs = r.next; return r.block }
  const PF = "/home/andre/workspace_ccstheia/zz/a.c"
  expect("first_reads: other call blocked before the guidelines", vrun("read", { path: PF }), "first read the working guidelines")
  expect("first_reads: grep blocked too", vrun("grep", { pattern: "x", path: "/home/andre/workspace_ccstheia/zz" }), "first read the working guidelines")
  expect("first_reads: the guidelines themselves are readable", vrun("read", { path: T + "/guide.md" }), null)
  expect("vault_first: project file before any vault access blocked", vrun("read", { path: PF }), "vault first")
  expect("vault_first: vault grep ok", vrun("grep", { pattern: "x", path: vault + "/Project筆記" }), null)
  expect("vault_first: project file after the vault ok", vrun("read", { path: PF }), null)
}

// R8 on the normal path
const sid = "r8"
let n = { ...m.newState(), sid, karpathy: true, vault: true }
const dec = (tool, input) => { const r = m.decide(n, tool, JSON.stringify(input)); if (!r.block) n = r.next; return r.block }
expect("R8 execute blocked", dec("execute", { code: "print(1)" }), "GATE R8")
// R9: shell writes into the vault, the project or the SDK are blocked on the normal path; reading and writing elsewhere is not
{
  const V = "/home/andre/llm-wiki", PR = "/home/andre/workspace_ccstheia/drone/escape32_msp"
  const w = (command, base) => m.protectedShellWrite(command, base || "/tmp")
  expect("R9: cp a project file into the vault is blocked", w(`cp ${PR}/mcu/x/config.h ${V}/.tmp/config.h`), "cp")
  expect("R9: copying a project file to /tmp is reading, allowed", String(w(`cp ${PR}/src/io.c /tmp/io.c`)), "null")
  expect("R9: copying a vault note to /tmp is allowed", String(w(`cp ${V}/a.md /tmp/a.md`)), "null")
  expect("R9: moving a file out of the vault removes it, blocked", w(`mv ${V}/a.md /tmp/a.md`), "mv")
  expect("R9: cp -t into the vault is blocked", w(`cp -t ${V} /tmp/a /tmp/b`), "cp")
  expect("R9: ln -s into the vault is blocked", w(`ln -s /tmp/a ${V}/l`), "ln")
  expect("R9: cp into a protected directory is blocked", w(`cp /tmp/a ${V}/x`), "cp")
  expect("R9: the working directory counts at each command, not the last cd", String(w(`cp /tmp/a b && cd ${V}`, "/tmp")), "null")
  expect("R9: a redirect to a variable cannot be resolved and is refused", w('echo x > "$OUT"'), "cannot be resolved")
  expect("R9: a cp destination built from a variable is refused", w('cp /tmp/a "$D/x"'), "cannot be resolved")
  expect("R9: a cd to a variable makes later relative writes unresolvable", w('cd "$X" && echo y > out'), "cannot be resolved")
  expect("R9: a variable in a read-only part is fine", String(w('echo "$X" | grep a; ls "$D"')), "null")
  expect("R9: a heredoc body with cp and > is data, not a command", String(w("cat <<'EOF'\ncp a b\necho x > /home/andre/llm-wiki/z\nEOF", "/tmp")), "null")
  expect("R9: a redirect into the vault is blocked", w(`echo hi > ${V}/note.md`), "redirection")
  expect("R9: an append into the project is blocked", w(`echo hi >> ${PR}/src/io.c`), "redirection")
  expect("R9: tee into the vault is blocked", w(`cat x | tee ${V}/y.md`), "tee")
  expect("R9: sed -i on a project file is blocked", w(`sed -i s/a/b/ ${PR}/src/io.c`), "sed -i")
  expect("R9: cd into the vault then a relative cp is blocked", w(`cd ${V} && cp /tmp/a b`), "cp")
  expect("R9: a relative redirect with the vault as working directory is blocked", w("echo x > out.txt", V), "redirection")
  expect("R9: a relative cp with the vault as working directory is blocked", w("cp a b", V), "cp")
  expect("R9: mv out of the SDK is blocked", w("mv /home/andre/ti/sdk/a.h /tmp/a.h"), "mv")
  expect("R9: reading the vault is fine", String(w(`grep -rn foo ${V} 2>/dev/null | head`)), "null")
  expect("R9: reading the vault with the output in /tmp is fine", String(w(`grep -rn foo ${V} > /tmp/out.txt`)), "null")
  expect("R9: /dev/null and 2>&1 are fine", String(w(`ls ${V} >/dev/null 2>&1`)), "null")
  expect("R9: cp between /tmp files is fine", String(w("cp /tmp/a /tmp/b")), "null")
  expect("R9: cp between /tmp files with the vault as working directory is fine", String(w("cp /tmp/a /tmp/b", V)), "null")
  expect("R9: sed -n on a project file is fine", String(w(`sed -n 1,5p ${PR}/src/io.c`)), "null")
  expect("R9: sed -i on a /tmp file with the vault as working directory is fine", String(w("sed -i s/a/b/ /tmp/x", V)), "null")
  expect("R9: a > inside quotes is not a redirect", String(w(`grep ">" ${PR}/src/io.c`)), "null")
  expect("R9: a > or >= inside a quoted grep pattern is not a redirect (project as working directory)", String(w(`cd ${PR} && grep -n "t > 2000\\|a >= b" src/io.c`)), "null")
  expect("R9: > inside a heredoc body is not a redirect", String(w("python3 - <<'EOF'\nif a > b:\n    print(1)\nEOF", V)), "null")
  expect("R9: a redirect after a heredoc is still seen", w(`python3 - <<'EOF'\nx = 1\nEOF\necho y > ${V}/z.md`), "redirection")
  const r = m.decide({ ...m.newState(), karpathy: true, vault: true }, "bash", JSON.stringify({ command: `cp ${PR}/src/io.c ${V}/x.c` }))
  expect("R9: decide() blocks it", r.block, "GATE R9")
  expect("R9: decide() lets a plain read through", m.decide({ ...m.newState(), karpathy: true, vault: true }, "bash", JSON.stringify({ command: `sed -n 1,5p ${PR}/src/io.c` })).block, null)
}

// codegraph is gone: the command is not in the read-only list, and a directory search is an ordinary search
{
  const r = m.cardDecide({ ...card, first_reads: [] }, {}, "shell", JSON.stringify({ command: "codegraph query f -p " + src }))
  expect("codegraph is not an allowed command any more", r.block, "")
  expect("a directory grep is allowed", m.cardDecide({ ...card, first_reads: [] }, {}, "grep", JSON.stringify({ pattern: "ival", path: src })).block, null)
}

// command substitution: allowed when the inner command passes the same checks
{
  const c = { ...card, first_reads: [] }
  const sh = (command) => m.cardDecide(c, {}, "shell", JSON.stringify({ command })).block
  expect("substitution: a read-only grep inside $( ) is allowed", sh('echo "n=$(grep -c ival ' + src + '/cap.c)"'), null)
  expect("substitution: backticks with a read-only command are allowed", sh("echo `grep -c ival " + src + "/cap.c`"), null)
  expect("substitution: a command outside the read-only list inside $( ) is blocked", sh("echo $(rm " + src + "/cap.c)"), "GATE")
  expect("substitution: a read outside the roots inside $( ) is blocked", sh("echo $(cat /etc/passwd)"), "outside the allowed sources")
  expect("substitution: single-quoted text is not a substitution", sh("echo '$(rm x)'"), null)
  expect("substitution: a redirection outside is still blocked", sh("echo $(ls " + src + ") > " + src + "/o.txt"), "redirection")
}

// shell scanning and what the shell may do
{
  const sc = (c) => m.scanShell(c)
  expect("scan: a | inside quotes does not split", String(sc('grep -n "a\\|b" f | head').segs.length), "2")
  expect("scan: regex alternation in quotes is one word", String(sc('ls "x" | grep -iE "COMP|TIM|IFTIM"').segs.length), "2")
  expect("scan: && splits", String(sc("cd /a && ls").segs.length), "2")
  expect("scan: a ; inside single quotes does not split", String(sc("echo 'a;b'").segs.length), "1")
  expect("scan: redirection outside quotes is bad", sc("cat x > y").bad, "redirection")
  expect("scan: > inside quotes is fine", String(sc('grep ">" f').bad), "null")
  expect("scan: command substitution is bad even in double quotes", sc('echo "$(id)"').bad, "command substitution")
  expect("scan: command substitution in single quotes is literal", String(sc("echo '$(id)'").bad), "null")
  const V = "/home/andre/llm-wiki"
  const cv = { ...card, first_reads: [], vault_first: false, seeds: [src, V], out: wd + "/facts/V.facts.md" }
  let sv = {}
  const vx = (tool, input) => { const r = m.cardDecide(cv, sv, tool, JSON.stringify(input)); if (!r.block) sv = r.next; return r.block }
  expect("shell: grep with an alternation in quotes, piped, is allowed", vx("shell", { command: 'grep -n "ertm\\|ival" ' + src + "/cap.c | head -5" }), null)
  expect("shell: cd into an allowed root then a command is allowed", vx("shell", { command: "cd " + src + " && grep -n ertm cap.c" }), null)
  expect("shell: cd outside the roots is refused", vx("shell", { command: "cd /etc && ls" }), "outside the allowed")
  expect("shell: cd to a relative path is refused", vx("shell", { command: "cd src && ls" }), "absolute path")
  expect("shell: an awk program starting with / is not a path", vx("shell", { command: "awk '/^int scale\\(/{f=1} f' " + src + "/cap.c" }), null)
  expect("shell: a sed address is not a path", vx("shell", { command: "sed -n '/ertm/p' " + src + "/cap.c" }), null)
  expect("shell: grep -i is just ignore-case", vx("shell", { command: "grep -rni ertm " + src }), null)
  expect("shell: stat and diff are read-only and allowed", vx("shell", { command: "stat " + src + "/cap.c && diff " + src + "/cap.c " + src + "/ref.h" }), null)
  expect("shell: sed -i is refused", vx("shell", { command: "sed -i s/a/b/ " + src + "/cap.c" }), "sed -i")
  expect("shell: find -exec is refused", vx("shell", { command: "find " + src + " -exec rm {} ;" }), "find -exec")
  expect("shell: a path outside the roots is refused", vx("shell", { command: "cat /etc/passwd" }), "outside the allowed")
  expect("shell: a keyword search of the vault is fine", vx("shell", { command: "grep -rilE 'eRPM|BEMF|COMP' " + V + " --include=*.md" }), null)
}

// the work session: one draft file, nothing checked while it is written, the check command allowed, big files and re-reads are not blocked
{
  const W = T + "/work"; fs.mkdirSync(W, { recursive: true })
  fs.writeFileSync(W + "/work.json", JSON.stringify({ seeds: [src], deny: [] }))
  fs.writeFileSync(T + "/bigfile.md", ("line of text\n").repeat(5000))
  const wscript = "/home/andre/.agents/skills/work-run/scripts/workrun.py"
  const draft = W + "/draft.md"
  const cw = { mode: "work", id: "W", question: "Q?", out: draft, seeds: [src, T], extra_reads: [W + "/request.md"], required_reads: [], first_reads: [src + "/ref.h"], vault_first: false, deny: [], script: wscript, work_file: W + "/work.json", workdir: W, gate_level: "thin" }
  let sw = {}
  const wk = (tool, input) => { const r = m.cardDecide(cw, sw, tool, JSON.stringify(input)); if (!r.block) sw = r.next; return r.block }
  expect("work: the guidelines come first", wk("grep", { pattern: "x", path: src }), "first read the working guidelines")
  wk("read", { path: src + "/ref.h" })
  const bad = "---\ntitle: t\n---\n## Provenance\n| id | status | source | quote | claim |\n|---|---|---|---|---|\n| S1 | verified | `" + src + "/cap.c:2` | `not in the file` | x |\n"
  expect("work: a draft with a wrong row is written without a check", wk("write", { path: draft, content: bad }), null)
  fs.writeFileSync(draft, bad)
  expect("work: the draft can be edited", wk("edit", { path: draft, oldString: "not in the file", newString: "int a;" }), null)
  expect("work: a write to another file is refused", wk("write", { path: W + "/other.md", content: "x" }), "write only to")
  expect("work: an edit of a source is refused", wk("edit", { path: src + "/cap.c", oldString: "int a", newString: "int b" }), "edit only to")
  expect("work: the check command is allowed", wk("shell", { command: "python3 " + wscript + " check-note --work " + W + "/work.json --file " + draft }), null)
  expect("work: any other python3 is refused", wk("shell", { command: "python3 -c 'print(1)'" }), "only for the check command")
  expect("work: the check command on another file is refused", wk("shell", { command: "python3 " + wscript + " check-note --work " + W + "/work.json --file " + T + "/x.md" }), "only for the check command")
  expect("work: a whole big file is not blocked", wk("read", { path: T + "/bigfile.md" }), null)
  wk("read", { path: src + "/cap.c", offset: 1, limit: 5 }); wk("read", { path: src + "/cap.c", offset: 1, limit: 5 })
  expect("work: a third read of the same range is not blocked", wk("read", { path: src + "/cap.c", offset: 1, limit: 5 }), null)
  expect("work: a read outside the roots is refused", wk("read", { path: "/etc/hostname" }), "outside the allowed")
  expect("work: the dangerous shell is refused", wk("shell", { command: "echo x > " + src + "/cap.c" }), "redirection")
  expect("work: the excluded source is refused", m.cardDecide({ ...cw, deny: ["cap.c"] }, sw, "read", JSON.stringify({ path: src + "/cap.c" })).block, "excluded source")
  const rv = m.cardDecide({ ...cw, vault_first: true, first_reads: [], seeds: ["/home/andre/workspace_ccstheia", "/home/andre/llm-wiki"] }, {}, "read", JSON.stringify({ path: "/home/andre/workspace_ccstheia/x.c" }))
  expect("work: vault first still blocks", rv.block, "vault first")
  const L = m.cardLedger({ ...cw, question: "How is X measured?" }, sw)
  console.log(/Request \(word for word\): How is X measured\?/.test(L) && /Research path: the vault first/.test(L) && /nothing is checked while you write/.test(L) && /STATUS: the job ends when you stop/.test(L) ? "ok   the ledger carries the request, the research path and the draft rule" : (fails++, "FAIL work ledger: " + L))
}

// modules: the vault note of a module is found when a call names the module, and the ledger reminds until it has been read
{
  const VT = T + "/vault"; fs.mkdirSync(VT + "/專業領域/CHIPX", { recursive: true })
  fs.writeFileSync(VT + "/專業領域/CHIPX/COMP模組分析 (X).md", "# comp\n"); fs.writeFileSync(VT + "/專業領域/CHIPX/TIMx模組分析 (X).md", "# tim\n"); fs.writeFileSync(VT + "/專業領域/CHIPX/README.md", "x")
  const found = m.trmNotesFor("COMP", VT)
  expect("modules: the module note is found by its prefix", found.map((x) => x.split("/").pop()).join(","), "COMP模組分析 (X).md")
  expect("modules: a prefix without a note finds nothing", String(m.trmNotesFor("OPA", VT).length), "0")
  const cm = { mode: "work", id: "W", out: T + "/mw/draft.md", seeds: [src], extra_reads: [], required_reads: [], first_reads: [], vault_first: false, deny: [], script: "x", work_file: "y" }
  let s7 = {}
  const r1 = m.cardDecide(cm, s7, "grep", JSON.stringify({ pattern: "ertm", path: src }))
  expect("modules: a call that names no module leaves no reminder", Object.keys(r1.next.mods).join(","), "")
  const r2 = m.cardDecide(cm, r1.next, "grep", JSON.stringify({ pattern: "DL_COMP_init|COMP_INST", path: src }))
  console.log(Object.keys(r2.next.mods).includes("comp") && r2.next.advice.some((a) => a === "module-note:comp") ? "ok   modules: a call that names COMP is noted and leaves advice, not a block" : (fails++, "FAIL modules: " + JSON.stringify(r2.next.mods) + JSON.stringify(r2.next.advice)))
  expect("modules: the call is not blocked", r2.block, null)
  const withNote = { ...r2.next, mods: { comp: [VT + "/專業領域/CHIPX/COMP模組分析 (X).md"] }, modRead: [] }
  expect("modules: the ledger lists the unread note", m.cardLedger({ ...cm, question: "Q" }, withNote), "COMP模組分析 (X).md")
  const readIt = m.cardDecide({ ...cm, seeds: [src, VT] }, withNote, "read", JSON.stringify({ path: VT + "/專業領域/CHIPX/COMP模組分析 (X).md", offset: 1, limit: 20 }))
  console.log(readIt.next.modRead.length === 1 && !/Module notes your calls touched/.test(m.cardLedger({ ...cm, question: "Q" }, readIt.next)) ? "ok   modules: reading the note clears the reminder" : (fails++, "FAIL modules: reminder not cleared"))
}

fs.rmSync(T, { recursive: true, force: true })
console.log(fails ? fails + " FAILED" : "all passed")
process.exit(fails ? 1 : 0)
