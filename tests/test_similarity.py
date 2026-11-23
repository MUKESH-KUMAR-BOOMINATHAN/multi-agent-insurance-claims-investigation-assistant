"""The similar-claims index: distance, ranking, explanations, feedback, and failure modes."""

import logging

import pytest

import config
from agents import feedback_node as fb
from claims_data import load_cleaned_claims
from retrieval import retrieval_node, similarity
from retrieval.similarity import IndexNotBuiltError, SimilarityIndex

# Captured at import, i.e. before conftest's autouse fixture replaces get_index with a stub.
REAL_GET_INDEX = similarity.get_index
REAL_NODE_GET_INDEX = retrieval_node.get_index


def rec(severity="Major Damage", kind="Single Vehicle Collision", total=50000, label="Y", **extra):
    base = {
        "incident_severity": severity,
        "incident_type": kind,
        "collision_type": "Side Collision",
        "authorities_contacted": "Police",
        "police_report_available": "YES",
        "property_damage": "YES",
        "total_claim_amount": total,
        "injury_claim": total * 0.1,
        "property_claim": total * 0.2,
        "vehicle_claim": total * 0.7,
        "fraud_reported": label,
    }
    return {**base, **extra}


@pytest.fixture
def history():
    return [
        rec("Major Damage", "Single Vehicle Collision", 70000, "Y"),
        rec("Minor Damage", "Parked Car", 3000, "N", collision_type="Not Applicable", authorities_contacted="None"),
        rec("Total Loss", "Vehicle Theft", 90000, "Y", collision_type="Not Applicable"),
        rec("Trivial Damage", "Multi-vehicle Collision", 1000, "N"),
    ]


def test_an_identical_claim_is_the_top_hit_with_score_one(history):
    hits = SimilarityIndex(history).search(history[0], k=4)
    assert hits[0]["score"] == pytest.approx(1.0) and hits[0]["incident_severity"] == "Major Damage"


def test_results_are_ranked_best_first_and_scores_stay_in_range(history):
    hits = SimilarityIndex(history).search(rec("Major Damage", "Single Vehicle Collision", 65000), k=4)
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True) and all(0.0 <= s <= 1.0 for s in scores)
    assert hits[0]["incident_severity"] == "Major Damage"  # same severity + similar size wins


def test_similar_incident_beats_a_dissimilar_one(history):
    query = rec("Minor Damage", "Parked Car", 4000, collision_type="Not Applicable", authorities_contacted="None")
    assert SimilarityIndex(history).search(query, k=1)[0]["incident_type"] == "Parked Car"


def test_each_hit_says_which_fields_matched(history):
    hit = SimilarityIndex(history).search(history[0], k=1)[0]
    assert {"severity", "incident type", "police report", "claim size"} <= set(hit["matched_fields"])
    other = SimilarityIndex(history).search(history[0], k=4)[-1]
    assert "severity" not in other["matched_fields"] or other["incident_severity"] == "Major Damage"


def test_k_is_clamped_and_never_returns_duplicates(history):
    hits = SimilarityIndex(history).search(history[0], k=50)
    assert len(hits) == 4
    assert len({(h["summary"], h["score"]) for h in hits}) >= 3


def test_search_is_deterministic_including_ties(history):
    index = SimilarityIndex(history + history)  # exact duplicates force ties
    first = index.search(history[1], k=5)
    assert first == index.search(history[1], k=5)


def test_missing_and_non_numeric_fields_never_raise(history):
    index = SimilarityIndex(history)
    assert index.search({}, k=2)  # an empty claim still returns something sane
    assert index.search({"total_claim_amount": "not a number", "incident_type": None, "injury_claim": float("nan")}, k=2)
    assert all(0.0 <= h["score"] <= 1.0 for h in index.search({"incident_type": "Spaceship"}, k=4))


def test_an_unseen_category_matches_nothing_in_that_field(history):
    hit = SimilarityIndex(history).search(rec("Never Seen", "Parked Car", 3000), k=1)[0]
    assert "severity" not in hit["matched_fields"]


def test_numeric_difference_is_capped_so_one_outlier_cannot_dominate(history):
    index = SimilarityIndex(history)
    huge, large = index.search(rec(total=10**9), k=4), index.search(rec(total=10**7), k=4)
    assert [h["score"] for h in huge] == pytest.approx([h["score"] for h in large])  # both capped at max distance


