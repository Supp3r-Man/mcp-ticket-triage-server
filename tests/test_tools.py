"""Behaviour of the tools themselves, driven in-process."""
import re

from conftest import call
from ticketmcp.data import AGENTS, make_tickets


def _all_ids(server, **filters):
    ids, cursor = [], None
    while True:
        args = dict(filters, limit=7)
        if cursor:
            args["cursor"] = cursor
        is_error, page = call(server, "search_tickets", args)
        assert not is_error
        ids += [t["id"] for t in page["tickets"]]
        cursor = page["next_cursor"]
        if not cursor:
            return ids


def test_search_defaults_to_ten_ordered_summaries_with_a_cursor(server):
    _, page = call(server, "search_tickets")
    assert [t["id"] for t in page["tickets"]] == [f"T-{1001 + i}" for i in range(10)]
    assert page["next_cursor"] and page["total_matches"] == 60
    assert "body" not in page["tickets"][0] and "notes" not in page["tickets"][0]


def test_search_limit_is_respected(server):
    _, page = call(server, "search_tickets", {"limit": 3})
    assert len(page["tickets"]) == 3


def test_pagination_visits_every_ticket_exactly_once(server):
    ids = _all_ids(server)
    assert ids == [f"T-{1001 + i}" for i in range(60)]


def test_last_page_has_no_cursor_and_exact_multiple_does_not_leave_an_empty_page(server):
    _, page = call(server, "search_tickets", {"limit": 50})
    assert page["next_cursor"] is not None
    _, rest = call(server, "search_tickets", {"limit": 50, "cursor": page["next_cursor"]})
    assert len(rest["tickets"]) == 10 and rest["next_cursor"] is None
    _, whole = call(server, "search_tickets", {"queue": "billing", "limit": 15})
    assert len(whole["tickets"]) == 15 and whole["next_cursor"] is None  # 15 billing tickets: no empty extra page


def test_filter_by_queue_status_priority(server):
    for field, value in (("queue", "security"), ("status", "pending"), ("priority", "urgent")):
        _, page = call(server, "search_tickets", {field: value, "limit": 50})
        assert page["tickets"] and all(t[field] == value for t in page["tickets"])


def test_filter_by_assignee_and_unassigned(server):
    _, mine = call(server, "search_tickets", {"assignee": "avery", "limit": 50})
    assert mine["tickets"] and all(t["assignee"] == "avery" for t in mine["tickets"])
    _, none = call(server, "search_tickets", {"assignee": "unassigned", "limit": 50})
    assert none["tickets"] and all(t["assignee"] is None for t in none["tickets"])


def test_filters_combine_with_and(server):
    _, page = call(server, "search_tickets", {"queue": "billing", "status": "open", "limit": 50})
    assert all(t["queue"] == "billing" and t["status"] == "open" for t in page["tickets"])
    _, both = call(server, "search_tickets", {"queue": "billing", "limit": 50})
    assert len(page["tickets"]) < len(both["tickets"])


def test_text_query_is_case_insensitive_over_subject_and_body(server):
    _, lower = call(server, "search_tickets", {"query": "webhook", "limit": 50})
    _, upper = call(server, "search_tickets", {"query": "WEBHOOK", "limit": 50})
    assert lower["tickets"] and lower == upper
    _, body = call(server, "search_tickets", {"query": "r-7013", "limit": 50})
    assert [t["id"] for t in body["tickets"]] == ["T-1002"]


def test_pagination_preserves_filters_across_pages(server):
    ids = _all_ids(server, queue="technical")
    _, whole = call(server, "search_tickets", {"queue": "technical", "limit": 50})
    assert ids == [t["id"] for t in whole["tickets"]] and len(ids) == 15


def test_garbage_and_foreign_cursors_are_rejected_with_a_clear_error(server):
    for cursor in ("!!!", "bm90LW1pbmU", "YWZ0ZXI6bm9wZQ"):  # not base64 / wrong prefix / not a ticket id
        is_error, text = call(server, "search_tickets", {"cursor": cursor})
        assert is_error and text.startswith("[invalid_cursor]")


