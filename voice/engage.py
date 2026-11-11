"""Engagement discipline: his name is the only door, for every single task.

He hears the room all day, but nothing is ever acted upon, not a task,
not an ack, not a control word, unless his name is in the words. Even a
follow-up seconds after his last answer waits for the name again, no
matter how urgent it sounds. The window counted from the last acted
message only keeps the barge-in ear open while he is speaking, so a
named order can cut him off; it never opens a path to a response.
"""

WAKE_WORD = "diablo"


def decide(text):
    """Pure routing of one utterance: "wake" only when his name is in it.

    Anything else is the room talking to itself, media, muttering, other
    people, and is ignored.
    """
    if WAKE_WORD in text.lower():
        return "wake"
    return "ignore"
