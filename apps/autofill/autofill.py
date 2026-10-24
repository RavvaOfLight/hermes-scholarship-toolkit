"""Save or check the answers the browser companion fills in. Used by you or by Hermes.

    python3 autofill.py status                 # what's saved per section (no values)
    python3 autofill.py keys [section]         # item keys and labels
    python3 autofill.py set KEY VALUE          # e.g. set school.counselor_email counselor@school.example.org
    python3 autofill.py unset KEY
    python3 autofill.py show [section]         # prints saved values: only when the student asks
    python3 autofill.py list-add LIST field=value ...   # e.g. list-add activities name="Robotics Club" role=Captain
    python3 autofill.py list-show LIST
    python3 autofill.py list-update LIST NUMBER field=value ...   # change fields of one row, keeping the rest
    python3 autofill.py list-remove LIST NUMBER        # NUMBER starts at 1
    python3 autofill.py transcript FILE                # read classes + GPA from a transcript (.txt .csv .pdf, or - for pasted text); saves nothing
    python3 autofill.py transcript FILE --save [--replace]   # save the rows with no questions (--replace: drop saved classes first)
"""
import json
import re
import sys

import autofill_store as store


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    schema = store.SCHEMA
    if cmd == "status":
        for row in store.summary(store.load()):
            print(f"{row['section']}: {row['saved']} of {row['of']}" + (f"  (missing: {', '.join(row['missing'])})" if row.get("missing") else ""))
        return 0
    if cmd == "keys":
        for sec in schema["sections"]:
            if args and args[0] != sec["id"]:
                continue
            print(f"[{sec['id']}] {sec['title']}")
            for item in sec["items"]:
                extra = f" — one of: {', '.join(item['options'])}" if item.get("options") else ""
                print(f"  {item['key']}: {item['label']} ({item['type']}){extra}")
        for lst in schema["lists"]:
            if not args or args[0] == lst["id"]:
                print(f"[list {lst['id']}] fields: {', '.join(f['id'] for f in lst['fields'])}")
        return 0
    if cmd in ("set", "unset"):
        if (cmd == "set" and len(args) < 2) or (cmd == "unset" and len(args) != 1):
            print("usage: set KEY VALUE | unset KEY")
            return 2
        args = [a for a in args if a != "--"]  # "set financial.sai -- -1500"
        value = " ".join(args[1:]) if cmd == "set" else ""
        data, errors = store.set_values({args[0]: value})
        if errors:
            print(f"NOT SAVED: {errors[args[0]]}")
            return 1
        print(f"saved {args[0]} = {data['values'].get(args[0], '(cleared)')}")
        return 0
    if cmd == "show":
        data = store.load()
        for sec in schema["sections"]:
            if args and args[0] != sec["id"]:
                continue
            for item in sec["items"]:
                if item["key"] in data["values"]:
                    print(f"{item['key']} = {data['values'][item['key']]}")
        return 0
    if cmd == "list-show":
        rows = store.load()["lists"].get(args[0] if args else "", None)
        if rows is None:
            print("usage: list-show courses|exams|activities|awards|work|recommenders|custom")
            return 2
        for i, row in enumerate(rows, 1):
            print(f"{i}. {json.dumps(row, ensure_ascii=False)}")
        return 0
    if cmd == "list-add":
        item = dict(a.split("=", 1) for a in args[1:] if "=" in a)
        try:
            data = store.list_op(args[0], "add", item=item)
        except store.ValidationError as err:
            print(f"NOT SAVED: {err}")
            return 1
        print(f"saved {args[0]} #{len(data['lists'][args[0]])}")
        return 0
    if cmd == "list-update":
        try:
            item = dict(a.split("=", 1) for a in args[2:] if "=" in a)
            data = store.list_op(args[0], "update", int(args[1]) - 1, item=item)
        except (store.ValidationError, IndexError, ValueError) as err:
            print(f"NOT SAVED: {err}")
            return 1
        print(f"saved {args[0]} #{args[1]}: {json.dumps(data['lists'][args[0]][int(args[1]) - 1], ensure_ascii=False)}")
        return 0
    if cmd == "list-remove":
        try:
            store.list_op(args[0], "remove", int(args[1]) - 1)
        except (store.ValidationError, IndexError, ValueError) as err:
            print(f"NOT REMOVED: {err}")
            return 1
        print("removed")
        return 0
    if cmd == "transcript":
        return transcript_cmd(args)
    print(f"unknown command {cmd}\n{__doc__}")
    return 2