def test_argument_validation_errors_are_tool_errors(server):
    for args in ({"limit": 0}, {"limit": 51}, {"limit": "5"}, {"queue": "sales"}, {"bogus": 1}, {"query": "x" * 101}):
        is_error, text = call(server, "search_tickets", args)
        assert is_error and text.startswith("[invalid_arguments]"), args


def test_get_ticket_returns_full_ticket_and_rejects_bad_ids(server):
    _, t = call(server, "get_ticket", {"ticket_id": "T-1001"})
    assert t["id"] == "T-1001" and t["body"] and isinstance(t["notes"], list)
    assert call(server, "get_ticket", {"ticket_id": "T-9999"})[1].startswith("[not_found]")
    assert call(server, "get_ticket", {"ticket_id": "1001"})[1].startswith("[invalid_arguments]")
    assert call(server, "get_ticket", {})[1].startswith("[invalid_arguments]")


def test_list_queues_counts_add_up(server):
    _, data = call(server, "list_queues")
    assert [q["queue"] for q in data["queues"]] == ["billing", "technical", "onboarding", "security"]
    assert sum(q["total"] for q in data["queues"]) == 60
    for q in data["queues"]:
        assert q["open"] + q["pending"] + q["resolved"] == q["total"]


def test_instruction_like_ticket_text_is_returned_as_plain_data_and_changes_nothing(rw_server):
    _, before = call(rw_server, "search_tickets", {"assignee": "devon", "limit": 50})
    _, t = call(rw_server, "get_ticket", {"ticket_id": "T-1013"})
    assert "IGNORE PREVIOUS INSTRUCTIONS" in t["body"]
    _, after = call(rw_server, "search_tickets", {"assignee": "devon", "limit": 50})
    assert before == after and call(rw_server, "get_ticket", {"ticket_id": "T-1013"})[1]["notes"] == t["notes"]


def test_synthetic_data_is_deterministic_and_has_no_card_ssn_or_secret_patterns():
    assert make_tickets() == make_tickets()
    blob = " ".join(str(t) for t in make_tickets())
    assert not re.search(r"\b(?:\d[ -]?){13,19}\b", blob)
    assert not re.search(r"\b\d{3}-\d{2}-\d{4}\b", blob)
    assert not re.search(r"AKIA[0-9A-Z]{12}|ghp_[A-Za-z0-9]{20}", blob)


# ---- writes ----

def test_write_tools_are_refused_when_read_only_even_if_called_directly(server):
    key = "key-readonly-1"
    for name, args in (("add_note", {"ticket_id": "T-1001", "text": "hi", "idempotency_key": key}),
                       ("assign_ticket", {"ticket_id": "T-1001", "assignee": "avery", "idempotency_key": key})):
        is_error, text = call(server, name, args)
        assert is_error and text.startswith("[writes_disabled]")
    assert call(server, "get_ticket", {"ticket_id": "T-1001"})[1]["notes"] == make_tickets()[0]["notes"]


def test_add_note_appends_and_is_visible_on_the_ticket(rw_server):
    _, before = call(rw_server, "get_ticket", {"ticket_id": "T-1002"})
    is_error, res = call(rw_server, "add_note", {"ticket_id": "T-1002", "text": "Called the customer.", "author": "avery", "idempotency_key": "note-key-0001"})
    assert not is_error and res["note"]["author"] == "avery" and res["note"]["created"] == "2026-10-01T00:00:00Z"
    _, after = call(rw_server, "get_ticket", {"ticket_id": "T-1002"})
    assert len(after["notes"]) == len(before["notes"]) + 1 and after["notes"][-1]["text"] == "Called the customer."


def test_add_note_defaults_author_and_validates_input(rw_server):
    _, res = call(rw_server, "add_note", {"ticket_id": "T-1002", "text": "ok", "idempotency_key": "note-key-0002"})
    assert res["note"]["author"] == "mcp"
    for args in ({"ticket_id": "T-1002", "text": "", "idempotency_key": "note-key-0003"},
                 {"ticket_id": "T-1002", "text": "x" * 1001, "idempotency_key": "note-key-0003"},
                 {"ticket_id": "T-1002", "text": "ok", "author": "Bad Name", "idempotency_key": "note-key-0003"},
                 {"ticket_id": "T-1002", "text": "ok"},
                 {"ticket_id": "T-1002", "text": "ok", "idempotency_key": "short"}):
        assert call(rw_server, "add_note", args)[1].startswith("[invalid_arguments]"), args


