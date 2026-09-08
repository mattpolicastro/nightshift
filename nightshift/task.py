"""One task's state machine.

The decisions live in pure functions at the top of this module so the branches
that are hard to reach in real life — a truncated run, a reviewer that never
rendered a verdict, a second FAIL after a retry — can be tested directly
instead of hoped about. `run()` at the bottom does the IO and defers every
judgement to them.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import mirror as mirror_mod
from . import outcomes, prompts, queue, trace, vcs, verification, worker
from .config import Config, Repo
from .queue import Issue, Phase

log = logging.getLogger("nightshift")

ESCALATION_FILE = "NIGHTSHIFT-ESCALATION.md"


class Step(Enum):
    SHIP = "ship"  # push, open PR, label done
    ESCALATE = "escalate"  # comment + needs-human, agent moves on
    RETRY_REVIEW = "retry_review"  # re-implement with the reviewer's feedback


@dataclass(frozen=True)
class Decision:
    step: Step
    reason: str


@dataclass
class Attempt:
    """What one implement+review cycle produced."""

    implement: trace.Result | None
    review: trace.Result | None = None
    verdict: bool | None = None
    feedback: str = ""
    verification: verification.VerificationResult | None = None


def after_implement(
    run: trace.Result | None,
    *,
    escalated: bool,
    committed: bool,
    verified: bool,
) -> Decision | None:
    """None means 'carry on to review'. Anything else ends the task now."""
    if run is None:
        return Decision(Step.ESCALATE, "worker produced no result event")

    # A run that hit --max-turns stopped mid-thought. Whatever is on the branch
    # is an artefact of where it was cut off, not a finished piece of work.
    if run.truncated:
        return Decision(Step.ESCALATE, f"worker truncated at {run.turns} turns")

    if not run.ok:
        return Decision(Step.ESCALATE, "implementation worker failed")

    # Escalation is a correct outcome, not a failure — check it before anything
    # that looks like a shortfall, or a deliberate stop reads as a broken run.
    if escalated:
        return Decision(Step.ESCALATE, "worker escalated")

    # Reaching here means the run ended cleanly, on its own terms, having
    # written nothing and filed no findings. The observed cause is a worker
    # backgrounding its verify and yielding to be resumed (issue #31: subtype
    # success, 45 turns, "I'll pause here and pick back up once the background
    # test monitor reports results") — which never comes, because `-p` ends
    # when the turn ends. Say that, rather than "committed nothing": the old
    # wording read as a deliberate refusal, which points at the issue when the
    # fault is the run.
    if not committed:
        return Decision(
            Step.ESCALATE,
            f"worker ended its turn after {run.turns} turns without committing "
            "or filing findings — check whether it backgrounded work and yielded",
        )

    # AGENTS.md makes this an explicit failure so the incentive is never to
    # commit and hope. "I could not verify" is a valid outcome; silence is not.
    if not verified:
        return Decision(Step.ESCALATE, "no successful host verification for the candidate")

    return None


def after_review(
    run: trace.Result | None,
    verdict: bool | None,
    *,
    attempt: int,
    max_attempts: int,
) -> Decision:
    if run is None:
        return Decision(Step.ESCALATE, "reviewer produced no result event")

    # A reviewer cut off mid-analysis renders no verdict. Absence of a FAIL is
    # not a PASS — treating it as one silently disables the gate.
    if verdict is None:
        return Decision(
            Step.ESCALATE,
            "reviewer rendered no VERDICT line"
            + (" (truncated)" if run.truncated else ""),
        )

    if not run.ok or run.truncated:
        return Decision(Step.ESCALATE, "reviewer did not complete successfully")

    if verdict:
        return Decision(Step.SHIP, "reviewer passed")

    if attempt < max_attempts:
        return Decision(Step.RETRY_REVIEW, f"reviewer failed, attempt {attempt}")

    # Two agents disagreeing twice is a human's problem, not a budget to keep
    # spending.
    return Decision(Step.ESCALATE, f"reviewer failed {attempt} attempts")


def ran_verification(run: trace.Result, verify: str) -> bool:
    """Diagnostic only: did command requests contain the complete exact chain?

    Requested text cannot prove completion, exit status, or candidate identity.
    Shipping exclusively uses the host-owned VerificationResult below.
    """
    try:
        expected = verification.parse_commands(verify)
        requested = []
        for command in run.commands:
            try:
                requested.extend(verification.parse_commands(command))
            except ValueError:
                continue
        return all(clause in requested for clause in expected)
    except ValueError:
        return False



#: Written by the harness, not by an agent: the worker never sees this failure
#: — it happens after review, in the push the harness performs. Read by
#: `_escalation_text` when there is no agent-written escalation file.
_WORKFLOW_ESCALATION = """\
This task's diff touches `.github/workflows/`, and the remote refused the push:

