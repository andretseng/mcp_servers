/**
 * Pure state machine for the review gate (no pi imports, unit-testable with node).
 *
 * Rules enforced:
 *  1. Changed files => a completed adviser review is required.
 *  2. Every completed review needs a disposition (adopt | reject).
 *  3. adopt + decision "change" => files must change after the review;
 *     adopt + "need-evidence" => a new review is required.
 *  4. reject => challenge the SAME reviewer (round 2, xhigh); if still no agreement after 2
 *     reviewer rounds, escalate to the arbiter (Codex, --backend codex, no failover), whose
 *     decision is final unless the executor has objective evidence; max 3 rounds, then the
 *     user decides. Restricted data never goes to Codex: the user decides.
 */

export type Verdict = "adopt" | "reject";

export interface Disposition {
	verdict: Verdict;
	reason: string;
	evidence?: string;
	restricted?: boolean;
}

export interface Review {
	backend: string;
	decision: string;
	isCodex: boolean;
	contextId?: string;
	disposition?: Disposition;
}

export interface GateState {
	changedFiles: boolean;
	editsSinceReview: boolean;
	reviews: Review[];
	nudges: number;
	escalated: boolean;
	arbiter: string;
	contextId?: string;
	classification?: string;
	runId: string;
	violations: string[];
	reportedViolations: boolean;
}

export const MAX_ROUNDS = 3;
export const DISCUSSION_ROUNDS = 2; // reviewer rounds before escalating to the arbiter
export const MAX_NUDGES = 8;
export const SKILL = "~/.agents/skills/execute-with-adviser/SKILL.md";

