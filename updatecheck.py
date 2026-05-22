from urllib.request import urlopen
from version import version
import re


def extract_version(text):
    match = re.search(r'v\d+\.\d+\.\d+', text)

    if match:
        return match.group(0)

    return None


def version_tuple(v):
    return tuple(map(int, v.lstrip("v").split(".")))


def updatecheck():
    url = "https://raw.githubusercontent.com/Deeppy1/audio-mixer/main/version.py"

    webtext = urlopen(url).read().decode()

    webversion = extract_version(webtext)

    print("Web version:", webversion)
    print("Local version:", version)

    if webversion is None:
        print("Could not parse remote version")
        return

    local = version_tuple(version)
    remote = version_tuple(webversion)

    if local < remote:
        print("You are behind")
    elif local > remote:
        print("You are ahead")
    else:
        print("You are up to date")


#updatecheck()