def test_feedback_is_searchable_immediately_and_counted(history):
    index = SimilarityIndex(history)
    assert len(index) == 4
    new = rec("Minor Damage", "Vehicle Theft", 12345, "Y")
    index.add_feedback(new, "N")
    assert len(index) == 5
    top = index.search(new, k=1)[0]
    assert top["source"] == "investigator_feedback" and top["fraud_reported"] == "N" and top["score"] == pytest.approx(1.0)


def test_feedback_from_the_database_is_reloaded_at_startup(history):
    loaded = [(rec("Minor Damage", "Vehicle Theft", 777), "N")]
    index = SimilarityIndex(history, feedback_loader=lambda: loaded)
    assert len(index) == 5 and index.search(loaded[0][0], k=1)[0]["source"] == "investigator_feedback"


def test_a_failing_feedback_loader_degrades_to_historical_only(history, caplog):
    def broken():
        raise RuntimeError("db locked")

    with caplog.at_level(logging.ERROR):
        index = SimilarityIndex(history, feedback_loader=broken)
    assert len(index) == 4 and index.search(history[0], k=1)
    assert "Could not load feedback" in caplog.text


def test_an_empty_history_is_refused():
    with pytest.raises(IndexNotBuiltError):
        SimilarityIndex([])


# --- the real data + singleton -----------------------------------------------
def test_get_index_builds_from_the_csv_and_database_feedback(monkeypatch):
    fb._save_feedback({"incident_type": "Parked Car", "incident_severity": "Minor Damage"}, {}, {}, "agreed", "N", claim_id=1)
    similarity.reset_index()
    try:
        index = REAL_GET_INDEX()
        history_rows = len(load_cleaned_claims(config.CSV_PATH))
        assert len(index) == history_rows + 1  # historical + the feedback row rebuilt from SQLite
        assert REAL_GET_INDEX() is index  # singleton
        assert similarity.status() == {"ready": True, "documents": history_rows + 1}
    finally:
        similarity.reset_index()


def test_real_history_returns_sensible_neighbours():
    similarity.reset_index()
    try:
        hits = REAL_GET_INDEX().search(rec("Major Damage", "Multi-vehicle Collision", 60000), k=5)
        assert len(hits) == 5 and all(h["fraud_reported"] in ("Y", "N") for h in hits)
        assert sum(h["incident_severity"] == "Major Damage" for h in hits) >= 4  # severity dominates the match
    finally:
        similarity.reset_index()


def test_a_missing_csv_raises_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CSV_PATH", tmp_path / "nope.csv")
    with pytest.raises(IndexNotBuiltError, match="historical claims"):
        similarity.load_historical_records()


def test_status_never_triggers_a_build():
    similarity.reset_index()
    assert similarity.status() == {"ready": False, "documents": None}


# --- the retrieval node on top of it ---------------------------------------------
def test_node_shows_five_claims_but_votes_over_fifteen(monkeypatch, history):
    many = [rec("Major Damage", "Single Vehicle Collision", 60000 + i, "Y" if i < 6 else "N") for i in range(20)]
    index = SimilarityIndex(many)
    monkeypatch.setattr(retrieval_node, "get_index", lambda: index)
    result = retrieval_node.retrieval_node({"claim": many[0]})
    assert len(result["similar_claims"]) == 5
    assert result["similar_claims_summary"] == "6 of the 15 most similar past claims were confirmed fraud."
    assert "warnings" not in result and "matched_fields" in result["similar_claims"][0]


def test_node_degrades_with_a_warning_instead_of_raising(monkeypatch):
    def boom():
        raise IndexNotBuiltError("no history")

    monkeypatch.setattr(retrieval_node, "get_index", boom)
    result = retrieval_node.retrieval_node({"claim": {}})
    assert result["similar_claims"] == [] and "could not be loaded" in result["warnings"][0]

    def crash():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(retrieval_node, "get_index", crash)
    assert retrieval_node.retrieval_node({"claim": {}})["similar_claims"] == []


def test_leave_one_out_votes_never_count_a_claim_as_its_own_neighbour():
    # Two well-separated groups: fraud claims are all large/Major, clean ones small/Minor.
    fraud = [rec("Major Damage", "Single Vehicle Collision", 80000 + i, "Y") for i in range(5)]
    clean = [rec("Minor Damage", "Parked Car", 2000 + i, "N", collision_type="Not Applicable") for i in range(5)]
    votes = SimilarityIndex(fraud + clean).leave_one_out_fraud_votes(k=3)
    assert votes[:5].tolist() == [1.0] * 5 and votes[5:].tolist() == [0.0] * 5
