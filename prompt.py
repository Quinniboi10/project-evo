from database import PrimaryTableRow
import globals

def build_prompt(parent: PrimaryTableRow, child: PrimaryTableRow, try_something_new: bool):
    return f"""Improve the existing code for {parent.name} in the git branch {globals.BRANCH_BASE}/{child.uuid}. {"Try to make significant variation/changes on some significant portion of the existing changes" if try_something_new else "Don't make any drastic changes, just try to improve on the concepts already present."}
               The user stated your objective: '{globals.OBJECTIVE}'. Unless the user explicitly tells you to do so, you should not change user interfacing (CLI args, readings printed, etc), as they may be used for performance evaluation.
               After you finish working, you MUST write name.txt, containing a name for your attempt."""