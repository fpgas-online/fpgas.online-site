"""The states of the management pages, defined once (site #131, Tim 15 Oct 2026: the same names everywhere): a Pi's
condition (fleet.services.condition) and an area of the management index use the same marks and the same words, on
/fleet/, /management/ and a Pi's own page. One character a state (#127), its word in the title and for a screen
reader."""

from fleet.services import ATTENTION, MISSING, OPERATIONAL, UNKNOWN

# condition -> (mark, word). "checking" is the condition fleet.services calls "unknown" (a check that has not finished
# this boot), as the coordinator named it on 11 Oct: no state reads "unknown".
STATES = {OPERATIONAL: ("●", "operational"), ATTENTION: ("!", "needs attention"), MISSING: ("○", "missing"),
          UNKNOWN: ("⟳", "checking")}
MARKS = {condition: mark for condition, (mark, _) in STATES.items()}
WORDS = {condition: word for condition, (_, word) in STATES.items()}

# A check's or a run's result as a mark, the same four the Pi table's check columns use (#127):
# (mark, css class) for ran and passed, ran and failed, should have run but did not, not applicable.
PASSED, FAILED, NOT_RUN, NOT_APPLICABLE = ("✓", "mgmt-test-pass"), ("✗", "mgmt-test-fail"), \
    ("⊘", "mgmt-test-notrun"), ("–", "mgmt-test-none")