def test_assign_ticket_changes_the_assignee_and_reports_the_previous_one(rw_server):
    _, before = call(rw_server, "get_ticket", {"ticket_id": "T-1001"})
    _, res = call(rw_server, "assign_ticket", {"ticket_id": "T-1001", "assignee": "devon", "idempotency_key": "assign-key-01"})
    assert res == {"ticket_id": "T-1001", "assignee": "devon", "previous_assignee": before["assignee"]}
    assert call(rw_server, "get_ticket", {"ticket_id": "T-1001"})[1]["assignee"] == "devon"


def test_assign_rejects_unknown_assignee_and_allows_unassigning(rw_server):
    assert call(rw_server, "assign_ticket", {"ticket_id": "T-1001", "assignee": "mallory", "idempotency_key": "assign-key-02"})[1].startswith("[unknown_assignee]")
    _, res = call(rw_server, "assign_ticket", {"ticket_id": "T-1001", "assignee": None, "idempotency_key": "assign-key-03"})
    assert res["assignee"] is None
    assert call(rw_server, "get_ticket", {"ticket_id": "T-1001"})[1]["assignee"] is None
    assert AGENTS  # roster is non-empty


def test_retrying_a_write_with_the_same_key_applies_it_once_and_returns_the_original(rw_server):
    args = {"ticket_id": "T-1003", "text": "Escalated to tier 2.", "idempotency_key": "retry-key-0001"}
    _, first = call(rw_server, "add_note", args)
    _, second = call(rw_server, "add_note", args)
    assert first == second
    assert second["note"]["id"] == first["note"]["id"]
    notes = call(rw_server, "get_ticket", {"ticket_id": "T-1003"})[1]["notes"]
    assert [n["text"] for n in notes].count("Escalated to tier 2.") == 1


def test_same_key_with_different_arguments_is_a_conflict_and_changes_nothing(rw_server):
    call(rw_server, "add_note", {"ticket_id": "T-1003", "text": "first", "idempotency_key": "conflict-key-01"})
    is_error, text = call(rw_server, "add_note", {"ticket_id": "T-1003", "text": "different", "idempotency_key": "conflict-key-01"})
    assert is_error and text.startswith("[idempotency_conflict]")
    notes = call(rw_server, "get_ticket", {"ticket_id": "T-1003"})[1]["notes"]
    assert "different" not in [n["text"] for n in notes]


def test_same_key_reused_for_a_different_tool_is_a_conflict(rw_server):
    call(rw_server, "assign_ticket", {"ticket_id": "T-1003", "assignee": "avery", "idempotency_key": "shared-key-001"})
    is_error, text = call(rw_server, "add_note", {"ticket_id": "T-1003", "text": "hi", "idempotency_key": "shared-key-001"})
    assert is_error and text.startswith("[idempotency_conflict]")


def test_a_failed_write_is_not_remembered_so_the_key_can_be_retried(rw_server):
    key = "recover-key-001"
    assert call(rw_server, "add_note", {"ticket_id": "T-9999", "text": "lost", "idempotency_key": key})[1].startswith("[not_found]")
    is_error, res = call(rw_server, "add_note", {"ticket_id": "T-1003", "text": "now valid", "idempotency_key": key})
    assert not is_error and res["note"]["text"] == "now valid"


def test_different_keys_apply_separately(rw_server):
    call(rw_server, "add_note", {"ticket_id": "T-1004", "text": "same text", "idempotency_key": "distinct-key-01"})
    call(rw_server, "add_note", {"ticket_id": "T-1004", "text": "same text", "idempotency_key": "distinct-key-02"})
    notes = call(rw_server, "get_ticket", {"ticket_id": "T-1004"})[1]["notes"]
    assert [n["text"] for n in notes].count("same text") == 2
