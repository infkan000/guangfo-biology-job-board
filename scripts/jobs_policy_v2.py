"""Eligibility rules used only by the manual-review second version."""
import re


def contains_term(text, term):
    text, term = str(text or "").lower(), str(term or "").strip().lower()
    if not term:
        return False
    if term.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None
    return term in text


def eligible(job, config):
    title = str(job.get("title") or "")
    summary = str(job.get("summary") or "")
    city = str(job.get("city") or "")
    target = config.get("target", {})
    cities = target.get("cities", [])
    aliases = {
        "广州": ["广州", "Guangzhou", "Canton"],
        "佛山": ["佛山", "Foshan", "Fatshan"],
    }
    if not any(contains_term(city, alias) for c in cities for alias in aliases.get(c, [c])):
        return False
    foreign = job.get("is_foreign")
    if target.get("foreign_only") and not (
        foreign is True or (isinstance(foreign, str) and foreign.strip().lower() in {"true", "1", "yes", "y", "是"})
    ):
        return False
    keywords = config.get("keywords", [])
    # V2 fuzzy mode: any biology-related keyword in the title or JD is enough.
    # The role title and function are deliberately unrestricted.
    haystack = " ".join((title, summary))
    return any(contains_term(haystack, k) for k in keywords)

