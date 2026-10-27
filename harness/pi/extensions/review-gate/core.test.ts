import { test } from "node:test";
import assert from "node:assert/strict";
import {
	bashChangesFiles, checkToolCall, recordWork, dispositionRecord, runEndRecord, newState, nextAction, parseBridgeCall, recordEdit, recordReview, setDisposition, MAX_NUDGES,
} from "./core.ts";

const out = (decision: string) => `{\n  "state": "completed",\n  "decision": "${decision}"\n}`;
const cmd = (args: string) => `cd x; uv run --script ~/.agents/skills/execute-with-adviser/scripts/adviser_bridge.py --request p.json ${args}`;

function reviewed(args: string, decision: string) {
	const s = newState();
	recordEdit(s);
	recordReview(s, parseBridgeCall(cmd(args), out(decision))!);
	return s;
}

test("no file changes => ok", () => {
	assert.equal(nextAction(newState()).kind, "ok");
});

test("changed files without review => nudge", () => {
	const s = newState();
	recordEdit(s);
	assert.equal(nextAction(s).kind, "nudge");
});

test("parseBridgeCall ignores non-bridge and non-completed", () => {
	assert.equal(parseBridgeCall("ls", out("proceed")), null);
	assert.equal(parseBridgeCall(cmd("--backend omlx"), `{"state": "failed"}`), null);
	assert.equal(parseBridgeCall(cmd("--backend omlx"), `{"state": "completed"}`), null);
});

test("codex with failover is not treated as a pure Codex answer", () => {
	assert.equal(parseBridgeCall(cmd("--backend codex --failover claude,omlx"), out("proceed"))!.isCodex, false);
	assert.equal(parseBridgeCall(cmd("--backend codex"), out("proceed"))!.isCodex, true);
});

test("review without disposition => nudge for disposition", () => {
	const a = nextAction(reviewed("--backend omlx", "proceed"));
	assert.equal(a.kind, "nudge");
	assert.match((a as { message: string }).message, /DISPOSITION/);
});

test("adopt proceed => ok", () => {
	const s = reviewed("--backend omlx", "proceed");
	setDisposition(s, { verdict: "adopt", reason: "agree" });
	assert.equal(nextAction(s).kind, "ok");
});

test("adopt change needs an edit afterwards", () => {
	const s = reviewed("--backend omlx", "change");
	setDisposition(s, { verdict: "adopt", reason: "agree" });
	assert.equal(nextAction(s).kind, "nudge");
	recordEdit(s);
	assert.equal(nextAction(s).kind, "ok");
});

test("adopt need-evidence needs a new review", () => {
	const s = reviewed("--backend omlx", "need-evidence");
	setDisposition(s, { verdict: "adopt", reason: "will gather" });
	assert.equal(nextAction(s).kind, "nudge");
});

test("reject => challenge the same reviewer first; restricted goes to the user", () => {
	const s = reviewed("--backend omlx", "change");
	setDisposition(s, { verdict: "reject", reason: "disagree" });
	const m = (nextAction(s) as { message: string }).message;
	assert.match(m, /CHALLENGE ROUND 2/);
	assert.match(m, /--backend omlx/);
	setDisposition(s, { verdict: "reject", reason: "disagree", restricted: true });
	assert.equal(nextAction(s).kind, "escalate");
});

test("two reviewer rounds without agreement => escalate to Codex; Codex is final", () => {
	const s = reviewed("--backend omlx", "change");
	setDisposition(s, { verdict: "reject", reason: "disagree" });
	recordReview(s, parseBridgeCall(cmd("--backend omlx"), out("change"))!);
	setDisposition(s, { verdict: "reject", reason: "still disagree", evidence: "test_x fails" });
	assert.match((nextAction(s) as { message: string }).message, /ESCALATE TO CODEX/);
	recordReview(s, parseBridgeCall(cmd("--backend codex"), out("change"))!);
	setDisposition(s, { verdict: "reject", reason: "still disagree" });
	assert.match((nextAction(s) as { message: string }).message, /FINAL/);
	setDisposition(s, { verdict: "reject", reason: "test fails", evidence: "test_x fails: expected 3 got 2" });
	assert.equal(nextAction(s).kind, "escalate");
});

test("reviewer concedes in the challenge round => ok after adopt", () => {
	const s = reviewed("--backend omlx", "change");
	setDisposition(s, { verdict: "reject", reason: "disagree" });
	recordReview(s, parseBridgeCall(cmd("--backend omlx"), out("proceed"))!);
	setDisposition(s, { verdict: "adopt", reason: "reviewer agrees" });
	assert.equal(nextAction(s).kind, "ok");
});

