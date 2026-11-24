from jobfinder.paths import migrate_legacy_layout


def test_migrates_flat_layout_without_overwriting(tmp_path):
    (tmp_path / "profile.json").write_text("{}")
    (tmp_path / "cv.pdf").write_bytes(b"%PDF")
    (tmp_path / "raw" / "linkedin").mkdir(parents=True)
    (tmp_path / "raw" / "linkedin" / "a.json").write_text("{}")
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile.md").write_text("old")
    (tmp_path / "profile" / "profile.md").write_text("new")

    assert migrate_legacy_layout(tmp_path) == [
        "profile.json -> profile/profile.json", "cv.pdf -> profile/cv.pdf",
        "raw -> fetch/raw"]
    assert (tmp_path / "fetch" / "raw" / "linkedin" / "a.json").exists()
    assert (tmp_path / "profile" / "profile.md").read_text() == "new"
    assert (tmp_path / "profile.md").exists()
    assert migrate_legacy_layout(tmp_path) == []
