import json
import subprocess
import sys
import time


def ctl(*args):
    result = subprocess.run(
        ["tern", "ctl", "--control", sys.argv[1], *args],
        check=True, capture_output=True, text=True, timeout=8,
    )
    return json.loads(result.stdout)


main = ctl("dump", '[data-surface="plugin.gitbutler.workspace"].sf-main')["elements"]
assert len(main) == 1, "open one GitButler workspace in Board view"
x, y, w, h = main[0]["rect"]
for selector, count in ((".bp-tab", 5), (".bp-tools button", 10)):
    controls = ctl("tree", selector)["nodes"]
    assert len(controls) == count, f"missing controls: {selector}"
    for node in controls:
        bx, by, bw, bh = node["rect"]
        assert x <= bx and bx + bw <= x + w + 1, f"clipped horizontally: {node['text']}"
        assert y <= by and by + bh <= y + h + 1, f"clipped vertically: {node['text']}"
        picked = ctl("pick", str(bx + bw / 2), str(by + bh / 2))
        assert all(abs(a - b) < 1 for a, b in zip(picked["rect"], node["rect"])), f"obscured: {node['text']}"
        if selector == ".bp-tab":
            ctl("click", str(bx + bw / 2), str(by + bh / 2))
            for _ in range(30):
                active = ctl("tree", ".bp-tab.bp-on")["nodes"]
                if active and active[0]["text"] == node["text"]:
                    break
                time.sleep(0.1)
            assert active and active[0]["text"] == node["text"], f"unclickable: {node['text']}"

print("workspace controls: all five tabs and ten board actions fit")
