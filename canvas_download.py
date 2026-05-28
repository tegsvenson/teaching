#!/usr/bin/env python3
"""Download Canvas course content for Fall 2026 courses."""

import os
import re
import json
import time
import requests

TOKEN = "13~QKr6ENkeWn3VDZvNEBNufrx83FZXJ7NyW6ELWneLH7eXhTPzVNhtRAvyUBwzkMGm"
BASE = "https://usflearn.instructure.com/api/v1"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
OUTPUT_DIR = "/opt/data/teaching/Fall2026"

COURSE_IDS = [2078322, 2078420, 2085274]


def sanitize(name):
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:120]


def get_all(url, params=None):
    """Fetch all paginated results. Returns list for list endpoints, dict for single-item."""
    p = dict(per_page=100)
    if params:
        p.update(params)
    r = requests.get(url, headers=HEADERS, params=p)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        return data  # single object
    results = list(data)
    while True:
        link = r.headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().strip("<>")
        if not next_url:
            break
        r = requests.get(next_url, headers=HEADERS)
        r.raise_for_status()
        results.extend(r.json())
    return results


def get_one(url, params=None):
    """Fetch a single object."""
    p = params or {}
    r = requests.get(url, headers=HEADERS, params=p)
    r.raise_for_status()
    return r.json()


def html_wrap(title, body):
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{title}</title></head>
<body>
<h1>{title}</h1>
{body or '<p>(no content)</p>'}
</body>
</html>"""


def save_rubric(course_id, assignment, assignment_dir):
    rubric = assignment.get("rubric")
    rubric_settings = assignment.get("rubric_settings", {})
    if not rubric:
        return
    rubric_data = {
        "title": rubric_settings.get("title", "Rubric"),
        "points_possible": rubric_settings.get("points_possible"),
        "criteria": rubric
    }
    path = os.path.join(assignment_dir, "rubric.json")
    with open(path, "w") as f:
        json.dump(rubric_data, f, indent=2)
    # Also save as HTML table
    rows = ""
    for c in rubric:
        desc = c.get("description", "")
        long_desc = c.get("long_description", "")
        pts = c.get("points", "")
        ratings_html = ""
        for r in c.get("ratings", []):
            ratings_html += f"<td>{r.get('description','')}<br><small>{r.get('long_description','')}</small><br><b>{r.get('points','')} pts</b></td>"
        rows += f"<tr><td><b>{desc}</b><br>{long_desc}<br><b>{pts} pts</b></td>{ratings_html}</tr>\n"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Rubric: {rubric_settings.get('title','')}</title></head>
<body>
<h2>Rubric: {rubric_settings.get('title','')}</h2>
<p>Total Points: {rubric_settings.get('points_possible','')}</p>
<table border="1" cellpadding="6" cellspacing="0">
<thead><tr><th>Criteria</th><th colspan="10">Ratings</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body></html>"""
    with open(os.path.join(assignment_dir, "rubric.html"), "w") as f:
        f.write(html)
    print(f"      Saved rubric")


