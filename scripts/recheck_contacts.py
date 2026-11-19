"""Re-checks every contact currently chosen for a draft against the rubric's role rule (only the four preferred roles).
A company whose chosen contact is not eligible becomes NO_SUITABLE_CONTACT, and the draft written for that wrong person is removed from the
dashboard AND from Gmail Drafts. Uses stored Hunter data: no Hunter credit, no AI. Run:  .venv/bin/python scripts/recheck_contacts.py [--apply]"""
import os, sys, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db, settings as S, pipeline as P, research

apply = "--apply" in sys.argv
rows = db.q("""SELECT DISTINCT ON (c.domain) c.id cid, c.run_id, c.name, c.domain, ct.id ctid, ct.name person, ct.position, e.id eid, e.gmail_draft_id
               FROM companies c JOIN contacts ct ON ct.company_id=c.id AND ct.chosen JOIN emails e ON e.company_id=c.id AND e.is_current
               WHERE c.domain IS NOT NULL ORDER BY c.domain, c.id DESC""")
bad = [r for r in rows if P.classify_role(r["position"]) == "other"]
print(f"{len(rows)} drafted companies checked, {len(bad)} have a contact who is not one of the four rubric roles:")
for r in bad:
    print(f"  {r['name']}: {r['person']}, {r['position']}")
if apply and bad:
    token = requests.post("https://oauth2.googleapis.com/token", data={"client_id": S.env("GMAIL_OAUTH_CLIENT_ID"), "client_secret": S.env("GMAIL_OAUTH_CLIENT_SECRET"),
                          "refresh_token": S.env("GMAIL_OAUTH_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=30).json()["access_token"]
    for r in bad:
        if r["gmail_draft_id"]:
            code = requests.delete(f"https://gmail.googleapis.com/gmail/v1/users/me/drafts/{r['gmail_draft_id']}", headers={"Authorization": "Bearer " + token}, timeout=30).status_code
            print(f"  {r['name']}: Gmail draft removed (HTTP {code})")
        why = f"Hunter's best candidate was {r['person']} ({r['position']}), which is not one of the four roles the rubric prefers, so there is no suitable contact."
        db.x("UPDATE contacts SET chosen=false, passed=false WHERE id=%s", (r["ctid"],))
        db.x("UPDATE emails SET is_current=false, gmail_draft_id=NULL, gmail_message_id=NULL, review_decision='rejected', review_note='removed: contact not eligible under the role rule' WHERE id=%s", (r["eid"],))
        db.x("UPDATE companies SET status='NO_SUITABLE_CONTACT', reason=%s, escalation_reason=%s, updated_at=now() WHERE id=%s", (why, why + " " + P.finish_text({"status": "NO_SUITABLE_CONTACT"}), r["cid"]))
        research.log(r["run_id"], r["cid"], "contact_rechecked", {"removed": r["person"], "position": r["position"]}, "warn")
    print("applied.")
elif bad:
    print("(dry run: add --apply to correct them)")
