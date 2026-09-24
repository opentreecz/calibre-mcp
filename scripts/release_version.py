"""Print a GitHub output selecting a release from Conventional Commits."""
import re
import subprocess


def next_version(previous, messages):
    if previous is None:
        return "v0.1.0"
    major, minor, patch = map(int, previous.lstrip("v").split("."))
    if re.search(r"^[a-z]+(?:\([^\n]+\))?!:|^BREAKING[ -]CHANGE:", messages, re.M):
        return f"v{major + 1}.0.0"
    if re.search(r"^feat(?:\([^\n]+\))?:", messages, re.M):
        return f"v{major}.{minor + 1}.0"
    if re.search(r"^(?:fix|perf)(?:\([^\n]+\))?:", messages, re.M):
        return f"v{major}.{minor}.{patch + 1}"
    return ""


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def main():
    tags = [tag for tag in git("tag", "--merged", "HEAD").splitlines()
            if re.fullmatch(r"v\d+\.\d+\.\d+", tag)]
    previous = max(tags, key=lambda t: tuple(map(int, t[1:].split("."))), default=None)
    if previous and git("rev-list", "-n", "1", previous) == git("rev-parse", "HEAD"):
        version = previous  # Retry publication without incrementing the tag.
    else:
        messages = git("log", "--format=%B", f"{previous}..HEAD" if previous else "HEAD")
        version = next_version(previous, messages)
    print(f"version={version}")


if __name__ == "__main__":
    main()
