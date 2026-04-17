import re

path = r"src/sankaku_uploader/ui/main_window.py"
content = open(path, "r", encoding="utf-8").read()

# Fix the line with Chinese curly quotes inside an f-string
fixed = content.replace(
    'f"确定删除队列 \u201c{task.task_name}\u201d\uff1f此操作不可撤销。",',
    "f\"确定删除队列 '{task.task_name}'？此操作不可撤销。\","
)

if fixed != content:
    open(path, "w", encoding="utf-8").write(fixed)
    print("Fixed!")
else:
    # Print the raw bytes of line 451 (0-indexed) for debugging
    lines = content.splitlines()
    print("NOT FOUND. Line 451:", repr(lines[451]))