```
{detail}
```

**This is the design working, not a fault, and the fix is not to grant the
scope.** Withholding Workflows from the daemon's token is what makes CI a
genuinely held-out verifier — an agent physically cannot edit the thing that
judges its own work, so the guarantee rests on the credential rather than on
the agent choosing to respect it.

**A human has to make this change.** The work is not lost, but it is only
LOCAL: the push is the step that failed, so `{branch}` exists in the daemon's
clone of this repo and nowhere else. The worktree is torn down; the branch ref
is kept deliberately. Push it yourself, or lift the workflow change out and
make that part by hand.
"""


@dataclass
class Report:
    issue: Issue
    step: Step
    reason: str
    attempts: list[Attempt] = field(default_factory=list)
    pr_url: str = ""
    #: Findings written by the HARNESS rather than by an agent, for the cases
    #: that never reach one. `_escalation_text` prefers the agent's own
    #: `NIGHTSHIFT-ESCALATION.md`; this is what it falls back to before
    #: "no findings were produced", which is true but useless to read.
    escalation: str = ""

    @property
    def cost(self) -> float:
        total = 0.0
        for a in self.attempts:
            total += a.implement.cost_usd if a.implement else 0.0
            total += a.review.cost_usd if a.review else 0.0
        return total

    @property
    def subscription_billed(self) -> bool:
        """Did any phase of this task consume subscription quota?

        The cap counts subscription-billed work, so a task run entirely on a
        local endpoint must not eat a slot in a window it never touched. A task
        with no runs at all — one that crashed before invoking anything — is
        billed by default: over-counting against the cap is the safe direction.
        """
        runs = [r for a in self.attempts for r in (a.implement, a.review) if r]
        if not runs:
            return True
        return any(r.subscription_billed for r in runs)

    @property
    def turns(self) -> int:
        total = 0
        for a in self.attempts:
            total += a.implement.turns if a.implement else 0
            total += a.review.turns if a.review else 0
        return total


def run(
    cfg: Config,
    repo: Repo,
    repo_dir: Path,
    issue: Issue,
    claim: queue.Claim,
    *,
    transcript_dir: Path | None = None,
) -> Report:
    """Implement → review → ship, with one retry on a reviewer FAIL."""
    worktree = Path(claim.worktree)
    report = Report(issue=issue, step=Step.ESCALATE, reason="not started")
    feedback = ""

    # Resolved once per task rather than per attempt: which endpoint serves a
    # phase is static configuration, and a retry must not land somewhere else
    # than the attempt it is revising.
    implementing = cfg.assign("implement", repo)
    reviewing = cfg.assign("review", repo)
    if not implementing.endpoint.is_default or not reviewing.endpoint.is_default:
        log.info(
            "#%s implement=%s:%s review=%s:%s",
            issue.number,
            implementing.endpoint.name, implementing.model,
            reviewing.endpoint.name, reviewing.model,
        )

    # Read once per task, not per attempt: the comments cannot change while a
    # task is running, and a retry re-uses the same context.
    discussion = queue.comments_of(issue.repo, issue.number)

    # Before anything reads the base: the daemon never checks `main` out, so
    # this clone's local ref does not move when Matt merges a PR.
    vcs.fetch(repo_dir)

    # An issue may thread itself onto a shared theme branch with `base: <name>`
    # instead of the repo default (queue.base_branch). Resolved AFTER the fetch,
    # because the check below asks whether the remote has it.
    base = queue.base_branch(issue.body) or repo.base
    base_ref = f"origin/{base}"
    if base != repo.base and not vcs.remote_ref_exists(repo_dir, base_ref):
        # Refuse rather than fall back to `main`. A typo'd theme silently
        # branching from the trunk is the failure that looks like success: the
        # work lands somewhere nobody is merging from, and the first sign is a
        # conflict weeks later. Escalating costs one comment.
        return Report(
            issue=issue,
            step=Step.ESCALATE,
            reason=(
                f"issue declares `base: {base}` but {base_ref} does not exist on the "
                f"remote — create the theme branch and push it first, or drop the marker"
            ),
        )
    if claim.revise:
        # Continue the shipped branch. `add_worktree` would cut a new one from
        # base and rename this aside, which is the correct default for new work
        # and precisely wrong here — the diff being revised is the input.
        vcs.resume_worktree(
            repo_dir, worktree, claim.branch, allowed_root=cfg.worktree_root
        )
        log.info("#%s: revising %s in place", issue.number, claim.branch)
    else:
        kept = vcs.add_worktree(
            repo_dir,
            worktree,
            claim.branch,
            base_ref,
            allowed_root=cfg.worktree_root,
        )
        if kept:
            # A previous attempt's committed work was moved aside rather than
            # reused or deleted. Say so: the branch is otherwise invisible, and
            # it is usually an escalation someone is about to go looking for.
            log.info("#%s: kept the previous attempt's branch as %s", issue.number, kept)

    # What the human asked for, on the PR and on the issue, since the branch
    # was last committed to. Anything older is what the branch was already
    # built from.
    review_notes: list[str] = []
    if claim.revise:
        since = vcs.branch_tip_time(worktree)
        review_notes = queue.pr_feedback(issue.repo, claim.branch, since)
        fresh_issue_comments = queue.comments_of(issue.repo, issue.number, since)
        if not queue.has_instructions(review_notes + fresh_issue_comments):
            # The trap this path creates: the natural gesture is to comment on
            # the PR while closing it, and if that is the only place the words
            # went and nothing read them, the worker sees the original issue,
            # no revision request, and no way to tell "nothing was asked" from
            # "the issue body is the ask" — so it redoes the work and looks
            # like it succeeded. Refusing is the only honest answer.
            #
            # The worktree is left for the daemon to tear down, like every
            # other escalation — it is the only caller that knows whether the
            # branch is worth keeping (`daemon._branch_to_delete`).
            return Report(
                issue=issue,
                step=Step.ESCALATE,
                reason="revise requested with no instructions",
                escalation=(
                    f"`{claim.branch}` was labelled for revision, but there is "
                    "nothing on it or on this issue asking for a change — no "
                    "comment, review or inline note newer than the branch's "
                    f"last commit ({since or 'unknown'}).\n\n"
                    "Nothing was done, because the alternatives are worse: "
                    "re-reading the original description would reproduce work "
                    "that already shipped, and guessing at what you meant "
                    "would be worse than that.\n\n"
                    "Say what should change — on the PR (conversation, a "
                    "review, or an inline comment on the line) or on this "
                    "issue — and re-apply the label."
                ),
            )
    try:
        for attempt_no in range(1, cfg.review_attempts + 1):
            vcs.install(worktree, "pnpm install --frozen-lockfile")

            issue_text = f"# Issue #{issue.number}: {issue.title}\n\n{issue.body}"
            if discussion:
                # Later than the body, so it wins where the two disagree. This
                # is also where a previous run's escalation findings live.
                issue_text += (
                    "\n\n---\n\n## Discussion on this issue\n\n"
                    "Posted after the description above, so it may amend or "
                    "override it — a later comment that widens scope or "
                    "answers an open question is authoritative. If an earlier "
                    "run escalated, its findings are here: read them before "
                    "redoing that analysis.\n\n" + "\n\n---\n\n".join(discussion)
                )
            if review_notes:
                # Kept separate from the issue discussion above on purpose:
                # these are about THIS DIFF, not about the task. Concatenating
                # them would leave the worker unable to tell "the spec changed"
                # from "line 40 is wrong", which want different responses.
                issue_text += (
                    "\n\n---\n\n## Review feedback on the PR you are revising\n\n"
                    "This is what you were asked to change, and it is the "
                    "authority for this run — the issue above describes the "
                    "ORIGINAL task and has not been rewritten. Comments "
                    "anchored to a `file:line` refer to that line in the diff "
                    "already on this branch.\n\n" + "\n\n---\n\n".join(review_notes)
                )
            if feedback:
                issue_text += (
                    "\n\n---\n\n## A reviewer rejected your previous attempt\n\n"
                    "Address every BLOCKING concern below. Do not weaken a test "
                    "to satisfy one — if a concern cannot be addressed without "
                    "doing so, escalate instead.\n\n" + feedback
                )

            claim.advance(Phase.IMPLEMENTING)
            impl_transcript = _transcript_path(
                transcript_dir, repo.name, f"{issue.number}-impl-{attempt_no}"
            )
            outcomes.record(repo.name, issue.number, attempt=attempt_no,
                            phase="implement", attempt_result="", verdict=None,
                            implement_transcript=str(impl_transcript) if impl_transcript else None,
                            review_transcript=None)
            impl = worker.implement(
                worktree,
                prompts.revise(issue_text, claim.branch, base_ref, repo.verify)
                if claim.revise
                else prompts.implement(issue_text, claim.branch, repo.verify),
                model=implementing.model,
                # Scaled by the endpoint: a local model is slower per turn, so
                # a budget tuned for sonnet truncates on it — which spends the
                # whole run and yields nothing to merge or even to read.
                max_turns=implementing.max_turns(cfg.implement_max_turns),
                endpoint=implementing.endpoint,
                context_tokens=implementing.context_tokens,
                foreign_auth_envs=cfg.foreign_auth_envs(implementing.endpoint),
                transcript=impl_transcript,
            )
            impl_result = worker.parse_result(impl)
            attempt = Attempt(implement=impl_result)
            report.attempts.append(attempt)

            escalated = (worktree / ESCALATION_FILE).exists()
            committed = vcs.has_commits(worktree, base_ref)

            early = after_implement(
                impl_result,
                escalated=escalated,
                committed=committed,
                verified=True,
            )
            if early:
                outcomes.record(repo.name, issue.number, reason=early.reason,
                                attempt_result=early.step.value)
                report.step, report.reason = early.step, early.reason
                return report

            attempt.verification = verification.run(worktree, repo.verify)
            if impl_transcript:
                verification.save(attempt.verification,
                                  impl_transcript.with_suffix(".verification.json"))
            if not attempt.verification.ok:
                report.step = Step.ESCALATE
                report.reason = ("host verification failed: "
                                 + attempt.verification.error)
                outcomes.record(repo.name, issue.number, reason=report.reason,
                                attempt_result=report.step.value)
                return report

            claim.advance(Phase.REVIEWING)
            rev_transcript = _transcript_path(
                transcript_dir, repo.name, f"{issue.number}-review-{attempt_no}"
            )
            outcomes.record(repo.name, issue.number, phase="review",
                            review_transcript=str(rev_transcript) if rev_transcript else None)
            rev = worker.review(
                worktree,
                prompts.review(issue_text, claim.branch, base_ref, repo.verify)
                + "\n\nHost verification passed all configured clauses at candidate "
                + attempt.verification.candidate_sha + ".",
                model=reviewing.model,
                max_turns=reviewing.max_turns(cfg.review_max_turns),
                endpoint=reviewing.endpoint,
                context_tokens=reviewing.context_tokens,
                foreign_auth_envs=cfg.foreign_auth_envs(reviewing.endpoint),
                transcript=rev_transcript,
            )
            rev_result = worker.parse_result(rev)
            attempt.review = rev_result
            attempt.verdict = trace.verdict(rev_result.text) if rev_result else None

            decision = after_review(
                rev_result,
                attempt.verdict,
                attempt=attempt_no,
                max_attempts=cfg.review_attempts,
            )
            outcomes.record(repo.name, issue.number, reason=decision.reason,
                            attempt_result=decision.step.value, verdict=attempt.verdict)
            report.step, report.reason = decision.step, decision.reason

            if decision.step is Step.RETRY_REVIEW:
                attempt.feedback = rev_result.text if rev_result else ""
                feedback = attempt.feedback
                continue

            if decision.step is Step.SHIP:
                # Inspection failures must preserve the unpushed worktree. SHIP
                # activates teardown in finally, so don't set it until the gate
                # has actually completed successfully.
                report.step = Step.ESCALATE
                try:
                    candidate_unchanged = verification.unchanged(
                        worktree, attempt.verification.candidate_sha, branch=claim.branch
                    )
                except (OSError, subprocess.SubprocessError):
                    report.reason = "unable to inspect candidate after verification or during review"
                    outcomes.record(repo.name, issue.number, reason=report.reason,
                                    attempt_result=report.step.value)
                    return report
                if not candidate_unchanged:
                    report.step = Step.ESCALATE
                    report.reason = "candidate changed after verification or during review"
                    outcomes.record(repo.name, issue.number, reason=report.reason,
                                    attempt_result=report.step.value)
                    return report
                report.step = Step.SHIP
                claim.advance(Phase.SHIPPING)
                try:
                    vcs.push(worktree, claim.branch, base)
                except vcs.WorkflowScopeRefusal as refusal:
                    # Escalate rather than let this reach the daemon's crash
                    # handler, which releases the issue and deletes the branch
                    # — making the work claimable again, so it fails at the same
                    # push and spends another full budget. Twice in one day on
                    # 2026-08-08 (swift-app #14), which is what that looks like.
                    report.step = Step.ESCALATE
                    report.reason = (
                        "this task changes a GitHub Actions workflow, which the "
                        "daemon is deliberately unable to push"
                    )
                    report.escalation = _WORKFLOW_ESCALATION.format(
                        branch=claim.branch, detail=str(refusal)
                    )
                    return report
                # A revise whose PR is still open needs no second one — the
                # push already updated it. Asked with state=open on purpose: a
                # CLOSED PR (the normal revise flow, where the human closes it
                # before re-labelling) must not suppress opening a fresh one.
                existing = (
                    vcs.pr_for_branch(repo.name, claim.branch, state="open")
                    if claim.revise
                    else None
                )
                report.pr_url = existing or vcs.open_pr(
                    worktree,
                    repo.name,
                    claim.branch,
                    base,
                    issue.title,
                    _pr_body(issue, report),
                )
                # Persist the link before teardown, which can itself fail.
                outcomes.record(repo.name, issue.number, state="awaiting_merge",
                                pr_url=report.pr_url, reason=report.reason)
            return report
    finally:
        # Ship tears down here because the branch is already pushed. Every
        # other outcome is left alone ON PURPOSE — the daemon owns that
        # teardown, because only it knows whether the branch is worth keeping
        # (`daemon._branch_to_delete`). This comment used to claim the
        # retention happened here; it did not, and sample #2's escalated diff
        # was deleted as a result.
        if report.step is Step.SHIP:
            vcs.remove_worktree(repo_dir, worktree, allowed_root=cfg.worktree_root)

    return report


def _mirror(cfg: Config, transcript: Path | None, repo: str, issue: int,
            phase: str) -> None:
    """Put a finished phase on the phone. Never allowed to affect the task.

    Runs while the worktree still exists — `paseo import` requires it, and the
    daemon removes it on teardown — and after the phase is complete, because
    import snapshots rather than tails.
    """
    if not cfg.mirror_to_paseo:
        return
    try:
        note = mirror_mod.mirror(
            transcript, repo=repo, issue=issue, phase=phase
        )
    except Exception as exc:  # noqa: BLE001 — a display feature, never a failure
        note = f"mirror crashed: {exc}"
    if note:
        log.info("#%s %s", issue, note)


def _transcript_path(directory: Path | None, repo: str, name: str) -> Path | None:
    """Where a phase streams its transcript, or None to stream nowhere.

    Handed to the worker BEFORE the run rather than written after it: the
    events used to be held in memory until the phase returned, so a forty
    minute implement pass was invisible while it ran and a worker killed
    mid-run took its whole transcript with it.

    The REPO is in the name, by the same `owner__repo#N` convention
    `queue.claim_path` uses. Without it the filename was `<issue>-impl-1`, and
    issue numbers are per repo: second-project's #11 and sample's #11 both
    resolved to `11-impl-1.jsonl`, so one silently overwrote the other. Both
    issues exist today, and sample's transcripts for those numbers are already
    on disk.
    """
    if directory is None:
        return None
    return directory / f"{repo.replace('/', '__')}#{name}.jsonl"


def _pr_body(issue: Issue, report: Report) -> str:
    last = report.attempts[-1]
    lines: list[str] = []

    # The skim block goes above everything, including `Closes #N`. The full
    # review below it is the evidence; this is the part that decides whether
    # you open the evidence at all.
    if last.review:
        s = trace.skim(last.review.text)
        if s.summary:
            lines += [f"**{s.summary}**", ""]
        if s.needs_human:
            lines += ["**Needs your eyes:**", ""]
            lines += [f"- {item}" for item in s.needs_human]
            lines += [""]
        elif s.summary:
            lines += ["The reviewer flagged nothing that needs a human.", ""]
        if lines:
            lines += ["---", ""]

    lines += [f"Closes #{issue.number}.", ""]
    if last.review:
        lines += ["<details>", "<summary>Full review</summary>", ""]
        lines += [last.review.text, "", "</details>", ""]
    lines += [
        "## Run metadata",
        "",
        f"- attempts: {len(report.attempts)}",
        f"- turns: {report.turns}",
        f"- list-price equivalent: ${report.cost:.2f} "
        + ("(subscription quota, not metered billing)"
           if report.subscription_billed
           else "(no subscription quota consumed — this ran off-subscription)"),
        "",
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
    ]
    return "\n".join(lines)
