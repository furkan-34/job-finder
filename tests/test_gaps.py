from jobfinder.profile.gaps import missing, set_pointer
from jobfinder.profile.schema import validate


def paths(gaps):
    return {g["path"]: g["level"] for g in gaps}


def test_complete_profile_has_no_gaps(example_profile):
    assert missing(example_profile) == []


def test_missing_fields_are_reported_by_level(example_profile):
    del example_profile["identity"]["phone"]
    del example_profile["preferences"]["min_salary"]
    del example_profile["preferences"]["office_days_per_week"]
    del example_profile["preferences"]["dealbreakers"]["confirmed"]
    del example_profile["experience"][0]["achievements"]
    del example_profile["extras"]  # optional: never asked
    del example_profile["identity"]["github"]  # optional

    assert paths(missing(example_profile)) == {
        "/identity/phone": "recommended",
        "/preferences/min_salary": "recommended",
        "/preferences/office_days_per_week": "recommended",
        "/preferences/dealbreakers/confirmed": "required",
        "/experience/0/achievements": "required",
    }


def test_skipped_recommended_field_is_not_missing(example_profile):
    example_profile["identity"]["phone"] = None
    assert missing(example_profile) == []
    assert validate(example_profile) == []


def test_office_days_are_0_to_5(example_profile):
    example_profile["preferences"]["office_days_per_week"] = 6
    assert validate(example_profile)
    example_profile["preferences"]["office_days_per_week"] = 0
    assert validate(example_profile) == []


def test_missing_section_lists_its_fields():
    gaps = paths(missing({"meta": {}}))
    assert gaps["/identity/full_name"] == "required"
    assert gaps["/identity/location/city"] == "required"
    assert gaps["/identity/phone"] == "recommended"
    assert gaps["/experience"] == "required"
    assert gaps["/preferences/dealbreakers/confirmed"] == "required"
    assert gaps["/preferences/availability"] == "recommended"  # nullable: asked as a whole
    assert not any(p.startswith("/extras") for p in gaps)


def test_gap_describes_expected_value():
    gaps = {g["path"]: g for g in missing({"meta": {}})}
    assert gaps["/identity/location/country"]["expects"] == "string matching ^[A-Z]{2}$"
    assert gaps["/preferences/seniority"]["expects"].startswith("list of (one of: intern")


def test_set_pointer_creates_parents_and_appends():
    doc = {"a/b": [{"x": 1}]}
    set_pointer(doc, "/a~1b/0/x", 2)
    set_pointer(doc, "/a~1b/-", {"x": 3})
    set_pointer(doc, "/new/deep", True)
    assert doc == {"a/b": [{"x": 2}, {"x": 3}], "new": {"deep": True}}


def test_set_pointer_appends_to_a_missing_list():
    doc = {}
    set_pointer(doc, "/extras/certifications/-", {"name": "CKA"})
    set_pointer(doc, "/extras/certifications/-", {"name": "AWS SAA"})
    assert doc == {"extras": {"certifications": [{"name": "CKA"}, {"name": "AWS SAA"}]}}
