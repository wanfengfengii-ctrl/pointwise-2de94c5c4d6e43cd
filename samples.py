"""Inputs only. These cases do not prescribe the review conclusions."""
CASES = {
    "normal": {
        "if_exists": "append", "owner": "engine", "catch": False,
        "begin": "legacy", "values": [1, 2, 3, 4], "chunksize": 2
    },
    "incident": {
        "if_exists": "append", "owner": "engine", "catch": False,
        "begin": "legacy", "values": [1, 2, -3, 4], "chunksize": 2
    },
    "replace": {
        "if_exists": "replace", "owner": "connection", "catch": False,
        "begin": "legacy", "values": [1, 2, 9223372036854775808, 4],
        "chunksize": 2
    }
}
