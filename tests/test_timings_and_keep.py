# Copyright (C) 2026 William Wang
# Licensed under the GNU AGPL v3 or later. See LICENSE.
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis"))

import ig_unfollow_checker as ig  # noqa: E402
import igpaths  # noqa: E402
from keepstore import load_keep, normalise  # noqa: E402


@pytest.fixture
def restore_timings():
    saved = ig.current_timings()
    yield
    ig.apply_timings(saved)


class TestTimings:
    def test_overrides_reach_the_pacer(self, restore_timings):
        ig.apply_timings({"min_delay": 1.5, "max_delay": 2.5})
        p = ig.Pacer()
        assert (p.base_min, p.base_max) == (1.5, 2.5)

    def test_pacer_is_not_frozen_at_import(self, restore_timings):
        # Default arguments bind once; this is the regression that made a
        # --min-delay flag a silent no-op.
        ig.apply_timings({"min_delay": 7, "max_delay": 8})
        assert ig.Pacer().base_min == 7.0

    def test_values_are_cast_to_their_type(self, restore_timings):
        t = ig.apply_timings({"batch_size": "40", "max_delay": 7.5})
        assert t["batch_size"] == 40 and isinstance(t["batch_size"], int)
        assert t["max_delay"] == 7.5

    def test_min_above_max_is_rejected(self, restore_timings):
        with pytest.raises(ValueError, match="larger than max_delay"):
            ig.apply_timings({"min_delay": 9, "max_delay": 3})

    def test_a_rejected_override_changes_nothing(self, restore_timings):
        before = ig.current_timings()
        with pytest.raises(ValueError):
            ig.apply_timings({"batch_size": 30, "min_delay": 20, "max_delay": 1})
        assert ig.current_timings() == before

    def test_unknown_and_non_positive_are_rejected(self, restore_timings):
        with pytest.raises(ValueError, match="unknown timing"):
            ig.apply_timings({"nap_time": 3})
        with pytest.raises(ValueError, match="greater than zero"):
            ig.apply_timings({"batch_size": 0})
        with pytest.raises(ValueError, match="must be a number"):
            ig.apply_timings({"settle_ms": "soon"})

    def test_warns_only_below_what_was_measured(self, restore_timings):
        assert ig.timing_warnings(ig.current_timings()) == []
        fast = dict(ig.current_timings(), min_delay=2.0, settle_ms=600)
        assert len(ig.timing_warnings(fast)) == 2


class TestKeepList:
    def test_normalise_accepts_every_way_of_naming_an_account(self):
        for raw in ("alice", "@alice", " alice ", "https://www.instagram.com/alice/",
                    "http://instagram.com/alice?igsh=abc"):
            assert normalise(raw) == "alice"

    def test_plain_text_with_comments(self, tmp_path):
        f = tmp_path / "keep.txt"
        f.write_text("alice\n@bob  # met at the conference\n\nhttps://www.instagram.com/carol/\n")
        assert load_keep(f) == {"alice", "bob", "carol"}

    def test_the_browser_export_format(self, tmp_path):
        f = tmp_path / "ig-keep-list.json"
        f.write_text(json.dumps({"format": "ig-keep-v1", "keep": ["alice", "@bob"],
                                 "clicks": {"alice": 3}}))
        assert load_keep(f) == {"alice", "bob"}

    def test_a_bare_json_list(self, tmp_path):
        f = tmp_path / "keep.json"
        f.write_text('["alice", "bob"]')
        assert load_keep(f) == {"alice", "bob"}

    def test_config_merges_inline_names_and_a_file(self, tmp_path):
        (tmp_path / "export.zip").write_bytes(b"")
        (tmp_path / "keep.txt").write_text("carol\n@alice\n")
        cfg_path = tmp_path / "snapshots.json"
        cfg_path.write_text(json.dumps({"latest_zip": "export.zip",
                                        "keep": ["@alice", "bob"],
                                        "keep_file": "keep.txt"}))
        assert igpaths.load(cfg_path).keep == ["alice", "bob", "carol"]

    def test_a_missing_keep_file_fails_loudly(self, tmp_path):
        (tmp_path / "export.zip").write_bytes(b"")
        cfg_path = tmp_path / "snapshots.json"
        cfg_path.write_text(json.dumps({"latest_zip": "export.zip",
                                        "keep_file": "nowhere.json"}))
        with pytest.raises(SystemExit, match="keep_file not found"):
            igpaths.load(cfg_path)
