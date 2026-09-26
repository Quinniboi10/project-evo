import math

def abbreviate_score(score: float) -> str:
    leading_digits = math.floor(math.log10(score)) + 1
    # For scores less than 1, switch to scientific notation
    if leading_digits < 1:
        return f"{score:.3e}"
    leading_chars = (leading_digits - 1) % 3 + 1
    prec = 4 - leading_chars
    for dig, suffix in ((12, "T"), (9, "B"), (6, "M"), (3, "K")):
        if leading_digits > dig:
            return f"{score/10**dig:.{prec}f}{suffix}"
    return f"{score:.{prec}f}"