export function newState(arbiter = "codex"): GateState {
	return { changedFiles: false, editsSinceReview: false, reviews: [], nudges: 0, escalated: false, arbiter, runId: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`, violations: [], reportedViolations: false };
}

/** Recognise a completed adviser_bridge.py run from its bash command and output. */
export function parseBridgeCall(command: string, output: string, arbiter = "codex"): Review | null {
	if (!command.includes("adviser_bridge.py")) return null;
	if (!/"state":\s*"completed"/.test(output)) return null;
	const decision = /"decision":\s*"([a-z-]+)"/.exec(output)?.[1];
	if (!decision) return null;
	const backend = /--backend\s+(\w+)/.exec(command)?.[1] ?? "claude";
	// Only a pure arbiter call (no failover) is known to have been answered by the arbiter.
	const isCodex = backend === arbiter && !/--failover/.test(command);
	const contextId = /"context_id":\s*"([^"]+)"/.exec(output)?.[1];
	return { backend, decision, isCodex, contextId };
}

/** Returns false when the review is ignored because the executor had already agreed. */
export function recordReview(state: GateState, review: Review): boolean {
	const previous = state.reviews.at(-1);
	if (previous?.disposition?.verdict === "adopt" && previous.decision !== "need-evidence") {
		state.violations.push(`review round after agreement (${review.backend}, ${review.decision}) ignored`);
		return false;
	}
	state.reviews.push(review);
	state.contextId ??= review.contextId;
	state.editsSinceReview = false;
	return true;
}

/**
 * Heuristic: does this bash command change files the user cares about? Review request
 * files and anything under /tmp do not count. Redirections, tee, sed -i, cp, mv, rm, patch,
 * git apply/commit. False negatives are possible (e.g. python -c "open(...).write").
 */
export function bashChangesFiles(command: string): boolean {
	if (/adviser_bridge\.py|evidence_pack\.py/.test(command)) return false;
	const targets: string[] = [];
	for (const m of command.matchAll(/(?:^|[^0-9&>])>>?\s*([^\s&|;<>]+)/g)) targets.push(m[1]);
	for (const m of command.matchAll(/\btee\s+(?:-a\s+)?([^\s|;&]+)/g)) targets.push(m[1]);
	for (const m of command.matchAll(/\b(?:sed\s+-[a-zA-Z]*i\S*|cp|mv|rm|install|patch|truncate|touch)\s+(?:-\S+\s+)*([^\s|;&]+)/g)) targets.push(m[1]);
	const real = targets.filter(
		(t) => t !== "/dev/null" && !t.startsWith("/dev/") && !t.startsWith("/tmp/") && !/(^|\/)(req|request|packed|review|response)[\w.-]*\.json$/.test(t),
	);
	if (real.length > 0 || /\bgit\s+(apply|commit|checkout|reset|merge|rebase)\b/.test(command)) return true;
	// Scripting languages can write files without a shell redirection (python -c, node -e, perl -e ...).
	const scripted = /\b(python3?|node|perl|ruby)\b/.test(command);
	const writes = /\.write\(|write_text\(|write_bytes\(|\bopen\([^)]*['"][wax]\+?b?['"]|writeFileSync|writeFile\(|appendFile|shutil\.(copy|move|rmtree)|os\.(rename|remove|unlink|makedirs)|\.unlink\(/.test(command);
	if (scripted && writes) {
		const homePaths = [...command.matchAll(/(?:~\/|\/home\/|\/Users\/)[^\s'")]+/g)].map((m) => m[0]);
		if (homePaths.length > 0) return true;
		return !/\/tmp\//.test(command); // relative path with no /tmp reference: assume the project
	}
	return false;
}

export function recordEdit(state: GateState): void {
	state.changedFiles = true;
	state.editsSinceReview = true;
}

/** A run that only read things (an answer, not a change) still has a result to review. Reading after a review counts as re-checking it. */
export function recordWork(state: GateState): void {
	state.changedFiles = true;
	state.editsSinceReview = true;
}

export function setDisposition(state: GateState, d: Disposition): string {
	const last = state.reviews.at(-1);
	if (!last) throw new Error("No completed adviser review yet. Run the review first.");
	if (!d.reason.trim()) throw new Error("A reason is required.");
	last.disposition = d;
	return `Recorded: ${d.verdict} (review ${state.reviews.length}/${MAX_ROUNDS}, backend ${last.backend}, decision ${last.decision}).`;
}

export type Action =
	| { kind: "ok" }
	| { kind: "nudge"; message: string }
	| { kind: "escalate"; message: string };

function arbiterCall(arbiter: string): string {
	return (
		"Run the second-round review: write a NEW request (new id, same context_id, consult_index = " +
		"the round number, only the new evidence, executor_position = your reasoned disagreement), " +
		`then run \`adviser_bridge.py --backend ${arbiter}\` (no --failover).`
	);
}

