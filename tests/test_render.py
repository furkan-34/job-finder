from jobfinder.profile.render import render_profile_md


def test_render_has_no_contact_info(example_profile):
    md = render_profile_md(example_profile)
    identity = example_profile["identity"]
    for key in ("full_name", "email", "phone", "linkedin", "github"):
        assert identity[key] not in md


def test_render_starts_with_preferences(example_profile):
    md = render_profile_md(example_profile)
    assert md.index("## What I am looking for") < md.index("## Summary") < md.index("## Experience")


def test_render_is_deterministic(example_profile):
    assert render_profile_md(example_profile) == render_profile_md(example_profile)
