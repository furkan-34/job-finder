from jobfinder.profile.schema import validate


def test_example_profile_is_valid(example_profile):
    assert validate(example_profile) == []


def test_unconfirmed_dealbreakers_are_invalid(example_profile):
    example_profile["preferences"]["dealbreakers"]["confirmed"] = False
    assert validate(example_profile)