export function nextAction(state: GateState): Action {
	if (!state.changedFiles) return { kind: "ok" };
	if (state.nudges >= MAX_NUDGES) {
		return {
			kind: "escalate",
			message:
				"review-gate stopped enforcing after too many reminders. Tell the user the result is " +
				"UNREVIEWED or unresolved, and list what remains open.",
		};
	}
	const rounds = state.reviews.length;
	const last = state.reviews.at(-1);
	if (!last) {
		return {
			kind: "nudge",
			message:
				`REVIEW REQUIRED: you changed files but no completed adviser review ran. Do not report ` +
				`the task as finished. Read ${SKILL} and follow it, then call the review_disposition ` +
				`tool. If every reviewer backend fails, say the result is UNREVIEWED.`,
		};
	}
	const d = last.disposition;
	if (!d) {
		return {
			kind: "nudge",
			message:
				`DISPOSITION REQUIRED: the review returned "${last.decision}". Call the review_disposition ` +
				`tool now: verdict "adopt" if you follow it, or "reject" with your reason (and objective ` +
				`evidence if you have it).`,
		};
	}
	if (d.verdict === "reject") {
		if (d.restricted) {
			return {
				kind: "escalate",
				message:
					"You rejected the review but the data is restricted, so Codex cannot arbitrate. Stop and " +
					"ask the user to decide; list agreed_items, disagreed_items, the adviser's decision and " +
					"your objective evidence.",
			};
		}
		if (last.isCodex) {
			// The arbiter's decision is final unless the executor has objective counter-evidence.
			if (!d.evidence?.trim()) {
				return {
					kind: "nudge",
					message:
						`THE ARBITER'S DECISION IS FINAL unless you have objective counter-evidence (a failing ` +
						`test, a spec line, a measurement). You rejected without evidence. Call review_disposition ` +
						`again with verdict "adopt" and follow "${last.decision}", or reject with evidence.`,
				};
			}
			return {
				kind: "escalate",
				message:
					`You still reject the arbiter's decision and have evidence, but the round limit ` +
					`(${MAX_ROUNDS}) is reached. Stop and ask the user to decide. List agreed_items, ` +
					`disagreed_items, each reviewer's decision, and your objective evidence.`,
			};
		}
		const discussion = state.reviews.filter((r) => !r.isCodex).length;
		if (discussion < DISCUSSION_ROUNDS) {
			return {
				kind: "nudge",
				message:
					`CHALLENGE ROUND ${rounds + 1}/${MAX_ROUNDS}: you rejected the review, so challenge it. Write a ` +
					`NEW request (new id, same context_id, consult_index ${rounds + 1}, only the new evidence, ` +
					`executor_position = your reasoned disagreement and the evidence behind it) and send it to the ` +
					`SAME reviewer backend (\`--backend ${last.backend}\`). Round 2 and later run at xhigh effort. ` +
					`If the reviewer is persuaded it will say so; if not, we escalate.`,
			};
		}
		if (rounds >= MAX_ROUNDS) {
			return {
				kind: "escalate",
				message:
					`The review rounds ended without agreement. Stop and ask the user to decide. List ` +
					`agreed_items, disagreed_items, each adviser's decision, and the objective evidence.`,
			};
		}
		return {
			kind: "nudge",
			message:
				`ESCALATE TO ${state.arbiter.toUpperCase()}: you and the reviewer still disagree after ` +
				`${discussion} rounds. ${state.arbiter} makes the final call. ${arbiterCall(state.arbiter)}`,
		};
	}
	// adopt
	if (last.decision === "change" && !state.editsSinceReview) {
		return {
			kind: "nudge",
			message:
				`You adopted the review's "change" decision but changed nothing afterwards. Apply it ` +
				`(write/edit), re-run your verification, and report what changed.`,
		};
	}
	if (last.decision === "need-evidence") {
		return {
			kind: "nudge",
			message:
				`The adviser asked for more evidence. Gather it, then run a new review round ` +
				`(${rounds + 1}/${MAX_ROUNDS}) with the added evidence and record its disposition.`,
		};
	}
	if (last.isCodex && last.decision === "stop") {
		return {
			kind: "nudge",
			message: `Codex decided "stop". Do not report the task as done; report the blocker to the user.`,
		};
	}
	return { kind: "ok" };
}

/**
 * Immediate enforcement: called before every tool call. Returns a reason to block, or null.
 * - After a completed review and before its disposition: no write/edit and no new bridge call.
 * - Agreement (adopt) ends the review: no further bridge round (except after need-evidence).
 * - A new bridge round must follow the flow: reject => challenge the SAME reviewer
 *   (first two reviewer rounds), then the arbiter; the arbiter's word is final; max rounds.
 */
export interface RequestInfo {
	consult_index?: number;
	context_id?: string;
	data_classification?: string;
}

