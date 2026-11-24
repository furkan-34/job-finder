"""Render profile.md from profile.json, deterministically and without contact info.

profile.md is what scoring prompts see, so it starts with what the candidate
wants and never includes name, email, phone or profile links.
"""


def render_profile_md(profile: dict) -> str:
    lines: list[str] = ["# Candidate profile", ""]
    _preferences(lines, profile["preferences"])
    _summary(lines, profile)
    _experience(lines, profile["experience"])
    _education(lines, profile["education"])
    _skills(lines, profile["skills"])
    _extras(lines, profile.get("extras") or {})
    return "\n".join(lines).rstrip() + "\n"


def _join(values) -> str:
    return ", ".join(values) if values else "none"


def _preferences(lines: list[str], p: dict) -> None:
    lines += ["## What I am looking for", ""]
    lines.append(f"- Target roles: {_join(p['target_roles'])}")
    lines.append(f"- Seniority: {_join(p['seniority'])}")
    places = [f"{loc['place']} ({'/'.join(loc['work_modes'])})" for loc in p["locations"]]
    lines.append(f"- Locations: {_join(places)}")
    lines.append(f"- Employment type: {_join(p['employment_types'])}")
    if salary := p.get("min_salary"):
        gross = "gross" if salary.get("gross", True) else "net"
        lines.append(f"- Minimum salary: {salary['amount']:,} {salary['currency']} per {salary['period']} ({gross}); "
                     "a soft preference, not a hard filter")
    if p.get("open_to_relocation") is not None:
        lines.append(f"- Open to relocation: {'yes' if p['open_to_relocation'] else 'no'}")
    if availability := p.get("availability"):
        if "notice_period_weeks" in availability:
            lines.append(f"- Notice period: {availability['notice_period_weeks']} weeks")
        if "earliest_start" in availability:
            lines.append(f"- Earliest start: {availability['earliest_start']}")
    for key, title in [("must_haves", "Must-haves"), ("nice_to_haves", "Nice-to-haves"),
                       ("company_sizes", "Preferred company sizes"),
                       ("preferred_industries", "Preferred industries")]:
        if p.get(key):
            lines.append(f"- {title}: {_join(p[key])}")
    d = p["dealbreakers"]
    lines += ["", "### Dealbreakers", "",
              f"- Industries: {_join(d['industries'])}",
              f"- Companies: {_join(d['companies'])}",
              f"- Keywords: {_join(d['keywords'])}", ""]


def _summary(lines: list[str], profile: dict) -> None:
    s, loc, auth = profile["summary"], profile["identity"]["location"], profile["work_authorization"]
    lines += ["## Summary", "",
              f"- Headline: {s['headline']}",
              f"- Total experience: {s['total_years_experience']:g} years",
              f"- Based in: {loc['city']}, {loc['country']}",
              f"- Can work without sponsorship in: {_join(auth['countries_without_sponsorship'])}",
              f"- Needs sponsorship elsewhere: {'yes' if auth['needs_sponsorship_elsewhere'] else 'no'}"]
    if s.get("about"):
        lines += ["", s["about"]]
    lines.append("")


def _experience(lines: list[str], experience: list[dict]) -> None:
    lines += ["## Experience", ""]
    for job in experience:
        lines.append(f"### {job['title']} at {job['company']}")
        lines.append(f"{job['start']} – {job['end'] or 'present'} · {job['location']} · {job['work_mode']}")
        if job["technologies"]:
            lines.append(f"Technologies: {_join(job['technologies'])}")
        lines.append("")
        lines += [f"- {a}" for a in job["achievements"]]
        lines.append("")


def _education(lines: list[str], education: list[dict]) -> None:
    lines += ["## Education", ""]
    for e in education:
        years = f"{e['start_year']}–{e['end_year'] or 'present'}"
        gpa = f", GPA {e['gpa']}" if e.get("gpa") else ""
        lines.append(f"- {e['degree']} {e['field']}, {e['school']} ({years}{gpa})")
    lines.append("")


def _skills(lines: list[str], skills: dict) -> None:
    lines += ["## Skills", ""]
    by_level: dict[str, list[str]] = {}
    for skill in skills["technical"]:
        by_level.setdefault(skill["level"], []).append(skill["name"])
    for level in ("expert", "advanced", "intermediate"):
        if level in by_level:
            lines.append(f"- {level.capitalize()}: {_join(by_level[level])}")
    langs = [f"{lang['language']} ({lang['level']})" for lang in skills["languages"]]
    lines += [f"- Languages: {_join(langs)}", ""]


def _extras(lines: list[str], extras: dict) -> None:
    if certs := extras.get("certifications"):
        lines += ["## Certifications", ""]
        for cert in certs:
            details = ", ".join(str(x) for x in (cert.get("issuer"), cert.get("year")) if x)
            lines.append(f"- {cert['name']}" + (f" ({details})" if details else ""))
        lines.append("")
    if projects := extras.get("projects"):
        lines += ["## Projects", ""]
        for proj in projects:
            tech = f" ({_join(proj['technologies'])})" if proj.get("technologies") else ""
            lines.append(f"- {proj['name']}{tech}: {proj['description']}")
        lines.append("")
    if pubs := extras.get("publications"):
        lines += ["## Publications", ""]
        lines += [f"- {p['title']}" + (f", {p['venue']}" if p.get("venue") else "")
                  + (f" ({p['year']})" if p.get("year") else "") for p in pubs]
        lines.append("")
