"""Research one company, then (if it qualifies) find the contact, write the email and put the draft in Gmail Drafts.
Usage:  .venv/bin/python run_one.py cal.com"""
import sys
import db, research, outreach

def main(domain):
    rid = research.start_run({"trigger": "run_one.py", "domain": domain})
    row = research.research_company(rid, domain)
    print(f"[1/2] research: {row['status']} | score {row['total_score']} | {row['seconds']}s | {row['reason']}")
    if row["status"] == "QUALIFIED":
        row = outreach.finish_company(rid, domain)
        print(f"[2/2] contact + email + Gmail draft: {row['status']} | total {row['seconds']}s | {row['reason']}")
    research.close_run_if_done(rid)
    return rid, row

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: run_one.py <domain>")
    main(sys.argv[1].strip().lower())
