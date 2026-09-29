from .error import assert_eval, EvalError

from dataclasses import dataclass

import math

@dataclass
class EvaluationResult:
    passed: bool
    score: float
    feedback: str = ""

def normalize_result(value: object) -> EvaluationResult:
    if isinstance(value, EvaluationResult):
        result = value
    elif isinstance(value, tuple) and len(value) == 2:
        result = EvaluationResult(*value)
    else:
        raise EvalError("Evaluation must return EvaluationResult or (passed, score)")

    assert_eval(type(result.passed) is bool, "Evaluation passed must be a boolean")
    assert_eval(type(result.score) in (int, float), "Evaluation score must be numeric (not boolean)")
    assert_eval(isinstance(result.feedback, str), "Evaluation feedback must be a string")
    if result.passed:
        assert_eval(result.score > 0 and (isinstance(result.score, int) or math.isfinite(result.score)), "Evaluated scores must be greater than 0 and finite")
    return result

def format_feedback(feedback: str) -> str:
    if not feedback.strip():
        return ""
    if len(feedback) > 8000:
        return feedback[:4000] + "\n[Feedback truncated; middle omitted.]\n" + feedback[-4000:]
    return feedback