test("custom arbiter is recognised", () => {
	assert.equal(parseBridgeCall(cmd("--backend omlx"), out("proceed"), "omlx")!.isCodex, true);
});

test("Codex first review: adopt change then edit => ok", () => {
	const s = reviewed("--backend codex", "change");
	setDisposition(s, { verdict: "adopt", reason: "ok" });
	recordEdit(s);
	assert.equal(nextAction(s).kind, "ok");
});

test("setDisposition without review throws; nudge cap escalates", () => {
	assert.throws(() => setDisposition(newState(), { verdict: "adopt", reason: "x" }));
	const s = newState();
	recordEdit(s);
	s.nudges = MAX_NUDGES;
	assert.equal(nextAction(s).kind, "escalate");
});

test("tool_call: no write/edit/new bridge round before the disposition", () => {
	const s = reviewed("--backend omlx", "change");
	assert.match(checkToolCall(s, "write", {})!, /disposition/);
	assert.match(checkToolCall(s, "edit", {})!, /disposition/);
	assert.match(checkToolCall(s, "bash", { command: cmd("--backend omlx") })!, /disposition/);
	assert.equal(checkToolCall(s, "read", {}), null);
	assert.equal(checkToolCall(s, "bash", { command: "grep -n x file" }), null);
	assert.equal(checkToolCall(s, "review_disposition", {}), null);
	setDisposition(s, { verdict: "adopt", reason: "ok" });
	assert.equal(checkToolCall(s, "write", {}), null);
});

test("tool_call: after reject, same reviewer first, then the arbiter only", () => {
	const s = reviewed("--backend omlx", "change");
	setDisposition(s, { verdict: "reject", reason: "no" });
	assert.match(checkToolCall(s, "bash", { command: cmd("--backend codex") })!, /SAME reviewer/);
	assert.equal(checkToolCall(s, "bash", { command: cmd("--backend omlx") }), null);
	recordReview(s, parseBridgeCall(cmd("--backend omlx"), out("change"))!);
	setDisposition(s, { verdict: "reject", reason: "no", evidence: "x" });
	assert.match(checkToolCall(s, "bash", { command: cmd("--backend omlx") })!, /arbiter/);
	assert.match(checkToolCall(s, "bash", { command: cmd("--backend codex --failover claude") })!, /arbiter/);
	assert.equal(checkToolCall(s, "bash", { command: cmd("--backend codex") }), null);
	recordReview(s, parseBridgeCall(cmd("--backend codex"), out("change"))!);
	setDisposition(s, { verdict: "reject", reason: "no", evidence: "x" });
	assert.match(checkToolCall(s, "bash", { command: cmd("--backend codex") })!, /final|limit/);
});

test("tool_call: restricted rejection blocks further rounds", () => {
	const s = reviewed("--backend omlx", "change");
	setDisposition(s, { verdict: "reject", reason: "no", restricted: true });
	assert.match(checkToolCall(s, "bash", { command: cmd("--backend omlx") })!, /restricted/);
});

test("tool_call: adopting ends the review; need-evidence still allows a new round", () => {
	const s = reviewed("--backend omlx", "proceed");
	setDisposition(s, { verdict: "adopt", reason: "agree" });
	assert.match(checkToolCall(s, "bash", { command: cmd("--backend omlx") })!, /finished/);
	assert.match(checkToolCall(s, "bash", { command: cmd("--backend codex") })!, /finished/);
	assert.equal(checkToolCall(s, "write", {}), null);
	const n = reviewed("--backend omlx", "need-evidence");
	setDisposition(n, { verdict: "adopt", reason: "will gather" });
	assert.equal(checkToolCall(n, "bash", { command: cmd("--backend omlx") }), null);
});

test("review after agreement is ignored and recorded as a violation", () => {
	const s = reviewed("--backend omlx", "change");
	setDisposition(s, { verdict: "adopt", reason: "ok" });
	const second = parseBridgeCall(cmd("--backend omlx"), out("proceed"))!;
	assert.equal(recordReview(s, second), false);
	assert.equal(s.reviews.length, 1);
	assert.equal(s.violations.length, 1);
	// need-evidence is the exception: a new round is legitimate
	const n = reviewed("--backend omlx", "need-evidence");
	setDisposition(n, { verdict: "adopt", reason: "will gather" });
	assert.equal(recordReview(n, second), true);
});

