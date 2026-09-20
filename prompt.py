from database import PrimaryTableRow
import config

def build_prompt(parent: PrimaryTableRow, child: PrimaryTableRow, try_something_new: bool):
    return f"""Improve the existing code for {parent.name} in the git branch {config.cfg.branch_base}/{child.uuid}. {"Try to make significant variation/changes on some significant portion of the existing changes" if try_something_new else "Don't make any drastic changes, just try to improve on the concepts already present."}
The user stated your objective: '{config.cfg.objective}'. Unless the user explicitly tells you to do so, you should not change user interfacing (CLI args, readings printed, etc), as they may be used for performance evaluation. Commit your changes as you work.
After you finish working, you MUST write name.txt, containing a name for your attempt."""

def build_run_fix_prompt(parent: PrimaryTableRow, child: PrimaryTableRow):
    parent_branch = f"{config.cfg.branch_base}/{parent.uuid}"
    child_branch = f"{config.cfg.branch_base}/{child.uuid}"

    return f"""The implementation on the current branch ({child_branch}) failed the tests.
Your objective is to diagnose the failure and fix the current implementation while preserving the intended improvements for the user's objective:
'{config.cfg.objective}'

The parent branch is available at:
    {parent_branch}

Use the parent branch as a reference for understanding what changed in this attempt. Inspect the diff between the parent branch and the current workspace/branch to identify the changes introduced by this attempt and determine which of them caused the test failure.

Important:
- Fix the current branch; do not switch to or modify the parent branch.
- Do not simply revert the current branch back to the parent implementation.
- Preserve useful improvements from this attempt whenever possible.
- Make the smallest appropriate change that fixes the underlying problem.
- Do not change user-facing interfaces or behavior (CLI arguments, printed output, file formats, APIs, etc.) unless the user's objective explicitly requires it or the existing changes necessarily require it.
- Check related code for consistency if the failure indicates the bug is broader than the immediately failing line.
- A name for your attempt MUST be stored into name.txt if not already present.

Treat the parent branch as a debugging reference and baseline, not as the desired final solution. Commit your changes as you work. The tests will be re-run when you're done working."""