export function checkToolCall(
	state: GateState,
	toolName: string,
	input: Record<string, unknown>,
	request: RequestInfo | null = null,
): string | null {
	if (toolName === "review_disposition") return null;
	const command = String(input.command ?? "");
	const isBridge = toolName === "bash" && command.includes("adviser_bridge.py");
	const last = state.reviews.at(-1);
	const d = last?.disposition;

	if (isBridge && request && /--backend\s+(codex|claude|omlx|scripted)\b/.test(command)) {
		// Effort and round limits come from consult_index, so the executor may not reset it.
		const expected = state.reviews.length + 1;
		if (request.consult_index !== expected) {
			return `This is review round ${expected}: set consult_index to ${expected} in the request (round 1 runs at low effort, later rounds at xhigh). You cannot restart the count.`;
		}
		if (state.contextId && request.context_id !== state.contextId) {
			return `Keep the same context_id ("${state.contextId}") for every round of this review.`;
		}
	}
	if (!last) return null;
	if (!d) {
		if (toolName === "write" || toolName === "edit" || isBridge) {
			return (
				`Record your disposition for the review that just returned ("${last.decision}") before ` +
				`${isBridge ? "starting another review round" : "changing files"}: call review_disposition ` +
				`(adopt or reject, with a reason). You may read and verify first.`
			);
		}
		return null;
	}
	if (!isBridge) return null;
	if (d.verdict === "adopt") {
		// Agreement ends the review. Only "need-evidence" legitimately asks for another round.
		if (last.decision === "need-evidence") return null;
		return "You adopted the adviser's decision, so the review is finished. Do not start another round: apply it and report.";
	}

	const rounds = state.reviews.length;
	if (d.restricted) return "The data is restricted: Codex cannot see it. Ask the user to decide.";
	if (last.isCodex) return "The arbiter already decided; its decision is final. Adopt it, or tell the user you have objective counter-evidence.";
	if (rounds >= MAX_ROUNDS) return `The ${MAX_ROUNDS}-round limit is reached. Ask the user to decide.`;
	const backend = /--backend\s+(\w+)/.exec(command)?.[1];
	const pureArbiter = backend === state.arbiter && !/--failover/.test(command);
	const discussion = state.reviews.filter((r) => !r.isCodex).length;
	if (discussion < DISCUSSION_ROUNDS) {
		if (backend !== last.backend) {
			return `You rejected the review: challenge the SAME reviewer first (--backend ${last.backend}), as round ${rounds + 1}.`;
		}
		return null;
	}
	if (!pureArbiter) {
		return `Two reviewer rounds ended without agreement: escalate to the arbiter now (--backend ${state.arbiter}, no --failover).`;
	}
	return null;
}

/** Ledger records (one JSON object per line); reasons are omitted for restricted data. */
export function dispositionRecord(state: GateState, executor: string, d: Disposition, now = new Date()) {
	const last = state.reviews.at(-1);
	const restricted = state.classification === "restricted" || d.restricted === true;
	return {
		schema: 1,
		event: "disposition",
		ts: now.toISOString().replace(/\.\d+Z$/, "Z"),
		run_id: state.runId,
		executor,
		context_id: state.contextId ?? null,
		round: state.reviews.length,
		reviewer_backend: last?.backend ?? null,
		is_arbiter_review: last?.isCodex ?? false,
		review_decision: last?.decision ?? null,
		verdict: d.verdict,
		has_evidence: Boolean(d.evidence?.trim()),
		restricted,
		reason: restricted ? null : d.reason.slice(0, 300),
	};
}

export function runEndRecord(state: GateState, executor: string, now = new Date()) {
	const last = state.reviews.at(-1);
	return {
		schema: 1,
		event: "run-end",
		ts: now.toISOString().replace(/\.\d+Z$/, "Z"),
		run_id: state.runId,
		executor,
		context_id: state.contextId ?? null,
		changed_files: state.changedFiles,
		rounds: state.reviews.length,
		backends: state.reviews.map((r) => r.backend),
		decisions: state.reviews.map((r) => r.decision),
		verdicts: state.reviews.map((r) => r.disposition?.verdict ?? null),
		final_verdict: last?.disposition?.verdict ?? null,
		nudges: state.nudges,
		escalated: state.escalated,
		violations: state.violations,
	};
}