test("round and context are enforced from the request, not trusted", () => {
	const s = newState();
	const first = (i: number, c: string) => checkToolCall(s, "bash", { command: cmd("--backend omlx") }, { consult_index: i, context_id: c });
	assert.match(first(2, "ctx")!, /round 1/);
	assert.equal(first(1, "ctx"), null);
	recordEdit(s);
	recordReview(s, { ...parseBridgeCall(cmd("--backend omlx"), out("change") + ' "context_id": "ctx"')! });
	assert.equal(s.contextId, "ctx");
	setDisposition(s, { verdict: "reject", reason: "no" });
	assert.match(checkToolCall(s, "bash", { command: cmd("--backend omlx") }, { consult_index: 1, context_id: "ctx" })!, /round 2/);
	assert.match(checkToolCall(s, "bash", { command: cmd("--backend omlx") }, { consult_index: 2, context_id: "other" })!, /same context_id/);
	assert.equal(checkToolCall(s, "bash", { command: cmd("--backend omlx") }, { consult_index: 2, context_id: "ctx" }), null);
});

test("bashChangesFiles heuristics", () => {
	assert.equal(bashChangesFiles("cat > src/main.c <<EOF"), true);
	assert.equal(bashChangesFiles("echo hi >> notes.txt"), true);
	assert.equal(bashChangesFiles("sed -i 's/a/b/' x.c"), true);
	assert.equal(bashChangesFiles("rm build/out.o"), true);
	assert.equal(bashChangesFiles("git commit -m x"), true);
	assert.equal(bashChangesFiles("grep -n x file.c"), false);
	assert.equal(bashChangesFiles("gcc a.c 2>&1 | tail"), false);
	assert.equal(bashChangesFiles("echo x > /dev/null"), false);
	assert.equal(bashChangesFiles("cat > /tmp/work/req.json"), false);
	assert.equal(bashChangesFiles("cat > request_r2.json"), false);
	assert.equal(bashChangesFiles("uv run --script adviser_bridge.py --request r.json > out.txt"), false);
});

test("bashChangesFiles catches scripted writes (python/node) but not /tmp or reads", () => {
	assert.equal(bashChangesFiles(`python3 -c "open('/home/andre/x/hello.py','w').write('print(1)')"`), true);
	assert.equal(bashChangesFiles(`python3 - <<EOF\nfrom pathlib import Path\nPath('out.txt').write_text('x')\nEOF`), true);
	assert.equal(bashChangesFiles(`node -e "require('fs').writeFileSync('a.js','x')"`), true);
	assert.equal(bashChangesFiles(`python3 -c "open('/tmp/scratch.txt','w').write('x')"`), false);
	assert.equal(bashChangesFiles(`python3 -c "print(open('data.txt').read())"`), false);
	assert.equal(bashChangesFiles(`python3 script.py`), false);
});

test("ledger records: disposition and run-end, restricted reasons are dropped", () => {
	const s = reviewed("--backend omlx", "change");
	s.contextId = "ctx-1";
	const d = { verdict: "reject" as const, reason: "x".repeat(500), evidence: "failing test" };
	setDisposition(s, d);
	const rec = dispositionRecord(s, "pi:dreammaker/Ornith", d, new Date("2026-10-12T15:00:00.123Z"));
	assert.equal(rec.ts, "2026-10-12T15:00:00Z");
	assert.equal(rec.executor, "pi:dreammaker/Ornith");
	assert.equal(rec.reason!.length, 300);
	assert.equal(rec.has_evidence, true);
	assert.equal(rec.review_decision, "change");
	assert.equal(rec.round, 1);
	s.classification = "restricted";
	assert.equal(dispositionRecord(s, "pi:x/y", d).reason, null);
	const end = runEndRecord(s, "pi:x/y");
	assert.deepEqual(end.verdicts, ["reject"]);
	assert.equal(end.final_verdict, "reject");
	assert.equal(end.context_id, "ctx-1");
});

test("a run that only read things still needs a review before it may finish", () => {
	const s = newState();
	recordWork(s);
	assert.equal(nextAction(s).kind, "nudge");
	recordReview(s, parseBridgeCall(cmd("--backend codex"), out("proceed"))!);
	setDisposition(s, { verdict: "adopt", reason: "agree" });
	assert.equal(nextAction(s).kind, "ok");
});

test("after a change decision on an answer, reading again counts as re-checking", () => {
	const s = newState();
	recordWork(s);
	recordReview(s, parseBridgeCall(cmd("--backend codex"), out("change"))!);
	setDisposition(s, { verdict: "adopt", reason: "agree" });
	assert.equal(nextAction(s).kind, "nudge");
	recordWork(s);
	assert.equal(nextAction(s).kind, "ok");
});
