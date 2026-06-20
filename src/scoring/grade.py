from __future__ import annotations


def grade_label(score: int) -> str:
    if score >= 85:
        return "S"
    if score >= 75:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def action_from_grade(grade: str, risk_penalty: int) -> str:
    if grade == "S" and risk_penalty < 5:
        return "Strong Buy Candidate"
    if grade in ("S", "A") and risk_penalty < 7:
        return "Watch / Buy Pullback"
    if grade == "B":
        return "Monitor"
    return "Avoid"
