"""Ask the running app to minimize its window, then look at the screen."""
import os
import sys
import time
import urllib.error
import urllib.request

from PIL import ImageGrab

TEMP = os.environ["TEMP"]
BASE = "http://127.0.0.1:8768"


def shoot(name):
    path = os.path.join(TEMP, name)
    ImageGrab.grab().save(path)
    return path


def post(action):
    request = urllib.request.Request(
        f"{BASE}/api/window/{action}", data=b"",
        headers={"X-QB-Request": "1"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()[:200].decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read()[:200].decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001 - diagnosis tool
        return None, repr(error)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "minimize"
    print("before:", shoot("win-before.png"))
    print("POST /api/window/%s -> %s" % (action, post(action)))
    time.sleep(6)
    print("after: ", shoot("win-after.png"))