def process_course(course_id):
    # Get course info
    course = get_one(f"{BASE}/courses/{course_id}")
    course_name = sanitize(course["name"])
    course_dir = os.path.join(OUTPUT_DIR, course_name)
    os.makedirs(course_dir, exist_ok=True)
    print(f"\n=== Course: {course_name} ===")

    # Save course info
    with open(os.path.join(course_dir, "course_info.json"), "w") as f:
        json.dump(course, f, indent=2)

    # Get modules
    modules = get_all(f"{BASE}/courses/{course_id}/modules")
    if not modules:
        print("  No modules found, saving pages/assignments flat")
        modules = [{"id": None, "name": "_unorganized", "position": 0}]

    # Build assignment map with rubrics
    assignments_raw = get_all(f"{BASE}/courses/{course_id}/assignments", {"include[]": "rubric"})
    assignment_map = {a["id"]: a for a in assignments_raw}

    # Build discussion map
    discussions_raw = get_all(f"{BASE}/courses/{course_id}/discussion_topics")
    discussion_map = {d["id"]: d for d in discussions_raw}

    for module in modules:
        mod_name = sanitize(module["name"])
        mod_dir = os.path.join(course_dir, mod_name)
        os.makedirs(mod_dir, exist_ok=True)
        print(f"  Module: {mod_name}")

        if module["id"] is None:
            # flat mode - just dump all pages
            pages = get_all(f"{BASE}/courses/{course_id}/pages")
            for page in pages:
                page_detail = get_one(f"{BASE}/courses/{course_id}/pages/{page['url']}")
                title = sanitize(page_detail.get("title", "page"))
                body = page_detail.get("body", "")
                fpath = os.path.join(mod_dir, f"{title}.html")
                with open(fpath, "w") as f:
                    f.write(html_wrap(title, body))
            continue

        # Get module items
        items = get_all(f"{BASE}/courses/{course_id}/modules/{module['id']}/items")

        for item in items:
            itype = item.get("type", "")
            title = sanitize(item.get("title", "item"))
            print(f"    [{itype}] {title}")

            if itype == "Page":
                page_url = item.get("page_url") or item.get("url", "").split("/pages/")[-1]
                if not page_url:
                    continue
                try:
                    page_detail = get_one(f"{BASE}/courses/{course_id}/pages/{page_url}")
                    body = page_detail.get("body", "")
                    fpath = os.path.join(mod_dir, f"{title}.html")
                    with open(fpath, "w") as f:
                        f.write(html_wrap(title, body))
                except Exception as e:
                    print(f"      ERROR: {e}")

            elif itype == "Assignment":
                content_id = item.get("content_id")
                a = assignment_map.get(content_id)
                if not a:
                    # fetch directly
                    try:
                        a = get_one(f"{BASE}/courses/{course_id}/assignments/{content_id}?include[]=rubric")
                    except:
                        continue
                body = a.get("description", "") or ""
                pts = a.get("points_possible", "")
                due = a.get("due_at", "")
                meta = f"<p><b>Points:</b> {pts} | <b>Due:</b> {due}</p>"
                full_body = meta + body
                adir = os.path.join(mod_dir, title)
                os.makedirs(adir, exist_ok=True)
                with open(os.path.join(adir, "assignment.html"), "w") as f:
                    f.write(html_wrap(title, full_body))
                save_rubric(course_id, a, adir)

            elif itype == "Discussion":
                content_id = item.get("content_id")
                d = discussion_map.get(content_id)
                if not d:
                    try:
                        d = get_one(f"{BASE}/courses/{course_id}/discussion_topics/{content_id}")
                    except:
                        continue
                body = d.get("message", "") or ""
                fpath = os.path.join(mod_dir, f"{title}.html")
                with open(fpath, "w") as f:
                    f.write(html_wrap(title, body))

            elif itype == "Quiz":
                content_id = item.get("content_id")
                try:
                    quiz = get_one(f"{BASE}/courses/{course_id}/quizzes/{content_id}")
                    body = quiz.get("description", "") or ""
                    fpath = os.path.join(mod_dir, f"{title}.html")
                    with open(fpath, "w") as f:
                        f.write(html_wrap(title, body))
                except Exception as e:
                    print(f"      Quiz error: {e}")

            elif itype == "ExternalUrl":
                url = item.get("external_url", "")
                fpath = os.path.join(mod_dir, f"{title}.html")
                with open(fpath, "w") as f:
                    f.write(html_wrap(title, f'<p><a href="{url}">{url}</a></p>'))

            elif itype == "File":
                # Save metadata only
                fpath = os.path.join(mod_dir, f"{title}_file_link.html")
                with open(fpath, "w") as f:
                    f.write(html_wrap(title, f'<p>File item: {json.dumps(item)}</p>'))

            time.sleep(0.05)

    print(f"  Done with {course_name}")


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for cid in COURSE_IDS:
        process_course(cid)
    print("\n\nAll courses downloaded!")
