#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""S2 tests: the council state machine, driven by scripted fake agents (no model, no GPU).
Run: uv run --script test_run.py -v

Each outcome (consensus, consensus-unverified, no-consensus, time-limit, aborted) and the anti-capitulation rule has a test;
mutation tests (see the plan note, section 8) check that these tests fail when the engine is broken."""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import council  # noqa: E402

MARK = re.compile(r"\[\[COUNCIL phase=(\w+) round=(\d+) attempt=(\d+)\]\]")
GOOD = 'src/io.c:3 "int m = 3;"'
BAD = 'src/io.c:3 "int m = 9;"'


def blk(id_, basis="verified", evidence=GOOD, stmt="m starts at 3", frm=None):
    s = f"ITEM {id_}\nstatement: {stmt}\nbasis: {basis}\nevidence: {evidence}\ncheck: read it\n"
    return s + (f"from: {frm}\n" if frm else "") + "\n"


class Scripted(council.Agent):
    def __init__(self, name, responder, tick=None):
        self.name, self.responder, self.tick = name, responder, tick
        self.prompts = []
        self.timeouts = []

    def ask(self, prompt, timeout):
        self.prompts.append(prompt)
        self.timeouts.append(timeout)
        if self.tick:
            self.tick()
        phase, rnd, attempt = MARK.search(prompt).groups()
        r = self.responder(phase, int(rnd), int(attempt), prompt)
        if isinstance(r, Exception):
            raise r
        return r

    def count(self, phase):
        return sum(1 for p in self.prompts if f"phase={phase} " in p)


def ornith(draft="D1", basis="verified", evidence=GOOD, revise=None, indep_text=None):
    """Integrator: independent -> one item; merge -> draft with `from:`; revise -> callable or NOCHANGE."""
    def responder(phase, rnd, attempt, prompt):
        if phase == "independent":
            return indep_text or blk("A1", basis, evidence)
        if phase == "merge":
            return blk(draft, basis, evidence, frm="Ornith/A1, Qwen/B1")
        if phase == "revise":
            return revise(rnd, prompt) if revise else "NOCHANGE\nREBUTTAL D1: the quote is on the cited line."
        raise AssertionError(phase)
    return responder


def qwen(review):
    def responder(phase, rnd, attempt, prompt):
        if phase == "independent":
            return blk("B1", "inferred", "measurement: none yet")
        if phase == "review":
            return review(rnd, attempt, prompt)
        raise AssertionError(phase)
    return responder


ACCEPT_ALL = lambda rnd, attempt, prompt: "RE D1: ACCEPT"  # noqa: E731


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "io.c").write_text("#include <stdint.h>\n// comment\nint m = 3;\n")
        self.out = self.tmp / "run"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def brief(self, **kw):
        d = dict(type="issue", task="Why is the RPM wrong?", participants=["Ornith", "Qwen"], roots=[str(self.tmp)])
        d.update(kw)
        return council.Brief(**d)

    def go(self, ornith_fn, qwen_fn, clock=None, tick=None, **kw):
        self.o, self.q = Scripted("Ornith", ornith_fn, tick), Scripted("Qwen", qwen_fn, tick)
        args = dict(clock=clock) if clock else {}
        self.res = council.run_council(self.brief(**kw), {"Ornith": self.o, "Qwen": self.q}, self.out, **args)
        return self.res


class ResumeTests(Base):
    """A run directory that already holds answered prompts lets the next run reuse them (an agent that timed out costs only its own turn)."""

    def timed_out_qwen(self, phase, rnd, attempt, prompt):
        return council.AgentError("timeout", "no answer after 30 min")

    def test_answered_prompts_are_reused_after_another_agent_timed_out(self):
        first = self.go(ornith(), self.timed_out_qwen)
        self.assertEqual(first["status"], "aborted")
        self.assertEqual(self.o.count("independent"), 1)
        second = self.go(ornith(), qwen(ACCEPT_ALL))
        self.assertEqual(second["status"], "consensus")
        self.assertEqual(self.o.count("independent"), 0, "Ornith was asked again for an answer it had already given")
        self.assertEqual(self.q.count("independent"), 1)
        self.assertIn("reused the saved reply r01-independent-Ornith.reply.md", (self.out / "ledger.md").read_text())

    def test_a_changed_prompt_is_not_answered_from_the_old_reply(self):
        self.go(ornith(), self.timed_out_qwen)
        self.go(ornith(), qwen(ACCEPT_ALL), task="A different question entirely")
        self.assertEqual(self.o.count("independent"), 1, "a reply to another question was reused")

    def test_a_rejected_first_attempt_is_not_asked_again_on_resume(self):
        good = ornith()
        bad_then_good = lambda ph, rnd, att, p: "no items here" if (ph, att) == ("independent", 1) else good(ph, rnd, att, p)  # noqa: E731
        self.go(bad_then_good, self.timed_out_qwen)
        self.assertEqual(self.o.count("independent"), 2)  # attempt 1 rejected, attempt 2 accepted
        second = self.go(bad_then_good, qwen(ACCEPT_ALL))
        self.assertEqual(second["status"], "consensus")
        self.assertEqual(self.o.count("independent"), 0, "Ornith was asked again after a resume although both attempts were saved")
        led = (self.out / "ledger.md").read_text()
        self.assertIn("saved reply", led)
        self.assertIn("reused the saved reply r01-independent-Ornith-a2.reply.md", led)

    def test_an_invalid_saved_reply_is_asked_again(self):
        self.go(ornith(), self.timed_out_qwen)
        (self.out / "r01-independent-Ornith.reply.md").write_text("no items here")
        self.go(ornith(), qwen(ACCEPT_ALL))
        self.assertEqual(self.o.count("independent"), 1)


class OutcomeTests(Base):
    def test_consensus_when_everything_is_accepted_and_verified(self):
        r = self.go(ornith(), qwen(ACCEPT_ALL))
        self.assertEqual(r["status"], "consensus")
        self.assertEqual(r["rounds"], 3)  # independent, merge, one review round
        self.assertEqual(r["items"][0]["basis_final"], "verified")
        self.assertIn("CONSENSUS: every reviewer", (self.out / "report.md").read_text())

    def test_consensus_unverified_is_a_normal_end(self):
        r = self.go(ornith(basis="inferred", evidence="measurement: not measured"), qwen(ACCEPT_ALL))
        self.assertEqual(r["status"], "consensus-unverified")
        self.assertEqual(r["rounds"], 3)
        rep = (self.out / "report.md").read_text()
        self.assertIn("CONSENSUS (UNVERIFIED)", rep)
        self.assertIn("normal end", rep)

    def test_a_verified_item_with_a_broken_quote_makes_the_consensus_unverified(self):
        r = self.go(ornith(evidence=BAD), qwen(ACCEPT_ALL))
        self.assertEqual(r["status"], "consensus-unverified")
        self.assertTrue(r["items"][0]["downgraded"])

    def test_objection_then_revision_then_accept(self):
        def review(rnd, attempt, prompt):
            return f'RE D1: OBJECT because the claim is too broad. evidence: {GOOD}' if rnd == 3 else "RE D1: ACCEPT"
        def revise(rnd, prompt):
            return blk("D1", "verified", GOOD, stmt="m starts at 3 on MSPM0 only", frm="Ornith/A1")
        r = self.go(ornith(revise=revise), qwen(review))
        self.assertEqual((r["status"], r["rounds"]), ("consensus", 4))
        self.assertEqual(r["flags"], [])  # the item changed, so switching to ACCEPT is legitimate
        self.assertIn("only", r["items"][0]["statement"])

    def test_round_cap_gives_no_consensus_with_the_open_disagreements(self):
        r = self.go(ornith(), qwen(lambda rnd, a, p: "RE D1: OBJECT because the quote is a coincidence. evidence: measurement: scope"),
                    max_rounds=6)
        self.assertEqual((r["status"], r["rounds"]), ("no-consensus", 6))
        self.assertEqual(r["open"][0]["id"], "D1")
        self.assertIn("coincidence", r["open"][0]["text"])
        self.assertIn("quote is on the cited line", r["open"][0]["rebuttal"])
        rep = (self.out / "report.md").read_text()
        self.assertIn("NO CONSENSUS", rep)
        self.assertIn("integrator rebuttal", rep)
        self.assertLess(rep.index("## Open items"), rep.index("D1"))
        self.assertNotIn("## Items", rep)

    def test_the_report_shows_the_check_text_with_its_steps(self):
        steps = "Run in this order:\n1) read A\n2) read B"
        def responder(phase, rnd, attempt, prompt):
            if phase == "independent":
                return blk("A1")
            return blk("D1", frm="Ornith/A1").replace("check: read it", "check: " + steps)
        self.go(responder, qwen(ACCEPT_ALL))
        rep = (self.out / "report.md").read_text()
        self.assertIn("check: Run in this order:", rep)
        self.assertIn("2) read B", rep)

    def two_items(self, revise=None):
        def responder(phase, rnd, attempt, prompt):
            if phase == "independent":
                return blk("A1")
            if phase == "merge":
                return blk("D1", frm="Ornith/A1") + blk("D2", frm="Ornith/A1")
            if revise:
                return revise(rnd, prompt)
            return "NOCHANGE\nREBUTTAL D2: the quote is on the cited line."
        return responder

    def test_no_consensus_report_starts_with_what_is_confirmed(self):
        review = lambda rnd, a, p: "RE D1: ACCEPT\nRE D2: OBJECT because the quote is a coincidence. evidence: measurement: scope"  # noqa: E731
        r = self.go(self.two_items(), qwen(review), max_rounds=4)
        self.assertEqual((r["confirmed"], [o["id"] for o in r["open"]], r["unreviewed"]), (["D1"], ["D2"], []))
        rep = (self.out / "report.md").read_text()
        self.assertLess(rep.index("## Confirmed"), rep.index("## Open items"))
        self.assertLess(rep.index("**D1**"), rep.index("## Open items"))
        self.assertGreater(rep.index("**D2**"), rep.index("## Open items"))

    def test_an_item_revised_after_the_last_review_is_not_called_confirmed(self):
        review = lambda rnd, a, p: "RE D1: ACCEPT\nRE D2: OBJECT because the quote is a coincidence. evidence: measurement: scope"  # noqa: E731
        revise = lambda rnd, p: blk("D1", stmt="m starts at 3 on MSPM0 only", frm="Ornith/A1") + blk("D2", frm="Ornith/A1")  # noqa: E731
        r = self.go(self.two_items(revise), qwen(review), max_rounds=3)
        self.assertEqual((r["confirmed"], r["unreviewed"]), ([], ["D1"]))
        self.assertEqual(r["changed_since_review"], ["D1"])  # D2 was re-issued unchanged
        rep = (self.out / "report.md").read_text()
        self.assertIn("## Not reviewed yet", rep)
        self.assertGreater(rep.index("**D1**"), rep.index("## Not reviewed yet"))

    def test_time_limit_stops_between_rounds(self):
        now = {"t": 0}
        r = self.go(ornith(), qwen(ACCEPT_ALL), clock=lambda: now["t"], tick=lambda: now.__setitem__("t", now["t"] + 2400),
                    time_limit_s=7200)
        self.assertEqual(r["status"], "time-limit")
        self.assertEqual(r["rounds"], 2)  # 3 asks x 2400 s = 7200 s before the first review round could start
        self.assertIn("120 min", r["reason"])

    def test_the_limit_holds_while_an_agent_is_still_working(self):
        now = {"t": 0}
        def merge_times_out(phase, rnd, attempt, prompt):
            if phase == "merge":
                return council.AgentError("timeout", "no answer")
            return ornith()(phase, rnd, attempt, prompt)
        r = self.go(merge_times_out, qwen(ACCEPT_ALL), clock=lambda: now["t"], tick=lambda: now.__setitem__("t", now["t"] + 3000),
                    time_limit_s=7200)
        self.assertEqual(r["status"], "time-limit")  # not "aborted": the agent ran out of the run's time
        self.assertEqual(self.o.timeouts[-1], 1200)  # the merge turn was given only the 1200 s that were left
        self.assertTrue((self.out / "report.md").exists())

    def test_a_silent_agent_aborts_and_keeps_the_partial_run(self):
        def review(rnd, attempt, prompt):
            return council.AgentError("silent", "no answer file after 20 min")
        r = self.go(ornith(), qwen(review))
        self.assertEqual(r["status"], "aborted")
        self.assertIn("Qwen", r["reason"])
        self.assertIn("silent", r["reason"])
        self.assertTrue((self.out / "result.json").exists())
        self.assertTrue((self.out / "r01-independent-Ornith.reply.md").exists())


class FormatTests(Base):
    def test_one_malformed_reply_is_sent_back_and_the_run_continues(self):
        def review(rnd, attempt, prompt):
            return "looks good to me" if attempt == 1 else "RE D1: ACCEPT"
        r = self.go(ornith(), qwen(review))
        self.assertEqual(r["status"], "consensus")
        self.assertEqual(self.q.count("review"), 2)
        self.assertIn("YOUR PREVIOUS REPLY WAS REJECTED", self.q.prompts[-1])
        self.assertIn("no `RE D1:", self.q.prompts[-1])

    def test_repeated_malformed_replies_abort(self):
        r = self.go(ornith(), qwen(lambda rnd, a, p: "looks good to me"))
        self.assertEqual(r["status"], "aborted")
        self.assertIn("format", r["reason"])

    def test_the_draft_must_name_its_sources(self):
        o = ornith()
        def no_from(phase, rnd, attempt, prompt):
            return blk("D1") if phase == "merge" else o(phase, rnd, attempt, prompt)
        r = self.go(no_from, qwen(ACCEPT_ALL))
        self.assertEqual(r["status"], "aborted")
        self.assertIn("from:", r["reason"])

    def test_an_objection_without_a_reason_is_rejected(self):
        r = self.go(ornith(), qwen(lambda rnd, a, p: "RE D1: OBJECT"))
        self.assertEqual(r["status"], "aborted")
        self.assertIn("needs a reason", r["reason"])


class PathTests(Base):
    def test_citations_to_nowhere_are_sent_back_with_the_roots(self):
        wrong = "".join(blk(f"A{n}", "verified", f'nope/x{n}.c:1 "x"') for n in (1, 2, 3))
        seen = []
        good = ornith()
        def responder(ph, rnd, att, p):
            if ph == "independent":
                seen.append(att)
                return wrong if att == 1 else blk("A1")
            return good(ph, rnd, att, p)
        r = self.go(responder, qwen(ACCEPT_ALL))
        self.assertEqual(seen, [1, 2])
        self.assertIn(str(self.tmp), self.o.prompts[1])  # the retry names the roots
        self.assertEqual(r["status"], "consensus")


class CapitulationTests(Base):
    def test_switching_to_accept_without_new_evidence_is_flagged_and_not_counted(self):
        def review(rnd, attempt, prompt):
            if rnd == 3:
                return "RE D1: OBJECT because I do not believe it."
            if rnd == 4:
                return "RE D1: ACCEPT"  # capitulation: same item, no evidence
            return f'RE D1: ACCEPT because the rebuttal holds. evidence: {GOOD}'
        r = self.go(ornith(), qwen(review))
        self.assertEqual((r["status"], r["rounds"]), ("consensus", 5))  # round 4 did not count
        self.assertEqual([(f["round"], f["agent"], f["kind"]) for f in r["flags"]], [(4, "Qwen", "flip-without-new-evidence")])

    def test_a_reviewer_who_only_ever_capitulates_never_reaches_consensus(self):
        r = self.go(ornith(), qwen(lambda rnd, a, p: "RE D1: OBJECT because no." if rnd == 3 else "RE D1: ACCEPT"), max_rounds=7)
        self.assertEqual(r["status"], "no-consensus")
        self.assertTrue(r["flags"])
        self.assertTrue(r["open"][0]["flag"])
        self.assertIn("agreement without new evidence", (self.out / "report.md").read_text())

    def test_missing_item_objection_blocks_until_the_draft_gains_an_item(self):
        def review(rnd, attempt, prompt):
            ids = re.findall(r"(?m)^ITEM (\S+)", prompt)
            lines = [f"RE {i}: ACCEPT" for i in ids]
            return "\n".join(lines + (["RE MISSING: OBJECT because there is no rollback step."] if rnd == 3 else []))
        def revise(rnd, prompt):
            return blk("D1", frm="Ornith/A1") + blk("D2", "inferred", "measurement: none", stmt="roll back by reflashing", frm="new")
        r = self.go(ornith(revise=revise), qwen(review))
        self.assertEqual(r["status"], "consensus-unverified")  # D2 is inferred
        self.assertEqual(r["rounds"], 4)

    def test_missing_item_objection_stays_open_if_the_draft_does_not_change(self):
        def review(rnd, attempt, prompt):
            return "RE D1: ACCEPT" + ("\nRE MISSING: OBJECT because there is no rollback step." if rnd == 3 else "")
        r = self.go(ornith(), qwen(review), max_rounds=6)
        self.assertEqual(r["status"], "no-consensus")
        self.assertIn("rollback", r["missing_open"][0]["text"])


class ProtocolTests(Base):
    def test_round_one_is_independent_and_the_merge_sees_every_candidate(self):
        o = ornith(indep_text=blk("A1", "inferred", "measurement: x", stmt="ORNITH-UNIQUE-STATEMENT"))
        r = self.go(o, qwen(ACCEPT_ALL))
        self.assertEqual(r["status"], "consensus-unverified" if r["items"][0]["basis_final"] != "verified" else "consensus")
        qwen_first = [p for p in self.q.prompts if "phase=independent" in p][0]
        self.assertNotIn("ORNITH-UNIQUE-STATEMENT", qwen_first)
        merge = [p for p in self.o.prompts if "phase=merge" in p][0]
        self.assertIn("ORNITH-UNIQUE-STATEMENT", merge)
        self.assertIn("candidate from Qwen", merge)

    def test_the_review_prompt_carries_rebuttals_and_open_objections(self):
        self.go(ornith(), qwen(lambda rnd, a, p: "RE D1: OBJECT because no. evidence: measurement: m"), max_rounds=5)
        later = [p for p in self.q.prompts if "phase=review round=4" in p][0]
        self.assertIn("Integrator rebuttals", later)
        self.assertIn("quote is on the cited line", later)
        self.assertIn("still open", later)

    def test_three_participants_every_reviewer_must_accept(self):
        gemma_calls = []
        def gemma(phase, rnd, attempt, prompt):
            if phase == "independent":
                return blk("C1", "inferred", "measurement: x")
            gemma_calls.append(rnd)
            return "RE D1: OBJECT because unclear. evidence: measurement: y" if rnd == 3 else "RE D1: ACCEPT"
        def revise(rnd, prompt):
            return blk("D1", stmt="m starts at 3, clarified", frm="Ornith/A1")
        o, q, g = Scripted("Ornith", ornith(revise=revise)), Scripted("Qwen", qwen(ACCEPT_ALL)), Scripted("Gemma", gemma)
        b = self.brief(participants=["Ornith", "Qwen", "Gemma"])
        r = council.run_council(b, {"Ornith": o, "Qwen": q, "Gemma": g}, self.out)
        self.assertEqual((r["status"], r["rounds"]), ("consensus", 4))
        self.assertEqual(gemma_calls, [3, 4])
        self.assertEqual(q.count("review"), 2)

    def test_artifacts_are_written(self):
        self.go(ornith(), qwen(ACCEPT_ALL))
        for name in ("brief.md", "ledger.md", "report.md", "result.json", "r03-review-Qwen.prompt.md", "r03-review-Qwen.reply.md", "r02-merge-Ornith.reply.md"):
            self.assertTrue((self.out / name).exists(), name)
        self.assertEqual(json.loads((self.out / "result.json").read_text())["status"], "consensus")

    def test_missing_agent_is_a_usage_error(self):
        with self.assertRaises(ValueError):
            council.run_council(self.brief(), {"Ornith": Scripted("Ornith", ornith())}, self.out)


class BriefTests(unittest.TestCase):
    def test_load_brief(self):
        b = council.load_brief("type: analysis\nparticipants: Ornith, Qwen\nroots: /a, /b\nmax_rounds: 8\ntime_limit_minutes: 90\n\nHow does X work?")
        self.assertEqual((b.type, b.participants, b.roots, b.max_rounds, b.time_limit_s, b.task),
                         ("analysis", ["Ornith", "Qwen"], ["/a", "/b"], 8, 5400, "How does X work?"))

    def test_defaults_are_ten_rounds_and_two_hours(self):
        b = council.load_brief("type: plan\nparticipants: A, B\n\ntask")
        self.assertEqual((b.max_rounds, b.time_limit_s), (5, 7200))

    def test_bad_briefs(self):
        for text in ("type: poem\nparticipants: A, B\n\ntask", "type: plan\nparticipants: A\n\ntask", "type: plan\nparticipants: A, B\n\n"):
            with self.assertRaises(ValueError):
                council.load_brief(text)


if __name__ == "__main__":
    unittest.main()