def transcript_cmd(args):
    import transcript

    files = [a for a in args if a == "-" or not a.startswith("--")]
    if len(files) != 1:
        print("usage: transcript FILE [--save] [--replace]")
        return 2
    result = transcript.parse(transcript.read_file(files[0]))
    rows = result["courses"]
    if not rows:
        print("No classes found. If this is a scan (a picture), type the classes in or paste the text instead.")
        return 1
    print(f"Read {len(rows)} classes ({result['format']} layout). Check every row:")
    for i, c in enumerate(rows, 1):
        bits = [c["level"], c["grade_level"] or "grade ?", c["term"] or "", f"grade {c['grade']}" if c["grade"] else c["status"],
                f"{c['credits']} cr" if c["credits"] else "", f"{c['gpa_points']} pts" if c["gpa_points"] else ""]
        flag = "  CHECK: " + " ".join(c["issues"]) if c["issues"] else ""
        print(f"{i:>3}. {c['name']} — " + ", ".join(b for b in bits if b) + flag)
    labels = {"gpa_unweighted": "Unweighted GPA", "gpa_weighted": "Weighted GPA",
              "gpa_unlabeled": "GPA (not labeled weighted or unweighted: ask which)", "class_rank": "Class rank",
              "class_size": "Class size", "total_credits": "Total credits"}
    for key, value in result["summary"].items():
        if key == "gpa_other":  # the school's own GPA names: the student says which (if any) is weighted/unweighted
            for g in value:
                print(f"  GPA called \"{g['label']}\": {g['value']} (ask the student whether to save it, and as which GPA)")
            continue
        v = value["value"] + (f" on a {value['scale']} scale" if value.get("scale") else "") if isinstance(value, dict) else value
        print(f"  {labels[key]}: {v}")
    for e in result.get("exams", []):
        print(f"  exam score: {e['exam']} — {e['score']}")
    for note in result["notes"]:
        print(f"  note: {note}")
    if result["unread"]:
        print(f"  {len(result['unread'])} line(s) looked like classes but couldn't be read:")
        for line in result["unread"][:10]:
            print(f"     {line}")
    if "--save" not in args:
        print("Nothing saved. Rerun with --save after the student confirms the rows.")
        return 0
    good = [transcript.to_list_item(c) for c in rows if c["ok"]]
    held = [c["name"] for c in rows if not c["ok"]]
    if good:
        try:
            _, errors = store.list_add_many("courses", good, replace="--replace" in args)
        except store.ValidationError as err:
            print(f"NOT SAVED: {err}")
            return 1
        if errors:
            print(f"NOT SAVED (nothing was saved): {json.dumps(errors)}")
            return 1
    print(f"saved {len(good)} classes.")
    have = {re.sub(r"[^a-z0-9]", "", e.get("exam", "").lower()) for e in store.load()["lists"].get("exams", [])}
    exams = [e for e in result.get("exams", []) if re.sub(r"[^a-z0-9]", "", e["exam"].lower()) not in have]
    if exams:
        try:
            _, errors = store.list_add_many("exams", exams)
            print(f"saved {len(exams)} exam scores." if not errors else f"exam scores NOT saved: {json.dumps(errors)}")
        except store.ValidationError as err:
            print(f"exam scores NOT saved: {err}")
    if held:
        print(f"not saved, need the student's answer first: {', '.join(held)} (add with list-add courses ...)")
    print("GPA and rank are never saved from here: confirm them with the student, then use set.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
