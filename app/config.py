import os


def connected_mode():
    return os.getenv("GUIDEBOT_MODE") == "connected"
