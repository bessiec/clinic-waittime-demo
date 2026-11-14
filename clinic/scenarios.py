"""Named scenarios. Each names the front-desk persona on duty and the
injected events. Thresholds and rates come from config, not from here.

Hold-out scenarios are never tuned against: their scored evals are reported
separately and do not fail the run, but their hard gates still do."""

SCENARIOS = {
    "normal_day": {
        "frontdesk": "dana", "surge": False, "providers_absent": 0, "holdout": False,
        "description": "Dana on the desk, all seven patient personas, base walk-in rate.",
    },
    "kim_goes_quiet": {
        "frontdesk": "kim", "surge": True, "burst": True, "providers_absent": 0, "holdout": False,
        "description": "Kim on the desk, walk-in surge plus an injected burst at 3 PM that pushes the queue past her limit.",
    },
    "walkin_surge": {
        "frontdesk": "dana", "surge": True, "providers_absent": 0, "holdout": False,
        "description": "Dana on the desk with a walk-in surge from 3 to 4 PM.",
    },
    "luis_day": {
        "frontdesk": "luis", "surge": False, "providers_absent": 0, "holdout": False,
        "description": "Luis on the desk: batched updates, his own headcount guesses.",
    },
    "pat_day": {
        "frontdesk": "pat", "surge": False, "providers_absent": 0, "holdout": False,
        "description": "Pat on the desk: on-time updates, erratic guesses and run-long flags.",
    },
    "understaffed_opening": {
        "frontdesk": "dana", "surge": False, "providers_absent": 1, "holdout": True,
        "description": "HOLD-OUT. Dana on the desk, one provider absent from opening.",
    },
    "luis_surge": {
        "frontdesk": "luis", "surge": True, "providers_absent": 0, "holdout": True,
        "description": "HOLD-OUT. Luis on the desk during a walk-in surge.",
    },
}
